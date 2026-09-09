from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from drsm_core import AnalysisCancelled

from .job_state import JobStateStore, MemoryJobStateStore
from .pipeline import run_pipeline
from .storage import TemporaryStorage


TERMINAL_STATES = {"completed", "cancelled", "failed", "expired"}


@dataclass
class Job:
    id: str
    user_id: str
    project_id: str
    workspace: Path
    input_path: Path
    model_name: str
    language: str
    cpu_threads: int
    source_asset_id: str | None = None
    source_expires_at: str | None = None
    execution_backend: str = "inline"
    worker_id: str | None = None
    lease_expires_at: float | None = None
    attempt_count: int = 0
    state: str = "queued"
    stage: str = "upload"
    message: str = "Upload received"
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    process: mp.Process | None = field(default=None, repr=False)
    event_queue: Any = field(default=None, repr=False)
    pause_event: Any = field(default=None, repr=False)
    cancel_event: Any = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "source_asset_id": self.source_asset_id,
            "source_expires_at": self.source_expires_at,
            "execution_backend": self.execution_backend,
            "state": self.state,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "artifacts": sorted(self.artifacts),
            "metrics": self.metrics,
        }

    def record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "workspace": str(self.workspace),
            "input_path": str(self.input_path),
            "model_name": self.model_name,
            "language": self.language,
            "cpu_threads": self.cpu_threads,
            "source_asset_id": self.source_asset_id,
            "source_expires_at": self.source_expires_at,
            "execution_backend": self.execution_backend,
            "worker_id": self.worker_id,
            "lease_expires_at": self.lease_expires_at,
            "attempt_count": self.attempt_count,
            "state": self.state,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Job:
        return cls(
            id=record["id"],
            user_id=record["user_id"],
            project_id=record.get("project_id", ""),
            workspace=Path(record["workspace"]),
            input_path=Path(record["input_path"]),
            model_name=record["model_name"],
            language=record["language"],
            cpu_threads=int(record["cpu_threads"]),
            source_asset_id=record.get("source_asset_id"),
            source_expires_at=record.get("source_expires_at"),
            execution_backend=record.get("execution_backend", "inline"),
            worker_id=record.get("worker_id"),
            lease_expires_at=record.get("lease_expires_at"),
            attempt_count=int(record.get("attempt_count", 0)),
            state=record["state"],
            stage=record["stage"],
            message=record["message"],
            progress=float(record["progress"]),
            created_at=float(record["created_at"]),
            updated_at=float(record["updated_at"]),
            error=record.get("error"),
            artifacts=dict(record.get("artifacts", {})),
            metrics=dict(record.get("metrics", {})),
        )


def _process_entry(
    input_path: str,
    workspace: str,
    model_name: str,
    language: str,
    cpu_threads: int,
    event_queue,
    pause_event,
    cancel_event,
) -> None:
    try:
        result = run_pipeline(
            Path(input_path),
            Path(workspace),
            model_name=model_name,
            language=language,
            cpu_threads=cpu_threads,
            progress=lambda event: event_queue.put(("progress", event)),
            should_pause=pause_event.is_set,
            should_cancel=cancel_event.is_set,
        )
        event_queue.put(("completed", asdict(result)))
    except AnalysisCancelled:
        event_queue.put(("cancelled", None))
    except Exception as exc:
        event_queue.put(("failed", f"{type(exc).__name__}: {exc}"))


