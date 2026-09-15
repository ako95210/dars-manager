from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_client
from .config import settings
from .database import get_db
from .costs import cost_control, quote_usage, record_usage
from .media_storage import LocalMediaStorage
from .media_lifecycle import meter_media
from .models import Asset, Project, User, utc_now
from .runtime import job_queue, manager, media_storage


router = APIRouter(prefix="/api/uploads", tags=["uploads"])
jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])

ALLOWED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mpeg",
    ".mpga",
    ".ogg",
    ".opus",
    ".wav",
}
LOCAL_MODELS = {"tiny", "base", "small"}


class UploadCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=32)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
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


class JobFromAsset(BaseModel):
    asset_id: str = Field(min_length=1, max_length=32)
    model: str | None = None
    language: str = Field(default="fr", min_length=2, max_length=20)
    estimated_duration_seconds: float | None = Field(default=None, gt=0, le=24 * 60 * 60)
    cost_confirmed: bool = False


class AssetResponse(BaseModel):
    id: str
    project_id: str
    original_name: str
    content_type: str
    size_bytes: int
    status: str
    checksum_sha256: str | None
    expires_at: datetime


class UploadResponse(BaseModel):
    asset: AssetResponse
    upload: dict[str, Any]


def asset_response(asset: Asset) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        original_name=asset.original_name,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        status=asset.status,
        checksum_sha256=asset.checksum_sha256,
        expires_at=asset.expires_at,
    )


def owned_asset(db: Session, user_id: str, asset_id: str) -> Asset:
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.user_id == user_id))
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.post("", response_model=UploadResponse, status_code=201)
def create_upload(
    payload: UploadCreate,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> UploadResponse:
    project = db.scalar(
        select(Project).where(Project.id == payload.project_id, Project.user_id == user.id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.size_bytes > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Upload too large")
    suffix = Path(payload.filename).suffix.lower()
    if suffix not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Unsupported audio format")
    if not (
        payload.content_type.startswith("audio/")
        or payload.content_type == "application/octet-stream"
    ):
        raise HTTPException(status_code=422, detail="Unsupported audio content type")

    asset = Asset(
        user_id=user.id,
        project_id=project.id,
        kind="source_audio",
        original_name=payload.filename,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        status="pending",
        expires_at=utc_now() + timedelta(seconds=settings.media_retention_seconds),
    )
    db.add(asset)
    db.flush()
    asset.storage_key = (
        f"users/{user.id}/projects/{project.id}/assets/{asset.id}/source{suffix}"
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


@router.put("/{asset_id}/content", response_model=AssetResponse)
async def upload_local_content(
    asset_id: str,
    request: Request,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> AssetResponse:
    if not isinstance(media_storage, LocalMediaStorage):
        raise HTTPException(status_code=404, detail="Local upload endpoint disabled")
    asset = owned_asset(db, user.id, asset_id)
    if asset.status != "pending" or not asset.storage_key:
        raise HTTPException(status_code=409, detail="Asset is not awaiting upload")

    destination = media_storage.path_for(asset.storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = destination.with_name(f".{destination.name}.{asset.id}.part")
    received = 0
    checksum = hashlib.sha256()
    try:
        with partial.open("wb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > asset.size_bytes or received > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Upload too large")
                checksum.update(chunk)
                output.write(chunk)
        if received != asset.size_bytes:
            raise HTTPException(status_code=422, detail="Uploaded size does not match reservation")
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    asset.status = "ready"
    asset.uploaded_at = utc_now()
    asset.storage_metered_at = asset.uploaded_at
    asset.checksum_sha256 = checksum.hexdigest()
    db.commit()
    db.refresh(asset)
    return asset_response(asset)


@router.post("/{asset_id}/complete", response_model=AssetResponse)
def complete_upload(
    asset_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> AssetResponse:
    asset = owned_asset(db, user.id, asset_id)
    if asset.status == "ready":
        return asset_response(asset)
    if asset.status != "pending" or not asset.storage_key:
        raise HTTPException(status_code=409, detail="Asset is not awaiting upload")
    try:
        stored = media_storage.stat(asset.storage_key)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Uploaded object is not available") from exc
    if stored.size_bytes != asset.size_bytes:
        raise HTTPException(status_code=422, detail="Uploaded size does not match reservation")
    asset.status = "ready"
    asset.uploaded_at = utc_now()
    asset.storage_metered_at = asset.uploaded_at
    db.commit()
    db.refresh(asset)
    return asset_response(asset)


@router.delete("/{asset_id}", status_code=204)
def delete_upload(
    asset_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> None:
    asset = owned_asset(db, user.id, asset_id)
    meter_media(db, asset)
    if asset.storage_key:
        media_storage.delete(asset.storage_key)
    db.delete(asset)
    db.commit()


@jobs_router.post("/from-asset", status_code=202)
def create_job_from_asset(
    payload: JobFromAsset,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if settings.transcription_backend == "openai":
        model = payload.model or settings.transcription_model
        if model != settings.transcription_model:
            raise HTTPException(status_code=422, detail="Unsupported transcription model")
        if payload.estimated_duration_seconds is None:
            raise HTTPException(status_code=422, detail="Audio duration estimate is required")
    else:
        model = payload.model or settings.local_whisper_model
        if model not in LOCAL_MODELS:
            raise HTTPException(status_code=422, detail="Unsupported local Whisper model")
    asset = owned_asset(db, user.id, payload.asset_id)
    if asset.status != "ready" or not asset.storage_key:
        raise HTTPException(status_code=409, detail="Asset upload is not complete")
    cost_decision = None
    if settings.transcription_backend == "openai":
        quote = quote_usage(
            db,
            provider="openai",
            service="transcription",
            model=settings.transcription_model,
            quantity=math.ceil(payload.estimated_duration_seconds or 0),
            unit="audio_second",
        )
        cost_decision = cost_control(
            db,
            user_id=user.id,
            proposed_amount_nanos=quote.amount_nanos,
            lock_policy=True,
        )
        if cost_decision.requires_confirmation and not payload.cost_confirmed:
            raise HTTPException(
                status_code=409,
                detail="Ce traitement dépasse un seuil financier et doit être confirmé.",
            )
    try:
        job = manager.create(
            user.id,
            asset.project_id,
            asset.original_name,
            model,
            payload.language,
            settings.whisper_cpu_threads,
            source_asset_id=asset.id,
            source_expires_at=asset.expires_at.isoformat(),
            execution_backend=settings.execution_backend,
            allocate_workspace=settings.execution_backend == "inline",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if settings.transcription_backend == "openai":
        try:
            record_usage(
                db,
                user_id=user.id,
                project_id=asset.project_id,
                job_id=job.id,
                provider="openai",
                service="transcription",
                model=settings.transcription_model,
                quantity=math.ceil(payload.estimated_duration_seconds or 0),
                unit="audio_second",
                status="estimated",
                idempotency_key=f"transcription:{job.id}:estimate",
                details={
                    "source": "browser_audio_metadata",
                    "cost_confirmed": payload.cost_confirmed,
                    "confirmation_reasons": (
                        list(cost_decision.confirmation_reasons) if cost_decision else []
                    ),
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            manager.delete(job)
            raise
    if settings.execution_backend == "worker":
        try:
            job_queue.enqueue(job.id)
        except Exception:
            # PostgreSQL remains the source of truth. A worker will discover
            # the queued row by polling even if Redis is briefly unavailable.
            pass
    else:
        try:
            media_storage.download_file(asset.storage_key, job.input_path)
            manager.start(job)
        except Exception:
            manager.delete(job)
            raise
    return job.public()
