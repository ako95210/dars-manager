from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import signal
import socket
import threading
import time
import uuid
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from drsm_core import AnalysisCancelled, TranscriptSegment, audio_duration, export_clips

from .app.archive_format import ALLOWED_FILES, InvalidArchive, build_archive, extract_archive, validate_analysis_duration
from .app.config import settings
from .app.costs import reconcile_job_estimates, record_usage, seed_default_rates
from .app.editor import queue_auto_archive
from .app.database import SessionLocal, init_database
from .app.job_state import DatabaseJobStateStore
from .app.jobs import Job
from .app.impact import record_impact
from .app.image_generation import ImageGenerationCall, OpenAIReferenceImageGenerator
from .app.media_lifecycle import meter_media
from .app.models import Artifact, Asset, BrandTemplateFile, utc_now
from .app.observability import configure_logging
from .app.pipeline import PipelineResult, render_static_video, run_pipeline
from .app.rendering import compose_cover, remap_subtitles, render_animated_video, subtitle_cue_indices
from .app.semantic_analysis import OpenAISemanticAnalyzer, SemanticAnalysisCall
from .app.runtime import job_queue, manager, media_storage, storage
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
logger = logging.getLogger("dars.worker")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class Worker:
    def _checkpoint_completed(self, job: Job) -> None:
        if job.state != "completed" or job.tool not in {"audio_pipeline", "semantic_reanalysis", "audio_selection", "video_render"}:
            return
        source = job if job.tool == "audio_pipeline" else manager.get(job.user_id, str(job.options.get("source_job_id", "")))
        if source is None:
            return
        try:
            with SessionLocal() as db:
                queue_auto_archive(db, source, audio_job_id=job.id if job.tool == "audio_selection" else None, video_job_id=job.id if job.tool == "video_render" else None)
        except Exception:
            logging.exception("Automatic archive checkpoint failed for %s", job.id)

    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.state = DatabaseJobStateStore(settings.job_ttl_seconds)
        self.stop_requested = threading.Event()
        self.transcription_provider = None
        self.semantic_analyzer = None
        self.image_generator = None
        if (
            settings.transcription_backend == "openai"
            or settings.semantic_analysis_backend == "openai"
        ) and not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY or OPENAI_API_KEY_FILE is required by the worker"
            )
        if settings.openai_api_key:
            self.transcription_provider = OpenAIWhisperProvider(
                api_key=settings.openai_api_key,
                model=settings.transcription_model,
                timeout_seconds=settings.openai_timeout_seconds,
            )
            self.semantic_analyzer = OpenAISemanticAnalyzer(
                api_key=settings.openai_api_key,
                model=settings.semantic_analysis_model,
                timeout_seconds=settings.openai_timeout_seconds,
            )
            self.image_generator = OpenAIReferenceImageGenerator(
                api_key=settings.openai_api_key,
                model=settings.image_generation_model,
                quality=settings.image_generation_quality,
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

    def _record_semantic_analysis_call(
        self,
        job: Job,
        call: SemanticAnalysisCall,
        purpose: str = "semantic_chapters_and_titles",
    ) -> None:
        with SessionLocal() as db:
            request_key = call.request_id or uuid.uuid4().hex
            for quantity, unit in (
                (call.input_tokens, "input_token"),
                (call.output_tokens, "output_token"),
            ):
                if quantity <= 0:
                    continue
                record_usage(
                    db,
                    user_id=job.user_id,
                    project_id=job.project_id,
                    job_id=job.id,
                    provider=call.provider,
                    service="content_analysis",
                    model=call.model,
                    quantity=quantity,
                    unit=unit,
                    status="confirmed",
                    idempotency_key=(
                        f"content-analysis:{job.id}:{request_key}:{unit}"
                    ),
                    provider_request_id=call.request_id,
                    details={"purpose": purpose},
                )
            reconcile_job_estimates(db, job.id, "content_analysis")
            db.commit()

    def _process_subtitle_proofread(self, job: Job, heartbeat_stopped: threading.Event, heartbeat: threading.Thread) -> bool:
        try:
            if self.semantic_analyzer is None:
                raise ValueError("Subtitle proofreader is unavailable")
            key = str(job.options.get("analysis_storage_key", ""))
            checksum = str(job.options.get("analysis_checksum", ""))
            analysis_path = job.workspace / "analysis.json"
            media_storage.download_file(key, analysis_path)
            if sha256_file(analysis_path) != checksum:
                raise ValueError("Source analysis changed before proofreading")
            payload = json.loads(analysis_path.read_text(encoding="utf-8"))
            track_id = str(job.options.get("subtitle_track_id", ""))
            audio_export_job_id = str(job.options.get("audio_export_job_id", ""))
            subtitles = next(
                (
                    item
                    for item in payload.get("subtitle_tracks", [])
                    if isinstance(item, dict)
                    and item.get("id") == track_id
                    and item.get("audio_export_job_id") == audio_export_job_id
                ),
                None,
            )
            if subtitles is None:
                raise ValueError("Subtitle track no longer exists")
            cues = subtitles.get("cues", [])
            if not isinstance(cues, list) or not cues:
                raise ValueError("No subtitles to proofread")
            raw_ranges = job.options.get("ranges", [])
            if (
                not isinstance(raw_ranges, list)
                or not raw_ranges
                or any(not isinstance(item, list) or len(item) != 2 for item in raw_ranges)
            ):
                raise ValueError("Invalid subtitle selection")
            ranges = [
                (float(item[0]), float(item[1]))
                for item in raw_ranges
            ]
            if any(start < 0 or end <= start for start, end in ranges):
                raise ValueError("Invalid subtitle selection ranges")
            selected_indices = subtitle_cue_indices(subtitles, ranges)
            if not selected_indices:
                raise ValueError("The selected audio has no subtitles")
            corrected_by_index: dict[int, str] = {}
            for offset in range(0, len(selected_indices), 20):
                self._wait_if_paused(job)
                if self._control_state(job) in {"cancelled", "cancelling"}:
                    raise AnalysisCancelled("Job cancelled")
                chunk_indices = selected_indices[offset:offset + 20]
                chunk = [cues[index] for index in chunk_indices]
                texts, call = self.semantic_analyzer.proofread_subtitles([str(item["text"]) for item in chunk], str(subtitles.get("language") or job.language))
                self._record_semantic_analysis_call(job, call, "subtitle_proofread")
                corrected_by_index.update(zip(chunk_indices, texts))
                self._save_progress(job, {"stage": "subtitle_proofread", "message": "Correction des sous-titres", "progress": min(0.9, (offset + len(chunk)) / len(selected_indices) * 0.9)})
            corrected_cues = [
                {**cue, "text": corrected_by_index.get(index, str(cue["text"]))}
                for index, cue in enumerate(cues)
            ]
            output_path = job.workspace / "subtitle-suggestions.json"
            output_path.write_text(json.dumps({"analysis_checksum": checksum, "cues": corrected_cues}, ensure_ascii=False), encoding="utf-8")
            output_key = f"users/{job.user_id}/projects/{job.project_id}/jobs/{job.id}/subtitle-suggestions.json"
            media_storage.upload_file(output_key, output_path, "application/json")
            with SessionLocal() as db:
                db.add(Artifact(user_id=job.user_id, project_id=job.project_id, job_id=job.id, kind="subtitle_suggestions", mime_type="application/json", size_bytes=output_path.stat().st_size, storage_key=output_key, checksum_sha256=sha256_file(output_path), storage_metered_at=utc_now(), expires_at=utc_now() + timedelta(seconds=settings.media_retention_seconds)))
                db.commit()
            job.artifacts = {"subtitle_suggestions": output_key}
            job.state, job.stage, job.message, job.progress, job.error = "completed", "done", "Suggestions prêtes", 1.0, None
        except AnalysisCancelled:
            job.state, job.message = "cancelled", "Correction annulée"
        except Exception as exc:
            job.state, job.stage, job.message, job.error = "failed", "failed", "Échec de la correction", f"{type(exc).__name__}: {exc}"
        finally:
            heartbeat_stopped.set()
            heartbeat.join(timeout=2)
            job.worker_id = None
            job.lease_expires_at = None
            job.updated_at = time.time()
            self.state.save(job.record())
            storage.remove_workspace(job.workspace)
        return True

    def _record_image_generation_call(
        self,
        job: Job,
        call: ImageGenerationCall,
    ) -> None:
        with SessionLocal() as db:
            request_key = call.request_id or uuid.uuid4().hex
            for quantity, unit in (
                (call.input_text_tokens, "input_text_token"),
                (call.input_image_tokens, "input_image_token"),
                (call.output_image_tokens, "output_image_token"),
            ):
                if quantity <= 0:
                    continue
                record_usage(
                    db,
                    user_id=job.user_id,
                    project_id=job.project_id,
                    job_id=job.id,
                    provider=call.provider,
                    service="image_generation",
                    model=call.model,
                    quantity=quantity,
                    unit=unit,
                    status="confirmed",
                    idempotency_key=f"image-generation:{job.id}:{request_key}:{unit}",
                    provider_request_id=call.request_id,
                    details={"purpose": "course_visual_from_reference"},
                )
            reconcile_job_estimates(db, job.id, "image_generation")
            db.commit()

    def _record_impact_event(
        self,
        job: Job,
        kind: str,
        *,
        duration_seconds: float,
        storage_bytes: int,
        details: dict | None = None,
    ) -> None:
        with SessionLocal() as db:
            record_impact(
                db,
                user_id=job.user_id,
                project_id=job.project_id,
                job_id=job.id,
                kind=kind,
                duration_seconds=duration_seconds,
                storage_bytes=storage_bytes,
                idempotency_key=f"impact:{kind}:{job.id}",
                details=details,
            )
            db.commit()

    def _process_semantic_reanalysis(
        self,
        job: Job,
        heartbeat_stopped: threading.Event,
        heartbeat: threading.Thread,
    ) -> bool:
        started = time.monotonic()
        try:
            if self.semantic_analyzer is None:
                raise ValueError("Semantic analysis provider is unavailable")
            source_job_id = str(job.options.get("source_job_id", ""))
            storage_key = str(job.options.get("analysis_storage_key", ""))
            expected_checksum = str(job.options.get("analysis_checksum", ""))
            if not source_job_id or not storage_key or len(expected_checksum) != 64:
                raise ValueError("Invalid semantic reanalysis job")

            self._save_progress(
                job,
                {
                    "stage": "semantic_analysis",
                    "message": "Lecture de la transcription existante",
                    "progress": 0.1,
                },
            )
            analysis_path = job.workspace / "analysis.json"
            media_storage.download_file(storage_key, analysis_path)
            if sha256_file(analysis_path) != expected_checksum:
                raise ValueError("Source analysis checksum mismatch")
            payload = json.loads(analysis_path.read_text(encoding="utf-8"))
            raw_segments = payload.get("segments") if isinstance(payload, dict) else None
            if not isinstance(raw_segments, list) or not raw_segments:
                raise ValueError("Source analysis has no transcript segments")
            segments = [
                TranscriptSegment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=str(item["text"]).strip(),
                )
                for item in raw_segments
                if isinstance(item, dict)
                and isinstance(item.get("start"), (int, float))
                and isinstance(item.get("end"), (int, float))
                and str(item.get("text", "")).strip()
            ]
            if len(segments) != len(raw_segments):
                raise ValueError("Source transcript segments are invalid")

            self._wait_if_paused(job)
            self._save_progress(
                job,
                {
                    "stage": "semantic_analysis",
                    "message": "Détection des sous-sujets et rédaction des titres",
                    "progress": 0.35,
                },
            )
            result = self.semantic_analyzer.analyze(segments, job.language)
            self._record_semantic_analysis_call(job, result.call)
            self._wait_if_paused(job)
            payload["parts"] = [asdict(part) for part in result.parts]
            payload["semantic_analysis"] = {
                "provider": result.call.provider,
                "model": result.call.model,
                "updated_at": utc_now().isoformat(timespec="seconds"),
            }
            analysis_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            updated_checksum = sha256_file(analysis_path)

            self._save_progress(
                job,
                {
                    "stage": "semantic_analysis",
                    "message": "Enregistrement du nouveau chapitrage",
                    "progress": 0.8,
                },
            )
            with SessionLocal() as db:
                artifact = db.scalar(
                    select(Artifact)
                    .where(
                        Artifact.job_id == source_job_id,
                        Artifact.user_id == job.user_id,
                        Artifact.project_id == job.project_id,
                        Artifact.kind == "analysis",
                    )
                    .with_for_update()
                )
                if artifact is None or artifact.storage_key != storage_key:
                    raise ValueError("Source analysis artifact is unavailable")
                if artifact.checksum_sha256 and artifact.checksum_sha256 != expected_checksum:
                    raise ValueError("Source analysis changed during semantic analysis")
                meter_media(db, artifact)
                media_storage.upload_file(storage_key, analysis_path, "application/json")
                artifact.size_bytes = analysis_path.stat().st_size
                artifact.checksum_sha256 = updated_checksum
                artifact.storage_metered_at = utc_now()
                db.commit()

            source_record = self.state.get(source_job_id)
            if source_record and source_record.get("user_id") == job.user_id:
                metrics = dict(source_record.get("metrics", {}))
                metrics["parts"] = len(result.parts)
                metrics["semantic_model"] = result.call.model
                source_record["metrics"] = metrics
                source_record["updated_at"] = time.time()
                self.state.save(source_record)

            job.state = "completed"
            job.stage = "done"
            job.message = "Chapitrage sémantique terminé"
            job.progress = 1.0
            job.error = None
            job.metrics = {
                "parts": len(result.parts),
                "semantic_input_tokens": result.call.input_tokens,
                "semantic_output_tokens": result.call.output_tokens,
                "elapsed_seconds": time.monotonic() - started,
            }
        except AnalysisCancelled:
            job.state = "cancelled"
            job.message = "Réanalyse annulée"
        except Exception as exc:
            job.state = "failed"
            job.stage = "failed"
            job.message = "Échec de la réanalyse sémantique"
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

    def _process_audio_selection(
        self,
        job: Job,
        heartbeat_stopped: threading.Event,
        heartbeat: threading.Thread,
    ) -> bool:
        started = time.monotonic()
        try:
            source_job_id = str(job.options.get("source_job_id", ""))
            raw_ranges = job.options.get("ranges", [])
            ranges = [
                (float(item[0]), float(item[1]))
                for item in raw_ranges
                if isinstance(item, (list, tuple)) and len(item) == 2
            ]
            if not source_job_id or not ranges or len(ranges) != len(raw_ranges):
                raise ValueError("Invalid audio selection job")
            if any(end <= start or start < 0 for start, end in ranges):
                raise ValueError("Invalid audio selection ranges")

            self._save_progress(
                job,
                {
                    "stage": "preparing_export",
                    "message": "Préparation des passages sélectionnés",
                    "progress": 0.1,
                },
            )
            with SessionLocal() as db:
                source = db.scalar(
                    select(Artifact).where(
                        Artifact.job_id == source_job_id,
                        Artifact.user_id == job.user_id,
                        Artifact.project_id == job.project_id,
                        Artifact.kind == "audio",
                    )
                )
                if source is None or not source.storage_key:
                    raise ValueError("Source audio artifact is unavailable")
                source_key = source.storage_key
                expected_checksum = source.checksum_sha256

            input_path = job.workspace / "source-audio.wav"
            media_storage.download_file(source_key, input_path)
            if expected_checksum and sha256_file(input_path) != expected_checksum:
                raise ValueError("Source audio artifact checksum mismatch")
            self._wait_if_paused(job)
            self._save_progress(
                job,
                {
                    "stage": "audio_export",
                    "message": "Assemblage de la sélection audio",
                    "progress": 0.35,
                },
            )
            output_path = job.workspace / "selection-audio.wav"
            export_clips(input_path, output_path, ranges)
            self._wait_if_paused(job)

            key = (
                f"users/{job.user_id}/projects/{job.project_id}/jobs/"
                f"{job.id}/selection-audio.wav"
            )
            with SessionLocal() as db:
                previous = db.scalars(
                    select(Artifact).where(
                        Artifact.job_id == job.id,
                        Artifact.kind == "selection_audio",
                    )
                ).all()
                for artifact in previous:
                    meter_media(db, artifact)
                db.commit()
            media_storage.upload_file(key, output_path, "audio/wav")
            artifact = Artifact(
                user_id=job.user_id,
                project_id=job.project_id,
                job_id=job.id,
                kind="selection_audio",
                mime_type="audio/wav",
                size_bytes=output_path.stat().st_size,
                storage_key=key,
                checksum_sha256=sha256_file(output_path),
                storage_metered_at=utc_now(),
                expires_at=utc_now() + timedelta(seconds=settings.media_retention_seconds),
            )
            with SessionLocal() as db:
                previous = db.scalars(
                    select(Artifact).where(
                        Artifact.job_id == job.id,
                        Artifact.kind == "selection_audio",
                    )
                ).all()
                for item in previous:
                    db.delete(item)
                db.add(artifact)
                db.commit()

            job.artifacts = {"selection_audio": key}
            job.metrics = {
                "selected_parts": len(ranges),
                "duration_seconds": sum(end - start for start, end in ranges),
                "elapsed_seconds": time.monotonic() - started,
            }
            job.state = "completed"
            job.stage = "done"
            job.message = "Sélection audio prête"
            job.progress = 1.0
            job.error = None
        except AnalysisCancelled:
            job.state = "cancelled"
            job.message = "Export annulé"
        except Exception as exc:
            job.state = "failed"
            job.stage = "failed"
            job.message = "Échec de l'export audio"
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

    def _process_image_generation(
        self,
        job: Job,
        heartbeat_stopped: threading.Event,
        heartbeat: threading.Thread,
    ) -> bool:
        started = time.monotonic()
        try:
            if self.image_generator is None:
                raise RuntimeError("La génération d'image OpenAI n'est pas configurée.")
            reference_key = str(job.options.get("reference_storage_key", ""))
            output_format = str(job.options.get("output_format", ""))
            title = str(job.options.get("title", "")).strip()
            instruction = str(job.options.get("prompt", "")).strip()
            if not reference_key or output_format not in {"16:9", "1:1", "9:16"} or not title:
                raise ValueError("Invalid image generation job")
            self._save_progress(job, {
                "stage": "preparing_image",
                "message": "Préparation du modèle visuel",
                "progress": 0.12,
            })
            reference_path = job.workspace / "reference.png"
            media_storage.download_file(reference_key, reference_path)
            reference_checksum = job.options.get("reference_checksum")
            if reference_checksum and sha256_file(reference_path) != reference_checksum:
                raise ValueError("Reference image checksum mismatch")
            self._wait_if_paused(job)
            output_path = job.workspace / "generated-image.png"
            sizes = {
                "16:9": "1536x1024",
                "1:1": "1024x1024",
                "9:16": "1024x1536",
            }
            prompt = (
                "Create a new polished background image for an educational audio video. "
                "Use the attached image strictly as the visual identity reference: preserve its "
                "overall art direction, colors, mood and composition language, while creating a "
                "new visual relevant to the course topic. Do not include any words, letters, "
                "logos, captions or typography; the title will be added later by the application. "
                f"Course topic: {title}."
            )
            if instruction:
                prompt += f" Additional creative direction: {instruction}."
            self._save_progress(job, {
                "stage": "image_generation",
                "message": "Génération de l'image avec l'IA",
                "progress": 0.28,
            })
            call = self.image_generator.generate(
                reference_path,
                output_path,
                prompt=prompt,
                size=sizes[output_format],
            )
            self._record_image_generation_call(job, call)
            self._wait_if_paused(job)
            expiration = utc_now() + timedelta(seconds=settings.media_retention_seconds)
            key = (
                f"users/{job.user_id}/projects/{job.project_id}/jobs/"
                f"{job.id}/generated-image.png"
            )
            media_storage.upload_file(key, output_path, "image/png")
            row = Artifact(
                user_id=job.user_id,
                project_id=job.project_id,
                job_id=job.id,
                kind="generated_image",
                mime_type="image/png",
                size_bytes=output_path.stat().st_size,
                storage_key=key,
                checksum_sha256=sha256_file(output_path),
                storage_metered_at=utc_now(),
                expires_at=expiration,
            )
            with SessionLocal() as db:
                previous = db.scalars(
                    select(Artifact).where(
                        Artifact.job_id == job.id,
                        Artifact.kind == "generated_image",
                    )
                ).all()
                for item in previous:
                    meter_media(db, item)
                    db.delete(item)
                db.add(row)
                db.commit()
            job.artifacts = {"generated_image": key}
            job.metrics = {
                "elapsed_seconds": time.monotonic() - started,
                "model": call.model,
                "input_text_tokens": call.input_text_tokens,
                "input_image_tokens": call.input_image_tokens,
                "output_image_tokens": call.output_image_tokens,
                "output_format": output_format,
            }
            job.state = "completed"
            job.stage = "done"
            job.message = "Image prête"
            job.progress = 1.0
            job.error = None
        except AnalysisCancelled:
            job.state = "cancelled"
            job.message = "Génération de l'image annulée"
        except Exception as exc:
            job.state = "failed"
            job.stage = "failed"
            job.message = "Échec de la génération de l'image"
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

    def _process_video_render(
        self,
        job: Job,
        heartbeat_stopped: threading.Event,
        heartbeat: threading.Thread,
    ) -> bool:
        started = time.monotonic()
        try:
            source_job_id = str(job.options.get("source_job_id", ""))
            template_id = str(job.options.get("template_id", ""))
            raw_ranges = job.options.get("ranges", [])
            ranges = [(float(item[0]), float(item[1])) for item in raw_ranges]
            output_format = str(job.options.get("output_format", ""))
            if not source_job_id or not template_id or not ranges:
                raise ValueError("Invalid video render job")
            self._save_progress(job, {
                "stage": "preparing_render",
                "message": "Préparation du template et de l'audio",
                "progress": 0.08,
            })
            with SessionLocal() as db:
                source_audio = db.scalar(
                    select(Artifact).where(
                        Artifact.job_id == source_job_id,
                        Artifact.user_id == job.user_id,
                        Artifact.kind == "audio",
                    )
                )
                image_job_id = job.options.get("image_job_id")
                if image_job_id:
                    template_source = db.scalar(
                        select(Artifact).where(
                            Artifact.job_id == image_job_id,
                            Artifact.user_id == job.user_id,
                            Artifact.kind == "generated_image",
                        )
                    )
                else:
                    template_source = db.scalar(
                        select(BrandTemplateFile).where(
                            BrandTemplateFile.template_id == template_id,
                            BrandTemplateFile.user_id == job.user_id,
                            BrandTemplateFile.kind == "source",
                        )
                    )
                if source_audio is None or not source_audio.storage_key or template_source is None:
                    raise ValueError("Render source is unavailable")
                audio_key = source_audio.storage_key
                audio_checksum = source_audio.checksum_sha256
                template_key = template_source.storage_key
                template_checksum = template_source.checksum_sha256

            audio_path = job.workspace / "source-audio.wav"
            template_path = job.workspace / f"template{Path(template_key).suffix}"
            media_storage.download_file(audio_key, audio_path)
            media_storage.download_file(template_key, template_path)
            if audio_checksum and sha256_file(audio_path) != audio_checksum:
                raise ValueError("Source audio artifact checksum mismatch")
            if template_checksum and sha256_file(template_path) != template_checksum:
                raise ValueError("Template checksum mismatch")
            self._wait_if_paused(job)

            selection_path = job.workspace / "selection-audio.wav"
            self._save_progress(job, {
                "stage": "audio_export",
                "message": "Assemblage des passages sélectionnés",
                "progress": 0.2,
            })
            export_clips(audio_path, selection_path, ranges)
            subtitles = remap_subtitles(job.options.get("subtitles"), ranges)
            cover_path = job.workspace / "cover.png"
            self._save_progress(job, {
                "stage": "cover_render",
                "message": "Composition de la couverture",
                "progress": 0.38,
            })
            compose_cover(
                template_path,
                cover_path,
                source_kind=str(job.options.get("template_source_kind", "image")),
                frame_seconds=float(job.options.get("template_frame_seconds", 0)),
                output_format=output_format,
                zones=list(job.options.get("template_zones", [])),
                values=dict(job.options.get("values", {})),
            )
            self._wait_if_paused(job)
            video_path = job.workspace / "video.mp4"
            self._save_progress(job, {
                "stage": "video_render",
                "message": "Encodage de la vidéo finale",
                "progress": 0.55,
            })
            if job.options.get("template_usage_mode") == "animated":
                render_animated_video(
                    template_path,
                    selection_path,
                    video_path,
                    output_format=output_format,
                    zones=list(job.options.get("template_zones", [])),
                    values=dict(job.options.get("values", {})),
                    subtitles=subtitles,
                )
            else:
                render_static_video(cover_path, selection_path, video_path, subtitles=subtitles)
            self._wait_if_paused(job)

            produced = {
                "selection_audio": (selection_path, "selection-audio.wav", "audio/wav"),
                "cover": (cover_path, "cover.png", "image/png"),
                "video": (video_path, "video.mp4", "video/mp4"),
            }
            expiration = utc_now() + timedelta(seconds=settings.media_retention_seconds)
            rows = []
            keys = {}
            for kind, (path, filename, mime_type) in produced.items():
                key = f"users/{job.user_id}/projects/{job.project_id}/jobs/{job.id}/{filename}"
                media_storage.upload_file(key, path, mime_type)
                keys[kind] = key
                rows.append(Artifact(
                    user_id=job.user_id,
                    project_id=job.project_id,
                    job_id=job.id,
                    kind=kind,
                    mime_type=mime_type,
                    size_bytes=path.stat().st_size,
                    storage_key=key,
                    checksum_sha256=sha256_file(path),
                    storage_metered_at=utc_now(),
                    expires_at=expiration,
                ))
            with SessionLocal() as db:
                previous = db.scalars(select(Artifact).where(Artifact.job_id == job.id)).all()
                for item in previous:
                    meter_media(db, item)
                    db.delete(item)
                db.add_all(rows)
                db.commit()
            rendered_duration = sum(end - start for start, end in ranges)
            self._record_impact_event(
                job,
                "video_rendered",
                duration_seconds=rendered_duration,
                storage_bytes=sum(path.stat().st_size for path, _, _ in produced.values()),
                details={
                    "template_id": template_id,
                    "template_version": int(job.options.get("template_version", 1)),
                    "output_format": output_format,
                },
            )
            job.artifacts = keys
            job.metrics = {
                "selected_parts": len(ranges),
                "duration_seconds": rendered_duration,
                "elapsed_seconds": time.monotonic() - started,
                "template_id": template_id,
                "template_version": int(job.options.get("template_version", 1)),
                "output_format": output_format,
            }
            job.state = "completed"
            job.stage = "done"
            job.message = "Vidéo prête"
            job.progress = 1.0
            job.error = None
        except AnalysisCancelled:
            job.state = "cancelled"
            job.message = "Rendu vidéo annulé"
        except Exception as exc:
            job.state = "failed"
            job.stage = "failed"
            job.message = "Échec du rendu vidéo"
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

    def _process_archive_export(
        self,
        job: Job,
        heartbeat_stopped: threading.Event,
        heartbeat: threading.Thread,
    ) -> bool:
        started = time.monotonic()
        try:
            raw_references = job.options.get("archive_files")
            if not isinstance(raw_references, dict):
                raise ValueError("Invalid archive export job")
            if "analysis" not in raw_references or "audio" not in raw_references:
                raise ValueError("Archive sources are incomplete")
            self._save_progress(job, {
                "stage": "archive_collect",
                "message": "Collecte des fichiers du cours",
                "progress": 0.12,
            })

            files: dict[str, Path] = {}
            for logical_kind, reference in raw_references.items():
                if logical_kind not in ALLOWED_FILES or not isinstance(reference, dict):
                    raise ValueError("Invalid archive source reference")
                source_job_id = str(reference.get("job_id", ""))
                artifact_kind = str(reference.get("artifact_kind", ""))
                with SessionLocal() as db:
                    artifact = db.scalar(
                        select(Artifact).where(
                            Artifact.job_id == source_job_id,
                            Artifact.user_id == job.user_id,
                            Artifact.project_id == job.project_id,
                            Artifact.kind == artifact_kind,
                        )
                    )
                    if artifact is None or not artifact.storage_key:
                        raise ValueError("Archive source artifact is unavailable")
                    source_key = artifact.storage_key
                    stored_checksum = artifact.checksum_sha256
                expected_checksum = reference.get("checksum_sha256")
                if expected_checksum and stored_checksum != expected_checksum:
                    raise ValueError("Archive source changed after reservation")
                target = job.workspace / ALLOWED_FILES[logical_kind][0]
                media_storage.download_file(source_key, target)
                checksum = sha256_file(target)
                if stored_checksum and checksum != stored_checksum:
                    raise ValueError("Archive source artifact checksum mismatch")
                files[logical_kind] = target
                self._wait_if_paused(job)

            self._save_progress(job, {
                "stage": "archive_build",
                "message": "Création de l'archive portable",
                "progress": 0.68,
            })
            output_path = job.workspace / "cours.dars"
            manifest = build_archive(
                output_path,
                files,
                project_title=str(job.options.get("project_title", "Cours")),
                source_job_id=str(job.options.get("source_job_id", "")),
                analysis_checksum=str(job.options.get("analysis_checksum", "")),
                render_snapshot=job.options.get("render"),
            )
            self._wait_if_paused(job)
            key = f"users/{job.user_id}/projects/{job.project_id}/jobs/{job.id}/cours.dars"
            media_storage.upload_file(key, output_path, "application/vnd.dars-manager.archive")
            row = Artifact(
                user_id=job.user_id,
                project_id=job.project_id,
                job_id=job.id,
                kind="archive",
                mime_type="application/vnd.dars-manager.archive",
                size_bytes=output_path.stat().st_size,
                storage_key=key,
                checksum_sha256=sha256_file(output_path),
                storage_metered_at=utc_now(),
                expires_at=utc_now() + timedelta(seconds=settings.media_retention_seconds),
            )
            with SessionLocal() as db:
                previous = db.scalars(
                    select(Artifact).where(Artifact.job_id == job.id, Artifact.kind == "archive")
                ).all()
                for item in previous:
                    meter_media(db, item)
                    db.delete(item)
                db.add(row)
                db.commit()
            job.artifacts = {"archive": key}
            job.metrics = {
                "archive_files": len(manifest["files"]),
                "size_bytes": output_path.stat().st_size,
                "elapsed_seconds": time.monotonic() - started,
            }
            job.state = "completed"
            job.stage = "done"
            job.message = "Archive .dars prête"
            job.progress = 1.0
            job.error = None
        except AnalysisCancelled:
            job.state = "cancelled"
            job.message = "Archivage annulé"
        except Exception as exc:
            job.state = "failed"
            job.stage = "failed"
            job.message = "Échec de la création de l'archive"
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

    def _process_archive_import(
        self,
        job: Job,
        heartbeat_stopped: threading.Event,
        heartbeat: threading.Thread,
    ) -> bool:
        started = time.monotonic()
        try:
            self._save_progress(job, {
                "stage": "archive_verify",
                "message": "Vérification de l'archive .dars",
                "progress": 0.12,
            })
            with SessionLocal() as db:
                asset = db.scalar(
                    select(Asset).where(
                        Asset.id == job.source_asset_id,
                        Asset.user_id == job.user_id,
                        Asset.project_id == job.project_id,
                        Asset.kind == "dars_archive",
                        Asset.status == "ready",
                    )
                )
                if asset is None or not asset.storage_key:
                    raise ValueError("Archive asset is unavailable")
                source_key = asset.storage_key
                expected_checksum = asset.checksum_sha256

            archive_path = job.workspace / "source.dars"
            media_storage.download_file(source_key, archive_path)
            if expected_checksum and sha256_file(archive_path) != expected_checksum:
                raise InvalidArchive("L'empreinte de l'archive envoyée est incorrecte.")
            self._wait_if_paused(job)
            extracted_root = job.workspace / "extracted"
            manifest, extracted = extract_archive(archive_path, extracted_root)
            analysis_path = extracted["analysis"]
            try:
                payload = json.loads(analysis_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise InvalidArchive("L'analyse de l'archive est illisible.") from exc
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("segments"), list)
                or not isinstance(payload.get("parts"), list)
            ):
                raise InvalidArchive("Le format de l'analyse archivée est invalide.")
            if manifest.get("analysis_checksum_sha256") != sha256_file(analysis_path):
                raise InvalidArchive("L'analyse ne correspond pas au manifeste.")
            duration = audio_duration(extracted["audio"])
            validate_analysis_duration(payload, duration)

            self._save_progress(job, {
                "stage": "archive_restore",
                "message": "Restauration du cours et de ses rendus",
                "progress": 0.62,
            })
            expiration = utc_now() + timedelta(seconds=settings.media_retention_seconds)
            rows: list[Artifact] = []
            keys: dict[str, str] = {}
            for kind, path in extracted.items():
                filename, mime_type = ALLOWED_FILES[kind]
                key = f"users/{job.user_id}/projects/{job.project_id}/jobs/{job.id}/{filename}"
                media_storage.upload_file(key, path, mime_type)
                keys[kind] = key
                rows.append(Artifact(
                    user_id=job.user_id,
                    project_id=job.project_id,
                    job_id=job.id,
                    kind=kind,
                    mime_type=mime_type,
                    size_bytes=path.stat().st_size,
                    storage_key=key,
                    checksum_sha256=sha256_file(path),
                    storage_metered_at=utc_now(),
                    expires_at=expiration,
                ))
            with SessionLocal() as db:
                previous = db.scalars(select(Artifact).where(Artifact.job_id == job.id)).all()
                for item in previous:
                    meter_media(db, item)
                    db.delete(item)
                db.add_all(rows)
                db.commit()

            self._record_impact_event(
                job,
                "archive_restored",
                duration_seconds=duration,
                storage_bytes=sum(path.stat().st_size for path in extracted.values()),
                details={"archive_schema": manifest["schema"]},
            )

            job.artifacts = keys
            job.metrics = {
                "segments": len(payload["segments"]),
                "parts": len(payload["parts"]),
                "duration_seconds": duration,
                "elapsed_seconds": time.monotonic() - started,
                "transcription_calls": 0,
                "imported_archive": True,
                "archive_schema": manifest["schema"],
            }
            # Once restored, an imported archive is a regular editable course.
            job.tool = "audio_pipeline"
            job.state = "completed"
            job.stage = "done"
            job.message = "Cours restauré sans nouvelle transcription"
            job.progress = 1.0
            job.error = None
        except AnalysisCancelled:
            job.state = "cancelled"
            job.message = "Import annulé"
        except Exception as exc:
            job.state = "failed"
            job.stage = "failed"
            job.message = "Échec de l'import de l'archive"
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
        if job.tool == "semantic_reanalysis":
            result = self._process_semantic_reanalysis(job, heartbeat_stopped, heartbeat)
            self._checkpoint_completed(job)
            return result
        if job.tool == "subtitle_proofread":
            return self._process_subtitle_proofread(job, heartbeat_stopped, heartbeat)
        if job.tool == "audio_selection":
            result = self._process_audio_selection(job, heartbeat_stopped, heartbeat)
            self._checkpoint_completed(job)
            return result
        if job.tool == "image_generation":
            return self._process_image_generation(job, heartbeat_stopped, heartbeat)
        if job.tool == "video_render":
            result = self._process_video_render(job, heartbeat_stopped, heartbeat)
            self._checkpoint_completed(job)
            return result
        if job.tool == "archive_export":
            return self._process_archive_export(job, heartbeat_stopped, heartbeat)
        if job.tool == "archive_import":
            return self._process_archive_import(job, heartbeat_stopped, heartbeat)
        try:
            if job.tool != "audio_pipeline":
                raise ValueError(f"Unsupported job tool: {job.tool}")
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
            semantic_input_tokens = 0
            semantic_output_tokens = 0

            def record_transcription(call: TranscriptionCall) -> None:
                nonlocal transcription_calls, transcription_checkpoint_hits
                self._record_transcription_call(job, call)
                if call.reused:
                    transcription_checkpoint_hits += 1
                else:
                    transcription_calls += 1

            def record_semantic_analysis(call: SemanticAnalysisCall) -> None:
                nonlocal semantic_input_tokens, semantic_output_tokens
                self._record_semantic_analysis_call(job, call)
                semantic_input_tokens += call.input_tokens
                semantic_output_tokens += call.output_tokens

            transcription_mode = str(
                job.options.get("transcription_mode", settings.transcription_backend)
            )
            chaptering_mode = str(
                job.options.get(
                    "chaptering_mode",
                    "ai" if settings.semantic_analysis_backend == "openai" else "local",
                )
            )
            if transcription_mode not in {"cloud", "local", "openai"}:
                raise ValueError("Unsupported transcription mode")
            if chaptering_mode not in {"ai", "local", "none", "openai", "heuristic"}:
                raise ValueError("Unsupported chaptering mode")
            use_cloud_transcription = transcription_mode in {"cloud", "openai"}
            use_ai_chaptering = chaptering_mode in {"ai", "openai"}
            if use_cloud_transcription and self.transcription_provider is None:
                raise ValueError("Cloud transcription provider is unavailable")
            if use_ai_chaptering and self.semantic_analyzer is None:
                raise ValueError("AI chaptering provider is unavailable")

            provider = (
                CheckpointingTranscriptionProvider(
                    self.transcription_provider,
                    job,
                    storage=media_storage,
                    session_factory=SessionLocal,
                    retention_seconds=settings.media_retention_seconds,
                )
                if use_cloud_transcription and self.transcription_provider
                else None
            )
            semantic_analyzer = self.semantic_analyzer if use_ai_chaptering else None
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
                    record_transcription if provider else None
                ),
                semantic_analyzer=semantic_analyzer,
                on_semantic_usage=(
                    record_semantic_analysis if semantic_analyzer else None
                ),
                chaptering_mode=chaptering_mode,
                course_title=str(job.options.get("course_title") or "Cours audio"),
            )
            self._wait_if_paused(job)
            self._store_artifacts(job, result)
            self._wait_if_paused(job)
            result_values = asdict(result)
            self._record_impact_event(
                job,
                "course_completed",
                duration_seconds=result.duration_seconds,
                storage_bytes=sum(
                    Path(result_values[path_field]).stat().st_size
                    for path_field, _, _ in ARTIFACTS.values()
                ),
                details={
                    "segments": result.segment_count,
                    "parts": result.part_count,
                },
            )
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
                "semantic_input_tokens": semantic_input_tokens,
                "semantic_output_tokens": semantic_output_tokens,
                "transcription_mode": "cloud" if use_cloud_transcription else "local",
                "chaptering_mode": "ai" if use_ai_chaptering else "none" if chaptering_mode == "none" else "local",
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
        self._checkpoint_completed(job)
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
    configure_logging(settings.log_level)
    worker = Worker()
    logger.info(
        "worker_started",
        extra={"event_fields": {"worker_id": worker.worker_id, "release": settings.release}},
    )
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
