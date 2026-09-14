from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import av
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_user
from .config import settings
from .database import get_db
from .media_lifecycle import meter_media
from .media_storage import LocalMediaStorage
from .models import BrandKit, BrandTemplate, BrandTemplateFile, User, utc_now
from .runtime import media_storage


router = APIRouter(prefix="/api/brand", tags=["brand"])
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTENSIONS = {".m4v", ".mov", ".mp4"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(gt=0)
    usage_mode: Literal["static_frame", "animated"] = "static_frame"
    frame_seconds: float = Field(default=0, ge=0, le=24 * 60 * 60)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("filename", mode="before")
    @classmethod
    def normalize_filename(cls, value: str) -> str:
        filename = Path(value.replace("\\", "/")).name.strip()
        if not filename:
            raise ValueError("Le nom du fichier est vide.")
        return filename

    @field_validator("content_type", mode="before")
    @classmethod
    def normalize_content_type(cls, value: str) -> str:
        return value.split(";", 1)[0].strip().lower()


class TemplateResponse(BaseModel):
    id: str
    name: str
    original_name: str
    source_kind: str
    usage_mode: str
    status: str
    width: int | None
    height: int | None
    duration_seconds: float | None
    version: int
    frame_seconds: float
    preview_url: str | None
    created_at: datetime
    updated_at: datetime


class TemplateUploadResponse(BaseModel):
    template: TemplateResponse
    upload: dict[str, Any]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def owned_template(
    db: Session,
    user_id: str,
    template_id: str,
    *,
    for_update: bool = False,
) -> BrandTemplate:
    statement = select(BrandTemplate).where(
            BrandTemplate.id == template_id,
            BrandTemplate.user_id == user_id,
        )
    if for_update:
        statement = statement.with_for_update()
    template = db.scalar(statement)
    if template is None:
        raise HTTPException(status_code=404, detail="Template introuvable.")
    return template


def template_response(template: BrandTemplate, has_preview: bool) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        name=template.name,
        original_name=template.original_name,
        source_kind=template.source_kind,
        usage_mode=template.usage_mode,
        status=template.status,
        width=template.width,
        height=template.height,
        duration_seconds=(
            template.duration_milliseconds / 1000
            if template.duration_milliseconds is not None
            else None
        ),
        version=template.version,
        frame_seconds=float(template.settings.get("frame_seconds", 0)),
        preview_url=f"/api/brand/templates/{template.id}/preview" if has_preview else None,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def response_for(db: Session, template: BrandTemplate) -> TemplateResponse:
    has_preview = db.scalar(
        select(BrandTemplateFile.id).where(
            BrandTemplateFile.template_id == template.id,
            BrandTemplateFile.kind == "preview",
        )
    ) is not None
    return template_response(template, has_preview)


def brand_kit_for(db: Session, user_id: str) -> BrandKit:
    kit = db.scalar(select(BrandKit).where(BrandKit.user_id == user_id))
    if kit is None:
        kit = BrandKit(user_id=user_id)
        db.add(kit)
        db.flush()
    return kit


def image_preview(source: Path, destination: Path) -> tuple[int, int]:
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            image.thumbnail((960, 960), Image.Resampling.LANCZOS)
            image.save(destination, format="PNG", optimize=True)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="L'image est invalide ou illisible.") from exc
    if width < 320 or height < 320:
        raise HTTPException(status_code=422, detail="Le template doit mesurer au moins 320 × 320 px.")
    return width, height


