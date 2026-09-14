from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_user
from .config import settings
from .database import get_db
from .models import Asset, Project, User, utc_now
from .runtime import job_queue, manager, media_storage
from .uploads import UploadResponse, asset_response


router = APIRouter(prefix="/api/archives", tags=["archives"])


class ArchiveUploadCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=32)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/zip", min_length=1, max_length=120)
    size_bytes: int = Field(gt=0)

    @field_validator("filename")
    @classmethod
    def normalize_filename(cls, value: str) -> str:
        filename = Path(value.replace("\\", "/")).name.strip()
        if not filename:
            raise ValueError("Le nom du fichier est vide")
        return filename

    @field_validator("content_type")
    @classmethod
    def normalize_content_type(cls, value: str) -> str:
        return value.split(";", 1)[0].strip().lower()


def owned_archive(db: Session, user_id: str, asset_id: str) -> Asset:
    asset = db.scalar(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.user_id == user_id,
            Asset.kind == "dars_archive",
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="Archive introuvable.")
    return asset


@router.post("", response_model=UploadResponse, status_code=201)
def create_archive_upload(
    payload: ArchiveUploadCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> UploadResponse:
    project = db.scalar(
        select(Project).where(Project.id == payload.project_id, Project.user_id == user.id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    if payload.size_bytes > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Archive trop volumineuse.")
    if Path(payload.filename).suffix.lower() != ".dars":
        raise HTTPException(status_code=422, detail="Le fichier doit porter l'extension .dars.")
    if payload.content_type not in {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
        "application/vnd.dars-manager.archive",
    }:
        raise HTTPException(status_code=422, detail="Type d'archive non pris en charge.")

    asset = Asset(
        user_id=user.id,
        project_id=project.id,
        kind="dars_archive",
        original_name=payload.filename,
        content_type="application/vnd.dars-manager.archive",
        size_bytes=payload.size_bytes,
        status="pending",
        expires_at=utc_now() + timedelta(seconds=settings.media_retention_seconds),
    )
    db.add(asset)
    db.flush()
    asset.storage_key = (
        f"users/{user.id}/projects/{project.id}/assets/{asset.id}/archive.dars"
    )
    upload = media_storage.upload_target(
        asset.storage_key,
        asset.content_type,
        asset.size_bytes,
        f"/api/uploads/{asset.id}/content",
    )
    db.commit()
    db.refresh(asset)
    return UploadResponse(asset=asset_response(asset), upload=upload)


@router.post("/{asset_id}/import", status_code=202)
def import_archive(
    asset_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    asset = owned_archive(db, user.id, asset_id)
    if asset.status != "ready" or not asset.storage_key:
        raise HTTPException(status_code=409, detail="L'envoi de l'archive n'est pas terminé.")

    previous = [
        job
        for job in manager.list_for_user(user.id, asset.project_id)
        if job.source_asset_id == asset.id and job.options.get("archive_import") is True
    ]
    reusable = next(
        (job for job in previous if job.state not in {"failed", "cancelled", "expired"}),
        None,
    )
    if reusable is not None:
        return reusable.public()
    if any(job.state not in {"completed", "failed", "cancelled", "expired"} for job in previous):
        raise HTTPException(status_code=409, detail="L'import de cette archive est déjà en cours.")

    job = manager.create(
        user.id,
        asset.project_id,
        asset.original_name,
        "",
        "fr",
        settings.whisper_cpu_threads,
        source_asset_id=asset.id,
        source_expires_at=asset.expires_at.isoformat(),
        execution_backend="worker",
        allocate_workspace=False,
        tool="archive_import",
        options={"archive_import": True},
    )
    try:
        job_queue.enqueue(job.id)
    except Exception:
        pass
    return job.public()
