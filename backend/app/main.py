from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from drsm_core import safe_filename
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_admin, require_client, router as auth_router
from .admin_users import router as admin_users_router
from .archives import router as archives_router
from .billing import admin_router as admin_billing_router
from .billing import router as billing_router
from .brand import router as brand_router
from .config import settings
from .costs import seed_default_rates
from .database import SessionLocal, get_db, init_database
from .editor import router as editor_router
from .jobs import Job, JobManager, TERMINAL_STATES
from .media_storage import LocalMediaStorage
from .models import Artifact, Asset, Project, User
from .observability import OperationalMiddleware, configure_logging, runtime_metrics
from .projects import router as projects_router
from .runtime import job_queue, manager, media_storage, storage
from .media_lifecycle import meter_media, purge_expired_media
from .uploads import jobs_router as asset_jobs_router
from .uploads import router as uploads_router
from .transcription_api import router as transcription_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    with SessionLocal() as db:
        seed_default_rates(db)
        purge_expired_media(db)
    storage.cleanup_expired()
    manager.recover_interrupted()
    yield
    manager.shutdown()


configure_logging(settings.log_level)
hide_api_schema = settings.environment in {"beta", "production"}
app = FastAPI(
    title="Dars Manager Beta API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if hide_api_schema else "/docs",
    redoc_url=None if hide_api_schema else "/redoc",
    openapi_url=None if hide_api_schema else "/openapi.json",
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=list(settings.allowed_hosts),
)
app.add_middleware(
    OperationalMiddleware,
    trusted_origins=settings.trusted_origins,
    hsts=settings.environment in {"beta", "production"},
    log_requests=settings.environment in {"beta", "production"},
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(admin_users_router)
app.include_router(projects_router)
app.include_router(billing_router)
app.include_router(admin_billing_router)
app.include_router(uploads_router)
app.include_router(asset_jobs_router)
app.include_router(transcription_router)
app.include_router(editor_router)
app.include_router(brand_router)
app.include_router(archives_router)


def artifact_filename(job: Job, artifact: str) -> str:
    title = ""
    if job.tool == "audio_selection":
        title = str(job.options.get("title", ""))
    elif job.tool == "video_render":
        values = job.options.get("values", {})
        if isinstance(values, dict):
            title = str(values.get("title", ""))
    elif job.tool == "image_generation":
        title = str(job.options.get("title", ""))
    elif job.tool == "archive_export":
        title = str(job.options.get("project_title", ""))
    slug = safe_filename(title).replace("_", "-").lower()[:120].strip("-")
    part_indices = job.options.get("part_indices", [])
    prefix = (
        f"{int(part_indices[0]):02d}-"
        if isinstance(part_indices, list) and len(part_indices) == 1
        else ""
    )
    defaults = {
        "analysis": "analyse.json",
        "audio": "audio-normalise.wav",
        "selection_audio": f"{prefix}{slug or 'extrait-audio'}.wav",
        "generated_image": f"{slug or 'image-generee'}.png",
        "cover": f"{slug or 'couverture'}.png",
        "video": f"{slug or 'video'}.mp4",
        "archive": f"{slug or 'cours'}.dars",
    }
    return defaults.get(artifact, safe_filename(artifact))


def owned_job(user_id: str, job_id: str) -> Job:
    job = manager.get(user_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/health/live")
def liveness() -> dict:
    return {"status": "alive", "release": settings.release}


@app.get("/api/health")
@app.get("/api/health/ready")
def readiness(response: Response, db: Session = Depends(get_db)) -> dict:
    checks = {"database": False, "queue": False}
    try:
        db.execute(select(1))
        checks["database"] = True
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "release": settings.release, "checks": checks}
    try:
        checks["queue"] = job_queue.ping()
    except Exception:
        checks["queue"] = False
    return {
        "status": "ready" if checks["queue"] else "degraded",
        "release": settings.release,
        "checks": checks,
    }


@app.get("/api/admin/system")
def system_status(_admin: User = Depends(require_admin)) -> dict:
    return {
        "release": settings.release,
        "environment": settings.environment,
        "media_backend": media_storage.backend,
        "execution_backend": settings.execution_backend,
        "metrics": runtime_metrics.snapshot(),
    }


@app.post("/api/jobs", status_code=202)
async def create_job(
    file: Annotated[UploadFile, File()],
    project_id: Annotated[str, Form()],
    model: Annotated[str, Form()] = "base",
    language: Annotated[str, Form()] = "fr",
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict:
    if model not in {"tiny", "base", "small"}:
        raise HTTPException(status_code=422, detail="Unsupported Whisper model")
    if db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    ) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if settings.execution_backend == "worker":
        raise HTTPException(
            status_code=410,
            detail="Use the temporary asset upload flow for worker execution",
        )
    try:
        job = manager.create(
            user.id,
            project_id,
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


@app.get("/api/jobs")
def list_jobs(
    project_id: str | None = None,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> list[dict]:
    if project_id and db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    ) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return [job.public() for job in manager.list_for_user(user.id, project_id)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: User = Depends(require_client)) -> dict:
    return owned_job(user.id, job_id).public()


@app.get("/api/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    user: User = Depends(require_client),
) -> StreamingResponse:
    job = owned_job(user.id, job_id)

    async def events() -> AsyncIterator[str]:
        previous = ""
        while True:
            current = owned_job(user.id, job_id)
            payload = json.dumps(current.public(), ensure_ascii=False)
            if payload != previous:
                yield f"event: job\ndata: {payload}\n\n"
                previous = payload
            if current.state in TERMINAL_STATES:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str, user: User = Depends(require_client)) -> dict:
    job = owned_job(user.id, job_id)
    try:
        manager.pause(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str, user: User = Depends(require_client)) -> dict:
    job = owned_job(user.id, job_id)
    try:
        manager.resume(job)
        if job.execution_backend == "worker" and job.state == "queued":
            try:
                job_queue.enqueue(job.id)
            except Exception:
                pass
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(require_client)) -> dict:
    job = owned_job(user.id, job_id)
    try:
        manager.cancel(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@app.get("/api/jobs/{job_id}/artifacts/{artifact}")
def download_artifact(
    job_id: str,
    artifact: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> Response:
    job = owned_job(user.id, job_id)
    if job.execution_backend == "worker":
        row = db.scalar(
            select(Artifact).where(
                Artifact.job_id == job.id,
                Artifact.user_id == user.id,
                Artifact.kind == artifact,
            )
        )
        if row is None or not row.storage_key:
            raise HTTPException(status_code=404, detail="Artifact not found")
        filename = artifact_filename(job, artifact)
        if isinstance(media_storage, LocalMediaStorage):
            path = media_storage.path_for(row.storage_key)
            if not path.is_file():
                raise HTTPException(status_code=410, detail="Artifact expired")
            return FileResponse(path, filename=filename, media_type=row.mime_type)
        url = media_storage.download_url(row.storage_key, filename)
        if not url:
            raise HTTPException(status_code=410, detail="Artifact expired")
        return RedirectResponse(url)
    path_value = job.artifacts.get(artifact)
    if not path_value:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = Path(path_value)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Artifact expired")
    media_types = {
        "analysis": "application/json",
        "audio": "audio/wav",
        "selection_audio": "audio/wav",
        "generated_image": "image/png",
        "cover": "image/png",
        "video": "video/mp4",
        "archive": "application/vnd.dars-manager.archive",
    }
    return FileResponse(
        path,
        filename=artifact_filename(job, artifact),
        media_type=media_types.get(artifact),
    )


@app.delete("/api/jobs/{job_id}/source")
def delete_job_source(
    job_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict:
    job = owned_job(user.id, job_id)
    if job.state not in TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail="Le média source ne peut être supprimé qu'après le traitement.",
        )
    if not job.source_asset_id:
        return job.public()
    asset = db.scalar(
        select(Asset).where(Asset.id == job.source_asset_id, Asset.user_id == user.id)
    )
    if asset is not None:
        meter_media(db, asset)
        if asset.storage_key:
            try:
                media_storage.delete(asset.storage_key)
            except Exception as exc:
                raise HTTPException(status_code=502, detail="Source deletion failed") from exc
        db.delete(asset)
        db.commit()
    manager.clear_source(job)
    return job.public()


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> None:
    job = owned_job(user.id, job_id)
    if job.state not in TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail="Un traitement actif doit être annulé avant sa suppression.",
        )
    jobs_to_delete = [job]
    if job.tool == "audio_pipeline":
        children = [
            candidate
            for candidate in manager.list_for_user(user.id, job.project_id)
            if candidate.options.get("source_job_id") == job.id
        ]
        if any(child.state not in TERMINAL_STATES for child in children):
            raise HTTPException(
                status_code=409,
                detail="Un export lié à ce cours est encore actif.",
            )
        jobs_to_delete = [*children, job]
    job_ids = [candidate.id for candidate in jobs_to_delete]
    artifacts = db.scalars(
        select(Artifact).where(
            Artifact.job_id.in_(job_ids),
            Artifact.user_id == user.id,
        )
    ).all()
    for row in artifacts:
        meter_media(db, row)
        if row.storage_key:
            try:
                media_storage.delete(row.storage_key)
            except Exception as exc:
                raise HTTPException(status_code=502, detail="Artifact deletion failed") from exc
    db.commit()
    for candidate in jobs_to_delete:
        manager.delete(candidate)


if settings.frontend_dist.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=settings.frontend_dist, html=True),
        name="frontend",
    )
