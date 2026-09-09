from __future__ import annotations

import os
import hashlib
import math
import signal
import socket
import threading
import time
import uuid
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from drsm_core import AnalysisCancelled

from .app.config import settings
from .app.costs import reconcile_job_estimates, record_usage, seed_default_rates
from .app.database import SessionLocal, init_database
from .app.job_state import DatabaseJobStateStore
from .app.jobs import Job
from .app.models import Artifact, Asset, utc_now
from .app.pipeline import PipelineResult, run_pipeline
from .app.runtime import job_queue, media_storage, storage
from .app.transcription import (
    OpenAIWhisperProvider,
    TranscriptionCall,
)
from .app.transcription_checkpoint import CheckpointingTranscriptionProvider


ARTIFACTS = {
    "analysis": ("analysis_path", "analysis.json", "application/json"),
    "audio": ("audio_path", "audio-export.wav", "audio/wav"),
    "cover": ("cover_path", "cover.png", "image/png"),
    "video": ("video_path", "video.mp4", "video/mp4"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class Worker:
    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.state = DatabaseJobStateStore(settings.job_ttl_seconds)
        self.stop_requested = threading.Event()
        self.transcription_provider = None
        if settings.transcription_backend == "openai":
            self.transcription_provider = OpenAIWhisperProvider(
                api_key=settings.openai_api_key or "",
                model=settings.transcription_model,
                timeout_seconds=settings.openai_timeout_seconds,
            )

    def stop(self, *_args) -> None:
        self.stop_requested.set()

    def _control_state(self, job: Job) -> str:
        record = self.state.get(job.id)
        if record is None or record.get("worker_id") != self.worker_id:
            return "cancelled"
        state = str(record.get("state", "cancelled"))
        job.state = state
        return state

    def _save_progress(self, job: Job, event: dict) -> None:
        state = self._control_state(job)
        if state in {"cancelled", "cancelling"}:
            return
        job.stage = str(event["stage"])
        job.message = str(event["message"])
        if event.get("progress") is not None:
            job.progress = float(event["progress"])
        job.updated_at = time.time()
        job.lease_expires_at = time.time() + settings.worker_lease_seconds
        self.state.save(job.record())

    def _heartbeat(self, job: Job, stopped: threading.Event) -> None:
        interval = max(10, settings.worker_lease_seconds // 3)
        while not stopped.wait(interval):
            lease = time.time() + settings.worker_lease_seconds
            if not self.state.renew_lease(job.id, self.worker_id, settings.worker_lease_seconds):
                return
            job.lease_expires_at = lease

    def _wait_if_paused(self, job: Job) -> None:
        while self._control_state(job) == "paused":
            if self.stop_requested.wait(0.2):
                raise AnalysisCancelled("Worker stopping")
        if self._control_state(job) in {"cancelled", "cancelling"}:
            raise AnalysisCancelled("Job cancelled")

    def _store_artifacts(self, job: Job, result: PipelineResult) -> None:
        expiration = utc_now() + timedelta(seconds=settings.media_retention_seconds)
        rows: list[Artifact] = []
        object_keys: dict[str, str] = {}
        result_values = asdict(result)
        for kind, (path_field, filename, mime_type) in ARTIFACTS.items():
            source = Path(result_values[path_field])
            checksum = sha256_file(source)
            key = f"users/{job.user_id}/projects/{job.project_id}/jobs/{job.id}/{filename}"
            media_storage.upload_file(key, source, mime_type)
            object_keys[kind] = key
            rows.append(
                Artifact(
                    user_id=job.user_id,
                    project_id=job.project_id,
                    job_id=job.id,
                    kind=kind,
                    mime_type=mime_type,
                    size_bytes=source.stat().st_size,
                    storage_key=key,
                    checksum_sha256=checksum,
                    storage_metered_at=utc_now(),
                    expires_at=expiration,
                )
            )
        with SessionLocal() as db:
            previous = db.scalars(
                select(Artifact).where(
                    Artifact.job_id == job.id,
                    Artifact.kind.in_(ARTIFACTS),
                )
            ).all()
            for artifact in previous:
                db.delete(artifact)
            db.add_all(rows)
            db.commit()
        job.artifacts = object_keys

    def _record_transcription_call(self, job: Job, call: TranscriptionCall) -> None:
        with SessionLocal() as db:
            record_usage(
                db,
                user_id=job.user_id,
                project_id=job.project_id,
                job_id=job.id,
                provider=call.provider,
                service="transcription",
                model=call.model,
                quantity=math.ceil(call.duration_seconds),
                unit="audio_second",
                status="confirmed",
                idempotency_key=(
                    f"transcription:{job.id}:chunk:{call.chunk_index}:"
                    f"{call.checksum_sha256}"
                ),
                provider_request_id=call.request_id,
                details={
                    "chunk_index": call.chunk_index,
                    "chunk_count": call.chunk_count,
                    "checksum_sha256": call.checksum_sha256,
                    "checkpoint_reused": call.reused,
                },
            )
            reconcile_job_estimates(db, job.id, "transcription")
            db.commit()

    def process(self, job_id: str) -> bool:
        record = self.state.claim(job_id, self.worker_id, settings.worker_lease_seconds)
        if record is None:
            return False
        job = Job.from_record(record)
        job.worker_id = self.worker_id
        job.workspace = storage.workspace_path(job.user_id, job.id)
        if job.workspace.exists():
            storage.remove_workspace(job.workspace)
        storage.create_workspace(job.user_id, job.id)
        suffix = Path(job.input_path.name).suffix
        job.input_path = job.workspace / f"input{suffix}"

        heartbeat_stopped = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat, args=(job, heartbeat_stopped), daemon=True
        )
        heartbeat.start()
        try:
            with SessionLocal() as db:
                asset = db.scalar(
                    select(Asset).where(
                        Asset.id == job.source_asset_id,
                        Asset.user_id == job.user_id,
                        Asset.project_id == job.project_id,
                        Asset.status == "ready",
                    )
                )
                if asset is None or not asset.storage_key:
                    raise ValueError("Source asset is unavailable")
                source_key = asset.storage_key
            media_storage.download_file(source_key, job.input_path)
            source_checksum = sha256_file(job.input_path)
            with SessionLocal() as db:
                persisted_asset = db.get(Asset, job.source_asset_id)
                if persisted_asset is None:
                    raise ValueError("Source asset disappeared during download")
                if (
                    persisted_asset.checksum_sha256
                    and persisted_asset.checksum_sha256 != source_checksum
                ):
                    persisted_asset.status = "corrupt"
                    db.commit()
                    raise ValueError("Source asset checksum mismatch")
                persisted_asset.checksum_sha256 = source_checksum
                db.commit()
            if self._control_state(job) in {"cancelled", "cancelling"}:
                raise AnalysisCancelled("Job cancelled")

            transcription_calls = 0
            transcription_checkpoint_hits = 0

            def record_transcription(call: TranscriptionCall) -> None:
                nonlocal transcription_calls, transcription_checkpoint_hits
                self._record_transcription_call(job, call)
                if call.reused:
                    transcription_checkpoint_hits += 1
                else:
                    transcription_calls += 1

            provider = (
                CheckpointingTranscriptionProvider(
                    self.transcription_provider,
                    job,
                    storage=media_storage,
                    session_factory=SessionLocal,
                    retention_seconds=settings.media_retention_seconds,
                )
                if self.transcription_provider
                else None
            )
            result = run_pipeline(
                job.input_path,
                job.workspace,
                model_name=job.model_name,
                language=job.language,
                cpu_threads=job.cpu_threads,
                progress=lambda event: self._save_progress(job, event),
                should_pause=lambda: self._control_state(job) == "paused",
                should_cancel=lambda: self._control_state(job)
                in {"cancelled", "cancelling"},
                transcription_provider=provider,
                transcription_chunk_seconds=settings.transcription_chunk_seconds,
                transcription_chunk_max_bytes=settings.transcription_chunk_max_bytes,
                on_transcription_usage=(
                    record_transcription if self.transcription_provider else None
                ),
            )
            self._wait_if_paused(job)
            self._store_artifacts(job, result)
            self._wait_if_paused(job)
            job.state = "completed"
            job.stage = "done"
            job.message = "Pipeline completed"
            job.progress = 1.0
            job.metrics = {
                "segments": result.segment_count,
                "parts": result.part_count,
                "duration_seconds": result.duration_seconds,
                "elapsed_seconds": result.elapsed_seconds,
                "transcription_calls": transcription_calls,
                "transcription_checkpoint_hits": transcription_checkpoint_hits,
            }
            job.error = None
        except AnalysisCancelled:
            job.state = "cancelled"
            job.message = "Job cancelled"
        except Exception as exc:
            job.state = "failed"
            job.stage = "failed"
            job.message = "Pipeline failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            heartbeat_stopped.set()
            heartbeat.join(timeout=2)
            job.worker_id = None
            job.lease_expires_at = None
            job.updated_at = time.time()
            self.state.save(job.record())
            storage.remove_workspace(job.workspace)
        return True

    def run(self) -> None:
        init_database()
        with SessionLocal() as db:
            seed_default_rates(db)
        while not self.stop_requested.is_set():
            for job_id in self.state.recover_expired_leases(settings.worker_max_attempts):
                try:
                    job_queue.enqueue(job_id)
                except Exception:
                    pass
            job_id = self.state.next_queued_id()
            if job_id is None:
                try:
                    job_id = job_queue.wait(settings.worker_poll_seconds)
                except Exception:
                    self.stop_requested.wait(settings.worker_poll_seconds)
                    continue
            if job_id:
                self.process(job_id)


def main() -> None:
    worker = Worker()
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
