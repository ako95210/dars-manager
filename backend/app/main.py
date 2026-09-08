from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_user, router as auth_router
from .config import settings
from .database import get_db, init_database
from .jobs import Job, JobManager, TERMINAL_STATES
from .models import Project, User
from .projects import router as projects_router
from .storage import TemporaryStorage


storage = TemporaryStorage(settings.workspace_root, settings.job_ttl_seconds)
manager = JobManager(storage)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    storage.cleanup_expired()
    yield
    for job in list(manager.jobs.values()):
        manager.delete(job)


app = FastAPI(title="Dars Manager Beta API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(projects_router)


def owned_job(user_id: str, job_id: str) -> Job:
    job = manager.get(user_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/jobs", status_code=202)
async def create_job(
    file: Annotated[UploadFile, File()],
    model: Annotated[str, Form()] = "base",
    language: Annotated[str, Form()] = "fr",
    project_id: Annotated[str | None, Form()] = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    if model not in {"tiny", "base", "small"}:
        raise HTTPException(status_code=422, detail="Unsupported Whisper model")
    if project_id and db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    ) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        job = manager.create(
            user.id,
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
def get_job(job_id: str, user: User = Depends(require_user)) -> dict:
    return owned_job(user.id, job_id).public()


@app.get("/api/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    user: User = Depends(require_user),
) -> StreamingResponse:
    job = owned_job(user.id, job_id)

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
def pause_job(job_id: str, user: User = Depends(require_user)) -> dict:
    job = owned_job(user.id, job_id)
    try:
        manager.pause(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str, user: User = Depends(require_user)) -> dict:
    job = owned_job(user.id, job_id)
    try:
        manager.resume(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(require_user)) -> dict:
    job = owned_job(user.id, job_id)
    manager.cancel(job)
    return job.public()


@app.get("/api/jobs/{job_id}/artifacts/{artifact}")
def download_artifact(
    job_id: str,
    artifact: str,
    user: User = Depends(require_user),
) -> FileResponse:
    job = owned_job(user.id, job_id)
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
def delete_job(job_id: str, user: User = Depends(require_user)) -> None:
    manager.delete(owned_job(user.id, job_id))


if settings.frontend_dist.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=settings.frontend_dist, html=True),
        name="frontend",
    )