class JobManager:
    def __init__(
        self,
        storage: TemporaryStorage,
        state_store: JobStateStore | None = None,
    ) -> None:
        self.storage = storage
        self.state_store = state_store or MemoryJobStateStore(storage.ttl_seconds)
        self.jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def _save(self, job: Job) -> None:
        self.state_store.save(job.record())

    def create(
        self,
        user_id: str,
        project_id: str,
        filename: str,
        model_name: str,
        language: str,
        cpu_threads: int,
        source_asset_id: str | None = None,
        source_expires_at: str | None = None,
        execution_backend: str = "inline",
        allocate_workspace: bool = True,
    ) -> Job:
        job_id = uuid.uuid4().hex
        workspace = self.storage.create_workspace(user_id, job_id) if allocate_workspace else self.storage.workspace_path(user_id, job_id)
        suffix = Path(filename).suffix.lower()[:10]
        job = Job(
            id=job_id,
            user_id=user_id,
            project_id=project_id,
            workspace=workspace,
            input_path=workspace / f"input{suffix}",
            model_name=model_name,
            language=language,
            cpu_threads=cpu_threads,
            source_asset_id=source_asset_id,
            source_expires_at=source_expires_at,
            execution_backend=execution_backend,
        )
        with self._lock:
            self.jobs[job_id] = job
            self._save(job)
        return job

    def get(self, user_id: str, job_id: str) -> Job | None:
        with self._lock:
            job = self.jobs.get(job_id)
        if job is None or job.execution_backend == "worker":
            record = self.state_store.get(job_id)
            job = Job.from_record(record) if record else None
        return job if job and job.user_id == user_id else None

    def list_for_user(self, user_id: str, project_id: str | None = None) -> list[Job]:
        jobs = [
            Job.from_record(record)
            for record in self.state_store.all()
            if record.get("user_id") == user_id
            and (project_id is None or record.get("project_id") == project_id)
        ]
        return sorted(jobs, key=lambda job: job.updated_at, reverse=True)

    def start(self, job: Job) -> None:
        if job.state != "queued":
            raise ValueError("Job is not queued")
        context = mp.get_context("spawn")
        job.event_queue = context.Queue()
        job.pause_event = context.Event()
        job.cancel_event = context.Event()
        job.process = context.Process(
            target=_process_entry,
            args=(
                str(job.input_path),
                str(job.workspace),
                job.model_name,
                job.language,
                job.cpu_threads,
                job.event_queue,
                job.pause_event,
                job.cancel_event,
            ),
            daemon=True,
        )
        job.state = "running"
        job.updated_at = time.time()
        job.process.start()
        self._save(job)
        threading.Thread(target=self._monitor, args=(job,), daemon=True).start()

    def _monitor(self, job: Job) -> None:
        while job.state not in TERMINAL_STATES:
            try:
                kind, payload = job.event_queue.get(timeout=0.5)
            except queue.Empty:
                if job.process and not job.process.is_alive():
                    job.state = "failed"
                    job.message = "Pipeline failed"
                    job.error = (
                        f"Worker exited with code {job.process.exitcode}"
                        if job.process.exitcode not in (None, 0)
                        else "Worker exited without returning a result"
                    )
                    job.updated_at = time.time()
                    self._save(job)
                continue
            job.updated_at = time.time()
            if kind == "progress":
                job.stage = payload["stage"]
                job.message = payload["message"]
                if payload.get("progress") is not None:
                    job.progress = float(payload["progress"])
            elif kind == "completed":
                job.state = "completed"
                job.stage = "done"
                job.message = "Pipeline completed"
                job.progress = 1.0
                job.artifacts = {
                    "analysis": payload["analysis_path"],
                    "audio": payload["audio_path"],
                    "cover": payload["cover_path"],
                    "video": payload["video_path"],
                }
                job.metrics = {
                    "segments": payload["segment_count"],
                    "parts": payload["part_count"],
                    "duration_seconds": payload["duration_seconds"],
                    "elapsed_seconds": payload["elapsed_seconds"],
                }
            elif kind == "cancelled":
                job.state = "cancelled"
                job.message = "Job cancelled"
            elif kind == "failed":
                job.state = "failed"
                job.message = "Pipeline failed"
                job.error = payload
            self._save(job)

    def pause(self, job: Job) -> None:
        if job.execution_backend == "worker":
            if job.state not in {"queued", "running"}:
                raise ValueError("Only a queued or running job can be paused")
            job.state = "paused"
            job.message = "Pause requested"
            job.updated_at = time.time()
            self._save(job)
            return
        if job.state != "running":
            raise ValueError("Only a running job can be paused")
        if job.pause_event is None:
            raise ValueError("Job worker is no longer available")
        job.pause_event.set()
        job.state = "paused"
        job.message = "Pause requested"
        job.updated_at = time.time()
        self._save(job)

    def resume(self, job: Job) -> None:
        if job.execution_backend == "worker":
            if job.state != "paused":
                raise ValueError("Only a paused job can be resumed")
            job.state = "running" if job.worker_id else "queued"
            job.message = "Resume requested"
            job.updated_at = time.time()
            self._save(job)
            return
        if job.state != "paused":
            raise ValueError("Only a paused job can be resumed")
        if job.pause_event is None:
            raise ValueError("Job worker is no longer available")
        job.pause_event.clear()
        job.state = "running"
        job.message = "Resume requested"
        job.updated_at = time.time()
        self._save(job)

    def cancel(self, job: Job) -> None:
        if job.state in TERMINAL_STATES:
            return
        if job.execution_backend == "worker":
            job.state = "cancelling" if job.worker_id else "cancelled"
            job.message = "Cancellation requested" if job.worker_id else "Job cancelled"
            job.updated_at = time.time()
            self._save(job)
            return
        if job.cancel_event is None or job.pause_event is None:
            raise ValueError("Job worker is no longer available")
        job.cancel_event.set()
        job.pause_event.clear()
        job.state = "cancelling"
        job.message = "Cancellation requested"
        job.updated_at = time.time()
        self._save(job)

    def delete(self, job: Job) -> None:
        if job.process and job.process.is_alive():
            if job.cancel_event is not None:
                job.cancel_event.set()
            if job.pause_event is not None:
                job.pause_event.clear()
            job.process.join(timeout=5)
            if job.process.is_alive():
                job.process.terminate()
                job.process.join(timeout=5)
        self.storage.remove_workspace(job.workspace)
        with self._lock:
            self.jobs.pop(job.id, None)
        self.state_store.delete(job.id)

    def recover_interrupted(self) -> int:
        recovered = 0
        for record in self.state_store.all():
            job = Job.from_record(record)
            if job.state in TERMINAL_STATES or job.execution_backend == "worker":
                continue
            job.state = "failed"
            job.stage = "interrupted"
            job.message = "Pipeline interrupted"
            job.error = "API restarted before the job completed"
            job.updated_at = time.time()
            self._save(job)
            recovered += 1
        return recovered

    def shutdown(self) -> None:
        for job in list(self.jobs.values()):
            if job.execution_backend == "worker":
                continue
            if job.process and job.process.is_alive():
                if job.cancel_event is not None:
                    job.cancel_event.set()
                if job.pause_event is not None:
                    job.pause_event.clear()
                job.process.join(timeout=5)
                if job.process.is_alive():
                    job.process.terminate()
                    job.process.join(timeout=5)
            if job.state not in TERMINAL_STATES:
                job.state = "failed"
                job.stage = "interrupted"
                job.message = "Pipeline interrupted"
                job.error = "API stopped before the job completed"
                job.updated_at = time.time()
                self._save(job)
        with self._lock:
            self.jobs.clear()