def video_preview(
    source: Path,
    destination: Path,
    frame_seconds: float,
) -> tuple[int, int, float]:
    try:
        container = av.open(str(source))
    except Exception as exc:
        raise HTTPException(status_code=422, detail="La vidéo est invalide ou illisible.") from exc
    try:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise HTTPException(status_code=422, detail="Aucune piste vidéo n'a été trouvée.")
        duration = (
            float(stream.duration * stream.time_base)
            if stream.duration is not None and stream.time_base is not None
            else float(container.duration / av.time_base)
            if container.duration is not None
            else 0.0
        )
        if duration > 0 and frame_seconds > duration:
            raise HTTPException(
                status_code=422,
                detail="L'instant choisi dépasse la durée de la vidéo.",
            )
        if frame_seconds > 0:
            container.seek(int(frame_seconds * av.time_base), any_frame=False, backward=True)
        frame = None
        for candidate in container.decode(stream):
            frame = candidate
            if frame_seconds <= 0 or candidate.time is None or float(candidate.time) >= frame_seconds:
                break
        if frame is None:
            raise HTTPException(status_code=422, detail="Impossible d'extraire une image de la vidéo.")
        image = Image.fromarray(frame.to_ndarray(format="rgb24"), mode="RGB")
        width, height = image.size
        image.thumbnail((960, 960), Image.Resampling.LANCZOS)
        image.save(destination, format="PNG", optimize=True)
        return width, height, duration
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="La vidéo est invalide ou illisible.") from exc
    finally:
        container.close()


@router.get("/templates", response_model=list[TemplateResponse])
def list_templates(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TemplateResponse]:
    templates = db.scalars(
        select(BrandTemplate)
        .where(BrandTemplate.user_id == user.id)
        .order_by(BrandTemplate.updated_at.desc())
    ).all()
    preview_ids = set(
        db.scalars(
            select(BrandTemplateFile.template_id).where(
                BrandTemplateFile.user_id == user.id,
                BrandTemplateFile.kind == "preview",
            )
        ).all()
    )
    return [template_response(template, template.id in preview_ids) for template in templates]


@router.post("/templates", response_model=TemplateUploadResponse, status_code=201)
def create_template(
    payload: TemplateCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TemplateUploadResponse:
    suffix = Path(payload.filename).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        source_kind = "image"
        if payload.size_bytes > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="L'image dépasse 25 Mo.")
        if not (
            payload.content_type.startswith("image/")
            or payload.content_type == "application/octet-stream"
        ):
            raise HTTPException(status_code=422, detail="Format d'image non pris en charge.")
        usage_mode = "static_frame"
    elif suffix in VIDEO_EXTENSIONS:
        source_kind = "video"
        if payload.size_bytes > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="La vidéo est trop volumineuse.")
        if not (
            payload.content_type.startswith("video/")
            or payload.content_type == "application/octet-stream"
        ):
            raise HTTPException(status_code=422, detail="Format vidéo non pris en charge.")
        usage_mode = payload.usage_mode
    else:
        raise HTTPException(status_code=422, detail="Utilisez une image PNG/JPEG ou une vidéo MP4/MOV.")

    kit = brand_kit_for(db, user.id)
    template = BrandTemplate(
        brand_kit_id=kit.id,
        user_id=user.id,
        name=payload.name,
        original_name=payload.filename,
        source_kind=source_kind,
        usage_mode=usage_mode,
        status="pending",
        settings={"frame_seconds": payload.frame_seconds, "zones": []},
    )
    db.add(template)
    db.flush()
    source_file = BrandTemplateFile(
        template_id=template.id,
        user_id=user.id,
        kind="source",
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        storage_key=f"users/{user.id}/brand/templates/{template.id}/source{suffix}",
    )
    db.add(source_file)
    upload = media_storage.upload_target(
        source_file.storage_key,
        source_file.content_type,
        source_file.size_bytes,
        f"/api/brand/templates/{template.id}/content",
    )
    db.commit()
    db.refresh(template)
    return TemplateUploadResponse(
        template=template_response(template, False),
        upload=upload,
    )


