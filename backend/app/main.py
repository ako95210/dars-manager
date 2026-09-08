from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .config import settings
from .jobs import Job, JobManager, TERMINAL_STATES
from .storage import TemporaryStorage


storage = TemporaryStorage(settings.workspace_root, settings.job_ttl_seconds)
manager = JobManager(storage)


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.cleanup_expired()
    yield
    for job in list(manager.jobs.values()):
        manager.delete(job)


app = FastAPI(title="Dars Manager Beta API", version="0.1.0", lifespan=lifespan)


def current_user(x_dars_user: Annotated[str | None, Header()] = None) -> str:
    # Phase-one identity boundary. Replaced by authenticated user claims in phase two.
    return (x_dars_user or "pilot").strip() or "pilot"


def owned_job(user_id: str, job_id: str) -> Job:
    job = manager.get(user_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "workspace": str(settings.workspace_root)}


@app.post("/api/jobs", status_code=202)
async def create_job(
    file: Annotated[UploadFile, File()],
    model: Annotated[str, Form()] = "base",
    language: Annotated[str, Form()] = "fr",
    user_id: str = Header(default="pilot", alias="X-Dars-User"),
) -> dict:
    if model not in {"tiny", "base", "small"}:
        raise HTTPException(status_code=422, detail="Unsupported Whisper model")
    try:
        job = manager.create(
            user_id,
            file.filename or "audio",
            model,
            language,
            settings.whisper_cpu_threads,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    size = 0
    try:
        with job.input_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Upload too large")
                output.write(chunk)
        manager.start(job)
    except Exception:
        manager.delete(job)
        raise
    finally:
        await file.close()
    return job.public()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user_id: str = Header(default="pilot", alias="X-Dars-User")) -> dict:
    return owned_job(user_id, job_id).public()


@app.get("/api/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    user_id: str = Header(default="pilot", alias="X-Dars-User"),
) -> StreamingResponse:
    job = owned_job(user_id, job_id)

    async def events() -> AsyncIterator[str]:
        previous = ""
        while True:
            payload = json.dumps(job.public(), ensure_ascii=False)
            if payload != previous:
                yield f"event: job\ndata: {payload}\n\n"
                previous = payload
            if job.state in TERMINAL_STATES:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str, user_id: str = Header(default="pilot", alias="X-Dars-User")) -> dict:
    job = owned_job(user_id, job_id)
    try:
        manager.pause(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str, user_id: str = Header(default="pilot", alias="X-Dars-User")) -> dict:
    job = owned_job(user_id, job_id)
    try:
        manager.resume(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user_id: str = Header(default="pilot", alias="X-Dars-User")) -> dict:
    job = owned_job(user_id, job_id)
    manager.cancel(job)
    return job.public()


@app.get("/api/jobs/{job_id}/artifacts/{artifact}")
def download_artifact(
    job_id: str,
    artifact: str,
    user_id: str = Header(default="pilot", alias="X-Dars-User"),
) -> FileResponse:
    job = owned_job(user_id, job_id)
    path_value = job.artifacts.get(artifact)
    if not path_value:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = Path(path_value)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Artifact expired")
    media_types = {
        "analysis": "application/json",
        "audio": "audio/wav",
        "cover": "image/png",
        "video": "video/mp4",
    }
    return FileResponse(path, filename=path.name, media_type=media_types.get(artifact))


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, user_id: str = Header(default="pilot", alias="X-Dars-User")) -> None:
    manager.delete(owned_job(user_id, job_id))
