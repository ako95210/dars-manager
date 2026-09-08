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

from .pipeline import run_pipeline
from .storage import TemporaryStorage


TERMINAL_STATES = {"completed", "cancelled", "failed", "expired"}


@dataclass
class Job:
    id: str
    user_id: str
    workspace: Path
    input_path: Path
    model_name: str
    language: str
    cpu_threads: int
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
    def __init__(self, storage: TemporaryStorage) -> None:
        self.storage = storage
        self.jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def create(
        self,
        user_id: str,
        filename: str,
        model_name: str,
        language: str,
        cpu_threads: int,
    ) -> Job:
        job_id = uuid.uuid4().hex
        workspace = self.storage.create_workspace(user_id, job_id)
        suffix = Path(filename).suffix.lower()[:10]
        job = Job(
            id=job_id,
            user_id=user_id,
            workspace=workspace,
            input_path=workspace / f"input{suffix}",
            model_name=model_name,
            language=language,
            cpu_threads=cpu_threads,
        )
        with self._lock:
            self.jobs[job_id] = job
        return job

    def get(self, user_id: str, job_id: str) -> Job | None:
        with self._lock:
            job = self.jobs.get(job_id)
            return job if job and job.user_id == user_id else None

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
        threading.Thread(target=self._monitor, args=(job,), daemon=True).start()

    def _monitor(self, job: Job) -> None:
        while job.state not in TERMINAL_STATES:
            try:
                kind, payload = job.event_queue.get(timeout=0.5)
            except queue.Empty:
                if job.process and not job.process.is_alive() and job.process.exitcode not in (None, 0):
                    job.state = "failed"
                    job.error = f"Worker exited with code {job.process.exitcode}"
                    job.updated_at = time.time()
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

    def pause(self, job: Job) -> None:
        if job.state != "running":
            raise ValueError("Only a running job can be paused")
        job.pause_event.set()
        job.state = "paused"
        job.message = "Pause requested"
        job.updated_at = time.time()

    def resume(self, job: Job) -> None:
        if job.state != "paused":
            raise ValueError("Only a paused job can be resumed")
        job.pause_event.clear()
        job.state = "running"
        job.message = "Resume requested"
        job.updated_at = time.time()

    def cancel(self, job: Job) -> None:
        if job.state in TERMINAL_STATES:
            return
        job.cancel_event.set()
        job.pause_event.clear()
        job.state = "cancelling"
        job.message = "Cancellation requested"
        job.updated_at = time.time()

    def delete(self, job: Job) -> None:
        if job.process and job.process.is_alive():
            job.cancel_event.set()
            job.pause_event.clear()
            job.process.join(timeout=5)
            if job.process.is_alive():
                job.process.terminate()
                job.process.join(timeout=5)
        self.storage.remove_workspace(job.workspace)
        with self._lock:
            self.jobs.pop(job.id, None)