@router.put("/templates/{template_id}/content", status_code=204)
async def upload_local_template(
    template_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    if not isinstance(media_storage, LocalMediaStorage):
        raise HTTPException(status_code=404, detail="Endpoint d'import local désactivé.")
    template = owned_template(db, user.id, template_id)
    if template.status != "pending":
        raise HTTPException(status_code=409, detail="Ce template n'attend pas de fichier.")
    source = db.scalar(
        select(BrandTemplateFile).where(
            BrandTemplateFile.template_id == template.id,
            BrandTemplateFile.kind == "source",
        )
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Réservation d'import introuvable.")
    destination = media_storage.path_for(source.storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = destination.with_name(f".{destination.name}.{source.id}.part")
    received = 0
    try:
        with partial.open("wb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > source.size_bytes:
                    raise HTTPException(status_code=413, detail="Fichier trop volumineux.")
                output.write(chunk)
        if received != source.size_bytes:
            raise HTTPException(status_code=422, detail="La taille reçue ne correspond pas.")
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


@router.post("/templates/{template_id}/complete", response_model=TemplateResponse)
def complete_template(
    template_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TemplateResponse:
    template = owned_template(db, user.id, template_id, for_update=True)
    if template.status == "ready":
        return response_for(db, template)
    if template.status != "pending":
        raise HTTPException(status_code=409, detail="Ce template ne peut pas être finalisé.")
    source = db.scalar(
        select(BrandTemplateFile).where(
            BrandTemplateFile.template_id == template.id,
            BrandTemplateFile.kind == "source",
        )
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Fichier source introuvable.")
    try:
        stored = media_storage.stat(source.storage_key)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Le fichier importé est indisponible.") from exc
    if stored.size_bytes != source.size_bytes:
        raise HTTPException(status_code=422, detail="La taille reçue ne correspond pas.")

    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        root = Path(temporary)
        source_path = root / Path(template.original_name).name
        preview_path = root / "preview.png"
        try:
            media_storage.download_file(source.storage_key, source_path)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Lecture du template impossible.") from exc
        if template.source_kind == "image":
            width, height = image_preview(source_path, preview_path)
            duration = None
        else:
            width, height, duration = video_preview(
                source_path,
                preview_path,
                float(template.settings.get("frame_seconds", 0)),
            )
        preview_key = f"users/{user.id}/brand/templates/{template.id}/preview.png"
        try:
            media_storage.upload_file(preview_key, preview_path, "image/png")
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Création de la vignette impossible.") from exc
        now = utc_now()
        source.checksum_sha256 = sha256_file(source_path)
        source.uploaded_at = now
        source.storage_metered_at = now
        preview = BrandTemplateFile(
            template_id=template.id,
            user_id=user.id,
            kind="preview",
            content_type="image/png",
            size_bytes=preview_path.stat().st_size,
            storage_key=preview_key,
            checksum_sha256=sha256_file(preview_path),
            uploaded_at=now,
            storage_metered_at=now,
        )
        db.add(preview)
        template.width = width
        template.height = height
        template.duration_milliseconds = round(duration * 1000) if duration is not None else None
        template.status = "ready"
        template.updated_at = now
        db.commit()
        db.refresh(template)
        return template_response(template, True)


@router.get("/templates/{template_id}/preview")
def template_preview(
    template_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    template = owned_template(db, user.id, template_id)
    preview = db.scalar(
        select(BrandTemplateFile).where(
            BrandTemplateFile.template_id == template.id,
            BrandTemplateFile.user_id == user.id,
            BrandTemplateFile.kind == "preview",
        )
    )
    if preview is None:
        raise HTTPException(status_code=404, detail="Vignette introuvable.")
    if isinstance(media_storage, LocalMediaStorage):
        path = media_storage.path_for(preview.storage_key)
        if not path.is_file():
            raise HTTPException(status_code=410, detail="Vignette indisponible.")
        return FileResponse(path, media_type="image/png")
    url = media_storage.download_url(preview.storage_key, "template-preview.png")
    if not url:
        raise HTTPException(status_code=410, detail="Vignette indisponible.")
    return RedirectResponse(url)


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(
    template_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    template = owned_template(db, user.id, template_id, for_update=True)
    files = db.scalars(
        select(BrandTemplateFile).where(
            BrandTemplateFile.template_id == template.id,
            BrandTemplateFile.user_id == user.id,
        )
    ).all()
    for item in files:
        meter_media(db, item)
        try:
            media_storage.delete(item.storage_key)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Suppression du template impossible.") from exc
    db.delete(template)
    db.commit()
