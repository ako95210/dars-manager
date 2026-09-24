from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_client
from .config import settings
from .costs import cost_control, money_string, quote_usage, record_usage
from .database import get_db
from .jobs import Job
from .media_lifecycle import meter_media
from .models import Artifact, BrandTemplate, BrandTemplateFile, Project, User, utc_now
from .runtime import job_queue, manager, media_storage
from .semantic_analysis import estimate_semantic_tokens


router = APIRouter(prefix="/api/jobs", tags=["editor"])
logger = logging.getLogger(__name__)


class EditablePart(BaseModel):
    index: int = Field(ge=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=4_000)

    @field_validator("title", "description", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def valid_interval(self) -> EditablePart:
        if self.end <= self.start:
            raise ValueError("La fin doit être postérieure au début.")
        return self


class AnalysisUpdate(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parts: list[EditablePart] = Field(min_length=1, max_length=500)


class SubtitleCue(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=1000)

    @field_validator("text", mode="before")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()


class SubtitleUpdate(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    track_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    audio_export_job_id: str = Field(min_length=32, max_length=32)
    name: str = Field(min_length=1, max_length=180)
    language: str = Field(min_length=2, max_length=20)
    font: str = Field(pattern=r"^(sans|serif|mono)$")
    font_size: int = Field(default=32, ge=12, le=96)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    position: str = Field(default="bottom", pattern=r"^(top|center|bottom)$")
    cues: list[SubtitleCue] = Field(min_length=1, max_length=10000)

    @field_validator("name", "language", mode="before")
    @classmethod
    def strip_subtitle_values(cls, value: str) -> str:
        return value.strip()


class SubtitleProofreadRequest(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audio_export_job_id: str = Field(min_length=32, max_length=32)
    subtitle_track_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    cost_confirmed: bool = False


class SemanticReanalysisRequest(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_confirmed: bool = False


class AudioExportRequest(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    part_indices: list[int] = Field(min_length=1, max_length=500)

    @field_validator("part_indices")
    @classmethod
    def valid_part_indices(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("Les numéros de partie doivent être positifs.")
        if len(set(values)) != len(values):
            raise ValueError("Une partie ne peut être sélectionnée qu'une fois.")
        return values


class VideoExportRequest(AudioExportRequest):
    include_subtitles: bool = False
    audio_export_job_id: str | None = Field(default=None, min_length=32, max_length=32)
    subtitle_track_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    template_id: str = Field(min_length=32, max_length=32)
    template_version: int = Field(ge=1)
    image_job_id: str | None = Field(default=None, min_length=32, max_length=32)
    output_format: str = Field(pattern=r"^(16:9|1:1|9:16)$")
    title: str = Field(default="", max_length=300)
    speaker: str = Field(default="", max_length=180)
    date: str = Field(default="", max_length=80)
    episode: str = Field(default="", max_length=80)

    @field_validator("title", "speaker", "date", "episode", mode="before")
    @classmethod
    def strip_values(cls, value: str) -> str:
        return value.strip()


class ImageGenerationRequest(BaseModel):
    template_id: str = Field(min_length=32, max_length=32)
    template_version: int = Field(ge=1)
    output_format: str = Field(pattern=r"^(16:9|1:1|9:16)$")
    title: str = Field(min_length=1, max_length=300)
    prompt: str = Field(default="", max_length=1_200)
    cost_confirmed: bool = False

    @field_validator("title", "prompt", mode="before")
    @classmethod
    def strip_prompt_values(cls, value: str) -> str:
        return value.strip()


class ArchiveExportRequest(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    video_job_id: str | None = Field(default=None, min_length=32, max_length=32)
    audio_export_job_id: str | None = Field(default=None, min_length=32, max_length=32)


@router.get("/{job_id}/exports/recovery")
def latest_recovery_archive(job_id: str, user: User = Depends(require_client), db: Session = Depends(get_db)) -> dict[str, Any] | None:
    source = owned_completed_job(user.id, job_id)
    for child in manager.list_for_user(user.id, source.project_id):
        if child.tool != "archive_export" or not child.options.get("automatic") or child.options.get("source_job_id") != source.id or child.state != "completed":
            continue
        artifact = db.scalar(select(Artifact).where(Artifact.job_id == child.id, Artifact.user_id == user.id, Artifact.kind == "archive"))
        if artifact and artifact.storage_key and (artifact.expires_at is None or artifact.expires_at > utc_now()):
            return child.public()
    return None


def owned_completed_job(user_id: str, job_id: str) -> Job:
    job = manager.get(user_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Traitement introuvable.")
    if job.state != "completed":
        raise HTTPException(
            status_code=409,
            detail="L'analyse ne peut être modifiée qu'après la fin du traitement.",
        )
    return job


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Le fichier d'analyse est illisible.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise HTTPException(status_code=422, detail="Le format de l'analyse est invalide.")
    if not isinstance(payload.get("parts"), list):
        raise HTTPException(status_code=422, detail="Le format des parties est invalide.")
    return payload


def analysis_artifact(
    db: Session,
    job: Job,
    user_id: str,
    *,
    for_update: bool = False,
) -> Artifact:
    statement = select(Artifact).where(
            Artifact.job_id == job.id,
            Artifact.user_id == user_id,
            Artifact.kind == "analysis",
        )
    if for_update:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None or not row.storage_key:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")
    return row


def child_artifact(
    db: Session,
    source_job: Job,
    user_id: str,
    child_job_id: str,
    tool: str,
    kind: str,
) -> tuple[Job, Artifact]:
    child = manager.get(user_id, child_job_id)
    if (
        child is None
        or child.project_id != source_job.project_id
        or child.tool != tool
        or child.options.get("source_job_id") != source_job.id
    ):
        raise HTTPException(status_code=404, detail="Export lié introuvable.")
    if child.state != "completed":
        raise HTTPException(status_code=409, detail="L'export lié n'est pas terminé.")
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.job_id == child.id,
            Artifact.user_id == user_id,
            Artifact.project_id == source_job.project_id,
            Artifact.kind == kind,
        )
    )
    if artifact is None or not artifact.storage_key:
        raise HTTPException(status_code=404, detail="Fichier exporté introuvable.")
    return child, artifact


def archive_reference(artifact: Artifact) -> dict[str, str | None]:
    return {
        "job_id": artifact.job_id,
        "artifact_kind": artifact.kind,
        "checksum_sha256": artifact.checksum_sha256,
    }


def queue_auto_archive(db: Session, source_job: Job, *, audio_job_id: str | None = None, video_job_id: str | None = None) -> None:
    """Best-effort server-side checkpoint; a failed backup never invalidates the completed stage."""
    if source_job.execution_backend != "worker" or source_job.state != "completed":
        return
    analysis = db.scalar(select(Artifact).where(Artifact.job_id == source_job.id, Artifact.kind == "analysis"))
    audio = db.scalar(select(Artifact).where(Artifact.job_id == source_job.id, Artifact.kind == "audio"))
    project = db.get(Project, source_job.project_id)
    if not analysis or not audio or not project or not analysis.checksum_sha256:
        return
    snapshot_kind = f"analysis_snapshot_{analysis.checksum_sha256[:16]}"
    snapshot = db.scalar(select(Artifact).where(Artifact.job_id == source_job.id, Artifact.kind == snapshot_kind, Artifact.checksum_sha256 == analysis.checksum_sha256))
    if snapshot is None:
        settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
            path = Path(temporary) / "analysis.json"
            media_storage.download_file(analysis.storage_key, path)
            if sha256_file(path) != analysis.checksum_sha256:
                logger.warning("Analysis changed before automatic archive snapshot for %s", source_job.id)
                return
            snapshot_key = f"users/{source_job.user_id}/projects/{source_job.project_id}/jobs/{source_job.id}/analysis-snapshots/{analysis.checksum_sha256}.json"
            media_storage.upload_file(snapshot_key, path, "application/json")
            snapshot = Artifact(user_id=source_job.user_id, project_id=source_job.project_id, job_id=source_job.id, kind=snapshot_kind, mime_type="application/json", size_bytes=path.stat().st_size, storage_key=snapshot_key, checksum_sha256=analysis.checksum_sha256, storage_metered_at=utc_now(), expires_at=utc_now() + timedelta(seconds=settings.media_retention_seconds))
            db.add(snapshot)
            db.commit()
    references = {"analysis": archive_reference(snapshot), "audio": archive_reference(audio)}
    if audio_job_id or video_job_id:
        child_id = video_job_id or audio_job_id
        selected = db.scalar(select(Artifact).where(Artifact.job_id == child_id, Artifact.kind == "selection_audio"))
        if selected:
            references["selection_audio"] = archive_reference(selected)
        if video_job_id:
            for artifact_kind in ("cover", "video"):
                item = db.scalar(select(Artifact).where(Artifact.job_id == child_id, Artifact.kind == artifact_kind))
                if item:
                    references[artifact_kind] = archive_reference(item)
    signature = {"source_job_id": source_job.id, "analysis_checksum": analysis.checksum_sha256, "archive_files": references}
    if any(item.tool == "archive_export" and item.options.get("automatic") and all(item.options.get(key) == value for key, value in signature.items()) and item.state not in {"failed", "cancelled", "expired"} for item in manager.list_for_user(source_job.user_id, source_job.project_id)):
        return
    child = manager.create(source_job.user_id, source_job.project_id, "cours-auto.dars", "", source_job.language, settings.whisper_cpu_threads, execution_backend="worker", allocate_workspace=False, tool="archive_export", options={**signature, "project_title": project.title, "automatic": True})
    try:
        job_queue.enqueue(child.id)
    except Exception:
        logger.exception("Automatic archive enqueue failed for %s", child.id)


def inline_analysis_path(job: Job) -> Path:
    value = job.artifacts.get("analysis")
    if not value:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")
    path = Path(value)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="L'analyse a expiré.")
    return path


def duration_for(job: Job, payload: dict[str, Any]) -> float:
    measured = job.metrics.get("duration_seconds")
    if isinstance(measured, (int, float)) and measured > 0:
        return float(measured)
    ends = [segment.get("end") for segment in payload["segments"] if isinstance(segment, dict)]
    numeric_ends = [float(value) for value in ends if isinstance(value, (int, float))]
    return max(numeric_ends, default=0.0)


def validate_export_ranges(job: Job, payload: dict[str, Any], ranges: list[list[float]]) -> None:
    duration = duration_for(job, payload)
    if any(start < 0 or end <= start for start, end in ranges):
        raise HTTPException(status_code=422, detail="Les timestamps sélectionnés sont invalides.")
    if duration > 0 and any(end > duration + 1.0 for _, end in ranges):
        raise HTTPException(
            status_code=422,
            detail="Une partie sélectionnée dépasse la durée de l'audio source. Vérifiez l'archive ou corrigez le chapitrage.",
        )


def response_payload(
    job: Job,
    payload: dict[str, Any],
    checksum: str,
) -> dict[str, Any]:
    subtitles = payload.get("subtitles") or {
        "language": job.language or "fr",
        "font": "sans",
        "color": "#ffffff",
        "cues": [
            {
                "start": item["start"], "end": item["end"], "text": item["text"]
            }
            for item in payload["segments"]
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        ],
    }
    subtitles = {
        **subtitles,
        "font_size": int(subtitles.get("font_size", 32)),
        "position": subtitles.get("position", "bottom"),
    }
    subtitle_tracks = [
        {
            **track,
            "font_size": int(track.get("font_size", 32)),
            "position": track.get("position", "bottom"),
        }
        for track in (payload.get("subtitle_tracks") or [])
        if isinstance(track, dict)
    ]
    return {
        "schema": payload.get("schema", 3),
        "audio_name": payload.get("audio_name", "audio"),
        "duration_seconds": duration_for(job, payload),
        "checksum_sha256": checksum,
        "segments": payload["segments"],
        "parts": payload["parts"],
        "subtitle_tracks": subtitle_tracks,
        "subtitles": subtitles,
    }


@router.put("/{job_id}/analysis/subtitles")
def update_subtitles(
    job_id: str,
    update: SubtitleUpdate,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = owned_completed_job(user.id, job_id)
    if job.execution_backend != "worker":
        raise HTTPException(status_code=409, detail="Ce cours ne permet pas les sous-titres éditables.")
    row = analysis_artifact(db, job, user.id, for_update=True)
    if row.checksum_sha256 != update.checksum_sha256:
        raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")
    audio_export, _ = child_artifact(
        db, job, user.id, update.audio_export_job_id, "audio_selection", "selection_audio"
    )
    raw_ranges = audio_export.options.get("ranges", [])
    ranges = [
        (float(item[0]), float(item[1]))
        for item in raw_ranges
        if isinstance(item, list) and len(item) == 2
    ]
    if not ranges or len(ranges) != len(raw_ranges):
        raise HTTPException(status_code=422, detail="La sélection audio est invalide.")
    previous_end = 0.0
    for cue in update.cues:
        if (
            cue.end <= cue.start
            or cue.start < previous_end - 0.1
            or not any(cue.end > start and cue.start < end for start, end in ranges)
        ):
            raise HTTPException(status_code=422, detail="Les horodatages des sous-titres sont invalides.")
        previous_end = cue.end
    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        path = Path(temporary) / "analysis.json"
        media_storage.download_file(row.storage_key, path)
        if sha256_file(path) != update.checksum_sha256:
            raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")
        payload = load_payload(path)
        tracks = payload.get("subtitle_tracks")
        if not isinstance(tracks, list):
            tracks = []
        existing = next(
            (item for item in tracks if isinstance(item, dict) and item.get("id") == update.track_id),
            None,
        )
        if existing and existing.get("audio_export_job_id") != audio_export.id:
            raise HTTPException(
                status_code=409,
                detail="Cette piste appartient à un autre audio.",
            )
        now = utc_now().isoformat(timespec="seconds")
        track = {
            "id": update.track_id,
            "audio_export_job_id": audio_export.id,
            "name": update.name.strip(),
            "language": update.language,
            "font": update.font,
            "font_size": update.font_size,
            "color": update.color,
            "position": update.position,
            "cues": [cue.model_dump() for cue in update.cues],
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        if existing is None:
            tracks.append(track)
        else:
            tracks[tracks.index(existing)] = track
        payload["subtitle_tracks"] = tracks
        write_payload(path, payload)
        checksum = sha256_file(path)
        meter_media(db, row)
        media_storage.upload_file(row.storage_key, path, "application/json")
        row.size_bytes = path.stat().st_size
        row.checksum_sha256 = checksum
        row.storage_metered_at = utc_now()
        db.commit()
        queue_auto_archive(db, job)
        return response_payload(job, payload, checksum)


def subtitle_proofread_quote(db: Session, duration: float) -> tuple[int, int, int, str]:
    input_tokens = max(800, round(duration * 5))
    output_tokens = max(800, round(duration * 5))
    prices = [quote_usage(db, provider="openai", service="content_analysis", model=settings.semantic_analysis_model, quantity=count, unit=unit) for count, unit in ((input_tokens, "input_token"), (output_tokens, "output_token"))]
    return input_tokens, output_tokens, sum(price.amount_nanos for price in prices), prices[0].currency


@router.get("/{job_id}/analysis/subtitles/proofread-quote")
def quote_subtitle_proofread(
    job_id: str,
    audio_export_job_id: str = Query(min_length=32, max_length=32),
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source = owned_completed_job(user.id, job_id)
    if source.execution_backend != "worker" or settings.semantic_analysis_backend != "openai":
        raise HTTPException(status_code=409, detail="La correction cloud est indisponible.")
    audio_export, _ = child_artifact(
        db, source, user.id, audio_export_job_id, "audio_selection", "selection_audio"
    )
    duration = float(audio_export.metrics.get("duration_seconds", 0) or 0)
    input_tokens, output_tokens, amount_nanos, currency = subtitle_proofread_quote(db, duration)
    return {"model": settings.semantic_analysis_model, "estimated_input_tokens": input_tokens, "estimated_output_tokens": output_tokens, "amount": money_string(amount_nanos), "currency": currency}


@router.post("/{job_id}/analysis/subtitles/proofread", status_code=202)
def create_subtitle_proofread(job_id: str, request: SubtitleProofreadRequest, user: User = Depends(require_client), db: Session = Depends(get_db)) -> dict[str, Any]:
    source = owned_completed_job(user.id, job_id)
    if source.execution_backend != "worker" or settings.semantic_analysis_backend != "openai":
        raise HTTPException(status_code=409, detail="La correction cloud est indisponible.")
    analysis = analysis_artifact(db, source, user.id, for_update=True)
    if analysis.checksum_sha256 != request.checksum_sha256:
        raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")
    audio_export, _ = child_artifact(
        db,
        source,
        user.id,
        request.audio_export_job_id,
        "audio_selection",
        "selection_audio",
    )
    part_indices = list(audio_export.options.get("part_indices", []))
    ranges = list(audio_export.options.get("ranges", []))
    if not part_indices or not ranges:
        raise HTTPException(status_code=422, detail="L'audio sélectionné ne contient aucune partie.")
    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        analysis_path = Path(temporary) / "analysis.json"
        media_storage.download_file(analysis.storage_key, analysis_path)
        if sha256_file(analysis_path) != request.checksum_sha256:
            raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")
        payload = load_payload(analysis_path)
    track = next(
        (
            item for item in payload.get("subtitle_tracks", [])
            if isinstance(item, dict)
            and item.get("id") == request.subtitle_track_id
            and item.get("audio_export_job_id") == audio_export.id
        ),
        None,
    )
    if track is None:
        raise HTTPException(status_code=404, detail="Piste de sous-titres introuvable.")
    previous = [item for item in manager.list_for_user(user.id, source.project_id) if item.tool == "subtitle_proofread" and item.options.get("source_job_id") == source.id]
    identical = next((item for item in previous if item.options.get("analysis_checksum") == request.checksum_sha256 and item.options.get("audio_export_job_id") == audio_export.id and item.options.get("subtitle_track_id") == request.subtitle_track_id and item.state not in {"failed", "cancelled", "expired"}), None)
    if identical:
        return identical.public()
    if any(item.state not in {"completed", "failed", "cancelled", "expired"} for item in previous):
        raise HTTPException(status_code=409, detail="Une correction est déjà en cours.")
    quantities = subtitle_proofread_quote(db, float(audio_export.metrics.get("duration_seconds", 0) or 0))
    input_tokens, output_tokens, amount_nanos, _currency = quantities
    control = cost_control(db, user_id=user.id, proposed_amount_nanos=amount_nanos, lock_policy=True)
    if control.requires_confirmation and not request.cost_confirmed:
        raise HTTPException(status_code=409, detail="Cette correction dépasse un seuil financier et doit être confirmée.")
    child = manager.create(user.id, source.project_id, "subtitle-suggestions.json", settings.semantic_analysis_model, source.language, settings.whisper_cpu_threads, execution_backend="worker", allocate_workspace=False, tool="subtitle_proofread", options={"source_job_id": source.id, "analysis_storage_key": analysis.storage_key, "analysis_checksum": request.checksum_sha256, "audio_export_job_id": audio_export.id, "subtitle_track_id": request.subtitle_track_id, "part_indices": part_indices, "ranges": ranges})
    try:
        for quantity, unit in ((input_tokens, "input_token"), (output_tokens, "output_token")):
            record_usage(db, user_id=user.id, project_id=source.project_id, job_id=child.id, provider="openai", service="content_analysis", model=settings.semantic_analysis_model, quantity=quantity, unit=unit, status="estimated", idempotency_key=f"content-analysis:{child.id}:estimate:{unit}", details={"purpose": "subtitle_proofread", "cost_confirmed": request.cost_confirmed})
        db.commit()
    except Exception:
        db.rollback()
        manager.delete(child)
        raise
    try:
        job_queue.enqueue(child.id)
    except Exception:
        pass
    return child.public()


@router.get("/{job_id}/analysis/subtitles/proofread/{child_id}")
def get_subtitle_suggestions(job_id: str, child_id: str, user: User = Depends(require_client), db: Session = Depends(get_db)) -> dict[str, Any]:
    source = owned_completed_job(user.id, job_id)
    child, artifact = child_artifact(db, source, user.id, child_id, "subtitle_proofread", "subtitle_suggestions")
    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        path = Path(temporary) / "suggestions.json"
        media_storage.download_file(artifact.storage_key, path)
        if artifact.checksum_sha256 != sha256_file(path):
            raise HTTPException(status_code=502, detail="Suggestions corrompues.")
        return json.loads(path.read_text(encoding="utf-8"))


def validated_parts(
    update: AnalysisUpdate,
    payload: dict[str, Any],
    duration: float,
) -> list[dict[str, Any]]:
    ordered = sorted(update.parts, key=lambda part: (part.start, part.end))
    previous_end = 0.0
    result: list[dict[str, Any]] = []
    for position, part in enumerate(ordered, start=1):
        if part.start < previous_end - 0.001:
            raise HTTPException(status_code=422, detail="Les parties ne peuvent pas se chevaucher.")
        if duration > 0 and part.end > duration + 0.1:
            raise HTTPException(
                status_code=422,
                detail="Une partie dépasse la durée de l'audio.",
            )
        transcript = " ".join(
            str(segment.get("text", "")).strip()
            for segment in payload["segments"]
            if isinstance(segment, dict)
            and isinstance(segment.get("start"), (int, float))
            and isinstance(segment.get("end"), (int, float))
            and float(segment["end"]) > part.start
            and float(segment["start"]) < part.end
            and str(segment.get("text", "")).strip()
        )
        result.append(
            {
                "index": position,
                "start": round(part.start, 3),
                "end": round(part.end, 3),
                "title": part.title,
                "description": part.description,
                "transcript": transcript,
            }
        )
        previous_end = part.end
    return result


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def semantic_quote(db: Session, duration_seconds: float) -> tuple[int, int, int, str]:
    input_tokens, output_tokens = estimate_semantic_tokens(duration_seconds)
    input_quote = quote_usage(
        db,
        provider="openai",
        service="content_analysis",
        model=settings.semantic_analysis_model,
        quantity=input_tokens,
        unit="input_token",
    )
    output_quote = quote_usage(
        db,
        provider="openai",
        service="content_analysis",
        model=settings.semantic_analysis_model,
        quantity=output_tokens,
        unit="output_token",
    )
    return (
        input_tokens,
        output_tokens,
        input_quote.amount_nanos + output_quote.amount_nanos,
        input_quote.currency,
    )


def image_generation_quote(db: Session) -> tuple[dict[str, int], int, str]:
    # Conservative medium-quality estimate used for budget approval. The worker
    # replaces it with the exact token usage returned by the provider.
    quantities = {
        "input_text_token": 500,
        "input_image_token": 2_000,
        "output_image_token": 3_000,
    }
    quotes = [
        quote_usage(
            db,
            provider="openai",
            service="image_generation",
            model=settings.image_generation_model,
            quantity=quantity,
            unit=unit,
        )
        for unit, quantity in quantities.items()
    ]
    return quantities, sum(item.amount_nanos for item in quotes), quotes[0].currency


@router.get("/{job_id}/analysis")
def get_analysis(
    job_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = owned_completed_job(user.id, job_id)
    if job.execution_backend != "worker":
        path = inline_analysis_path(job)
        payload = load_payload(path)
        return response_payload(job, payload, sha256_file(path))

    row = analysis_artifact(db, job, user.id)
    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        path = Path(temporary) / "analysis.json"
        try:
            media_storage.download_file(row.storage_key, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=410, detail="L'analyse a expiré.") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Lecture de l'analyse impossible.") from exc
        payload = load_payload(path)
        return response_payload(job, payload, sha256_file(path))


@router.put("/{job_id}/analysis")
def update_analysis(
    job_id: str,
    update: AnalysisUpdate,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = owned_completed_job(user.id, job_id)
    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    if job.execution_backend != "worker":
        path = inline_analysis_path(job)
        payload = load_payload(path)
        if sha256_file(path) != update.checksum_sha256:
            raise HTTPException(
                status_code=409,
                detail="Cette analyse a été modifiée ailleurs. Rechargez-la avant de continuer.",
            )
        payload["parts"] = validated_parts(update, payload, duration_for(job, payload))
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".analysis-",
            suffix=".json",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            write_payload(temporary_path, payload)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
        checksum = sha256_file(path)
        return response_payload(job, payload, checksum)

    row = analysis_artifact(db, job, user.id, for_update=True)
    if row.checksum_sha256 and row.checksum_sha256 != update.checksum_sha256:
        raise HTTPException(
            status_code=409,
            detail="Cette analyse a été modifiée ailleurs. Rechargez-la avant de continuer.",
        )
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        path = Path(temporary) / "analysis.json"
        try:
            media_storage.download_file(row.storage_key, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=410, detail="L'analyse a expiré.") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Lecture de l'analyse impossible.") from exc
        current_checksum = sha256_file(path)
        if current_checksum != update.checksum_sha256:
            raise HTTPException(
                status_code=409,
                detail="Cette analyse a été modifiée ailleurs. Rechargez-la avant de continuer.",
            )
        payload = load_payload(path)
        payload["parts"] = validated_parts(update, payload, duration_for(job, payload))
        write_payload(path, payload)
        checksum = sha256_file(path)
        meter_media(db, row)
        try:
            media_storage.upload_file(row.storage_key, path, "application/json")
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=502, detail="La sauvegarde a échoué.") from exc
        row.size_bytes = path.stat().st_size
        row.checksum_sha256 = checksum
        row.storage_metered_at = utc_now()
        db.commit()
        queue_auto_archive(db, job)
        return response_payload(job, payload, checksum)


@router.get("/{job_id}/analysis/reanalysis-quote")
def quote_semantic_reanalysis(
    job_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = owned_completed_job(user.id, job_id)
    if settings.semantic_analysis_backend != "openai":
        raise HTTPException(status_code=409, detail="L’analyse sémantique cloud est désactivée.")
    duration = float(job.metrics.get("duration_seconds", 0) or 0)
    if duration <= 0:
        raise HTTPException(status_code=422, detail="La durée du cours est inconnue.")
    input_tokens, output_tokens, amount_nanos, currency = semantic_quote(db, duration)
    control = cost_control(db, user_id=user.id, proposed_amount_nanos=amount_nanos)
    return {
        "provider": "openai",
        "model": settings.semantic_analysis_model,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "currency": currency,
        "amount": money_string(amount_nanos),
        "requires_confirmation": control.requires_confirmation,
        "confirmation_reasons": list(control.confirmation_reasons),
        "monthly_projected": money_string(control.projected_nanos),
        "monthly_budget": money_string(control.monthly_budget_nanos),
        "budget_state": control.state,
    }


@router.post("/{job_id}/analysis/reanalyze", status_code=202)
def create_semantic_reanalysis(
    job_id: str,
    request: SemanticReanalysisRequest,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source_job = owned_completed_job(user.id, job_id)
    if source_job.tool != "audio_pipeline":
        raise HTTPException(status_code=422, detail="Ce traitement ne contient pas de cours source.")
    if source_job.execution_backend != "worker":
        raise HTTPException(status_code=409, detail="La réanalyse nécessite le worker cloud.")
    if settings.semantic_analysis_backend != "openai":
        raise HTTPException(status_code=409, detail="L’analyse sémantique cloud est désactivée.")
    analysis = analysis_artifact(db, source_job, user.id, for_update=True)
    if analysis.checksum_sha256 and analysis.checksum_sha256 != request.checksum_sha256:
        raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")

    previous = [
        item
        for item in manager.list_for_user(user.id, source_job.project_id)
        if item.tool == "semantic_reanalysis"
        and item.options.get("source_job_id") == source_job.id
    ]
    identical = next(
        (
            item
            for item in previous
            if item.options.get("analysis_checksum") == request.checksum_sha256
            and item.state not in {"failed", "cancelled", "expired"}
        ),
        None,
    )
    if identical is not None:
        return identical.public()
    if any(item.state not in {"completed", "failed", "cancelled", "expired"} for item in previous):
        raise HTTPException(status_code=409, detail="Une réanalyse est déjà en cours.")

    duration = float(source_job.metrics.get("duration_seconds", 0) or 0)
    if duration <= 0:
        raise HTTPException(status_code=422, detail="La durée du cours est inconnue.")
    input_tokens, output_tokens, amount_nanos, _currency = semantic_quote(db, duration)
    control = cost_control(
        db,
        user_id=user.id,
        proposed_amount_nanos=amount_nanos,
        lock_policy=True,
    )
    if control.requires_confirmation and not request.cost_confirmed:
        raise HTTPException(
            status_code=409,
            detail="Cette réanalyse dépasse un seuil financier et doit être confirmée.",
        )

    child = manager.create(
        user.id,
        source_job.project_id,
        "analysis.json",
        settings.semantic_analysis_model,
        source_job.language,
        settings.whisper_cpu_threads,
        execution_backend="worker",
        allocate_workspace=False,
        tool="semantic_reanalysis",
        options={
            "source_job_id": source_job.id,
            "analysis_storage_key": analysis.storage_key,
            "analysis_checksum": request.checksum_sha256,
        },
    )
    try:
        for quantity, unit in (
            (input_tokens, "input_token"),
            (output_tokens, "output_token"),
        ):
            record_usage(
                db,
                user_id=user.id,
                project_id=source_job.project_id,
                job_id=child.id,
                provider="openai",
                service="content_analysis",
                model=settings.semantic_analysis_model,
                quantity=quantity,
                unit=unit,
                status="estimated",
                idempotency_key=f"content-analysis:{child.id}:estimate:{unit}",
                details={
                    "source": "existing_transcript_duration",
                    "source_job_id": source_job.id,
                    "cost_confirmed": request.cost_confirmed,
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        manager.delete(child)
        raise
    try:
        job_queue.enqueue(child.id)
    except Exception:
        pass
    return child.public()


@router.get("/{job_id}/images/generation-quote")
def quote_image_generation(
    job_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source_job = owned_completed_job(user.id, job_id)
    if source_job.tool != "audio_pipeline" or source_job.execution_backend != "worker":
        raise HTTPException(status_code=409, detail="Ce cours ne permet pas la génération cloud.")
    quantities, amount_nanos, currency = image_generation_quote(db)
    control = cost_control(db, user_id=user.id, proposed_amount_nanos=amount_nanos)
    return {
        "provider": "openai",
        "model": settings.image_generation_model,
        "quality": settings.image_generation_quality,
        "estimated_tokens": quantities,
        "currency": currency,
        "amount": money_string(amount_nanos),
        "requires_confirmation": control.requires_confirmation,
        "confirmation_reasons": list(control.confirmation_reasons),
        "monthly_projected": money_string(control.projected_nanos),
        "monthly_budget": money_string(control.monthly_budget_nanos),
        "budget_state": control.state,
    }


@router.post("/{job_id}/images/generate", status_code=202)
def create_image_generation(
    job_id: str,
    request: ImageGenerationRequest,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source_job = owned_completed_job(user.id, job_id)
    if source_job.tool != "audio_pipeline" or source_job.execution_backend != "worker":
        raise HTTPException(status_code=409, detail="Ce cours ne permet pas la génération cloud.")
    template = db.scalar(
        select(BrandTemplate).where(
            BrandTemplate.id == request.template_id,
            BrandTemplate.user_id == user.id,
            BrandTemplate.status == "ready",
        )
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Modèle visuel introuvable.")
    if template.version != request.template_version:
        raise HTTPException(status_code=409, detail="Le modèle visuel a changé. Rechargez-le.")
    reference = db.scalar(
        select(BrandTemplateFile).where(
            BrandTemplateFile.template_id == template.id,
            BrandTemplateFile.user_id == user.id,
            BrandTemplateFile.kind == "preview",
        )
    )
    if reference is None or not reference.storage_key:
        raise HTTPException(status_code=404, detail="Image de référence introuvable.")

    signature = {
        "source_job_id": source_job.id,
        "template_id": template.id,
        "template_version": template.version,
        "output_format": request.output_format,
        "title": request.title,
        "prompt": request.prompt,
    }
    previous = [
        item
        for item in manager.list_for_user(user.id, source_job.project_id)
        if item.tool == "image_generation"
        and item.options.get("source_job_id") == source_job.id
    ]
    identical = next(
        (
            item
            for item in previous
            if all(item.options.get(key) == value for key, value in signature.items())
            and item.state not in {"completed", "failed", "cancelled", "expired"}
        ),
        None,
    )
    if identical is not None:
        return identical.public()
    if any(item.state not in {"completed", "failed", "cancelled", "expired"} for item in previous):
        raise HTTPException(status_code=409, detail="Une image est déjà en cours de génération.")

    quantities, amount_nanos, _currency = image_generation_quote(db)
    control = cost_control(
        db,
        user_id=user.id,
        proposed_amount_nanos=amount_nanos,
        lock_policy=True,
    )
    if control.requires_confirmation and not request.cost_confirmed:
        raise HTTPException(
            status_code=409,
            detail="Cette génération dépasse un seuil financier et doit être confirmée.",
        )
    title_zones = [
        zone
        for zone in template.settings.get("zones", [])
        if isinstance(zone, dict) and zone.get("kind") == "title"
    ]
    if not title_zones:
        title_zones = [{
            "kind": "title",
            "x": 0.08,
            "y": 0.64,
            "width": 0.84,
            "height": 0.22,
            "font_scale": 0.06,
            "color": "#ffffff",
            "align": "left",
        }]
    child = manager.create(
        user.id,
        source_job.project_id,
        "image-generee.png",
        settings.image_generation_model,
        source_job.language,
        settings.whisper_cpu_threads,
        execution_backend="worker",
        allocate_workspace=False,
        tool="image_generation",
        options={
            **signature,
            "reference_storage_key": reference.storage_key,
            "reference_checksum": reference.checksum_sha256,
            "quality": settings.image_generation_quality,
            "template_zones": title_zones,
        },
    )
    try:
        for unit, quantity in quantities.items():
            record_usage(
                db,
                user_id=user.id,
                project_id=source_job.project_id,
                job_id=child.id,
                provider="openai",
                service="image_generation",
                model=settings.image_generation_model,
                quantity=quantity,
                unit=unit,
                status="estimated",
                idempotency_key=f"image-generation:{child.id}:estimate:{unit}",
                details={
                    "source": "reference_image_medium_quality",
                    "source_job_id": source_job.id,
                    "cost_confirmed": request.cost_confirmed,
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        manager.delete(child)
        raise
    try:
        job_queue.enqueue(child.id)
    except Exception:
        pass
    return child.public()


@router.post("/{job_id}/exports/audio", status_code=202)
def create_audio_export(
    job_id: str,
    request: AudioExportRequest,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source_job = owned_completed_job(user.id, job_id)
    if source_job.tool != "audio_pipeline":
        raise HTTPException(status_code=422, detail="Ce traitement ne contient pas de cours source.")
    if source_job.execution_backend != "worker":
        raise HTTPException(
            status_code=409,
            detail="L'export durable nécessite le mode worker cloud.",
        )

    audio = db.scalar(
        select(Artifact).where(
            Artifact.job_id == source_job.id,
            Artifact.user_id == user.id,
            Artifact.kind == "audio",
        )
    )
    if audio is None or not audio.storage_key:
        raise HTTPException(status_code=404, detail="Audio source introuvable.")

    analysis = analysis_artifact(db, source_job, user.id, for_update=True)
    if analysis.checksum_sha256 and analysis.checksum_sha256 != request.checksum_sha256:
        raise HTTPException(
            status_code=409,
            detail="L'analyse a changé. Enregistrez ou rechargez avant l'export.",
        )
    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        path = Path(temporary) / "analysis.json"
        try:
            media_storage.download_file(analysis.storage_key, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=410, detail="L'analyse a expiré.") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Lecture de l'analyse impossible.") from exc
        if sha256_file(path) != request.checksum_sha256:
            raise HTTPException(
                status_code=409,
                detail="L'analyse a changé. Enregistrez ou rechargez avant l'export.",
            )
        payload = load_payload(path)

    by_index = {
        int(part["index"]): part
        for part in payload["parts"]
        if isinstance(part, dict) and isinstance(part.get("index"), int)
    }
    if any(index not in by_index for index in request.part_indices):
        raise HTTPException(status_code=422, detail="Une partie sélectionnée n'existe plus.")
    selected = sorted(
        (by_index[index] for index in request.part_indices),
        key=lambda part: float(part["start"]),
    )
    canonical_indices = [int(part["index"]) for part in selected]
    ranges = [[float(part["start"]), float(part["end"])] for part in selected]
    validate_export_ranges(source_job, payload, ranges)

    previous_exports = [
        job
        for job in manager.list_for_user(user.id, source_job.project_id)
        if job.tool == "audio_selection"
        and job.options.get("source_job_id") == source_job.id
    ]
    identical = next(
        (
            job
            for job in previous_exports
            if job.options.get("analysis_checksum") == request.checksum_sha256
            and job.options.get("part_indices") == canonical_indices
            and job.state not in {"failed", "cancelled", "expired"}
        ),
        None,
    )
    if identical is not None:
        return identical.public()
    if any(job.state not in {"completed", "failed", "cancelled", "expired"} for job in previous_exports):
        raise HTTPException(
            status_code=409,
            detail="Un export audio est déjà en cours pour ce cours.",
        )

    child = manager.create(
        user.id,
        source_job.project_id,
        "selection-audio.wav",
        "",
        source_job.language,
        settings.whisper_cpu_threads,
        execution_backend="worker",
        allocate_workspace=False,
        tool="audio_selection",
        options={
            "source_job_id": source_job.id,
            "analysis_checksum": request.checksum_sha256,
            "part_indices": canonical_indices,
            "ranges": ranges,
            "title": " · ".join(str(part.get("title", "")) for part in selected),
        },
    )
    try:
        job_queue.enqueue(child.id)
    except Exception:
        pass
    return child.public()


@router.post("/{job_id}/exports/video", status_code=202)
def create_video_export(
    job_id: str,
    request: VideoExportRequest,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source_job = owned_completed_job(user.id, job_id)
    if source_job.tool != "audio_pipeline" or source_job.execution_backend != "worker":
        raise HTTPException(status_code=409, detail="Ce cours ne permet pas un rendu cloud durable.")
    template = db.scalar(
        select(BrandTemplate).where(
            BrandTemplate.id == request.template_id,
            BrandTemplate.user_id == user.id,
            BrandTemplate.status == "ready",
        )
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template introuvable.")
    if template.version != request.template_version:
        raise HTTPException(status_code=409, detail="Le template a changé. Rechargez-le.")
    template_source = db.scalar(
        select(BrandTemplateFile).where(
            BrandTemplateFile.template_id == template.id,
            BrandTemplateFile.user_id == user.id,
            BrandTemplateFile.kind == "source",
        )
    )
    generated_image = None
    generated_job = None
    if request.image_job_id:
        generated_job = owned_completed_job(user.id, request.image_job_id)
        if (
            generated_job.tool != "image_generation"
            or generated_job.project_id != source_job.project_id
            or generated_job.options.get("source_job_id") != source_job.id
            or generated_job.options.get("template_id") != template.id
        ):
            raise HTTPException(status_code=422, detail="L'image générée ne correspond pas à ce cours.")
        generated_image = db.scalar(
            select(Artifact).where(
                Artifact.job_id == generated_job.id,
                Artifact.user_id == user.id,
                Artifact.kind == "generated_image",
            )
        )
    audio = db.scalar(
        select(Artifact).where(
            Artifact.job_id == source_job.id,
            Artifact.user_id == user.id,
            Artifact.kind == "audio",
        )
    )
    if (
        template_source is None
        or audio is None
        or not audio.storage_key
        or (request.image_job_id and (generated_image is None or not generated_image.storage_key))
    ):
        raise HTTPException(status_code=404, detail="Un média nécessaire au rendu est introuvable.")

    analysis = analysis_artifact(db, source_job, user.id, for_update=True)
    if analysis.checksum_sha256 and analysis.checksum_sha256 != request.checksum_sha256:
        raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")
    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        analysis_path = Path(temporary) / "analysis.json"
        try:
            media_storage.download_file(analysis.storage_key, analysis_path)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Lecture de l'analyse impossible.") from exc
        if sha256_file(analysis_path) != request.checksum_sha256:
            raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")
        analysis_payload = load_payload(analysis_path)

    by_index = {
        int(part["index"]): part
        for part in analysis_payload["parts"]
        if isinstance(part, dict) and isinstance(part.get("index"), int)
    }
    if any(index not in by_index for index in request.part_indices):
        raise HTTPException(status_code=422, detail="Une partie sélectionnée n'existe plus.")
    selected = sorted(
        (by_index[index] for index in request.part_indices),
        key=lambda part: float(part["start"]),
    )
    canonical_indices = [int(part["index"]) for part in selected]
    ranges = [[float(part["start"]), float(part["end"])] for part in selected]
    validate_export_ranges(source_job, analysis_payload, ranges)
    values = {
        "title": request.title or " · ".join(str(part.get("title", "")) for part in selected),
        "speaker": request.speaker,
        "date": request.date,
        "episode": request.episode,
    }
    subtitle_payload = None
    subtitle_track_id = None
    audio_export_job_id = None
    if request.include_subtitles:
        if not request.audio_export_job_id or not request.subtitle_track_id:
            raise HTTPException(
                status_code=422,
                detail="Choisissez une piste de sous-titres pour cet audio.",
            )
        audio_export, _ = child_artifact(
            db,
            source_job,
            user.id,
            request.audio_export_job_id,
            "audio_selection",
            "selection_audio",
        )
        audio_indices = sorted(int(index) for index in audio_export.options.get("part_indices", []))
        if audio_indices != sorted(canonical_indices):
            raise HTTPException(
                status_code=422,
                detail="La piste de sous-titres ne correspond pas à l'audio de cette vidéo.",
            )
        track = next(
            (
                item
                for item in analysis_payload.get("subtitle_tracks", [])
                if isinstance(item, dict)
                and item.get("id") == request.subtitle_track_id
                and item.get("audio_export_job_id") == audio_export.id
            ),
            None,
        )
        if track is None:
            raise HTTPException(status_code=404, detail="Piste de sous-titres introuvable.")
        subtitle_payload = {
            "language": track.get("language") or source_job.language or "fr",
            "font": track.get("font") or "sans",
            "font_size": int(track.get("font_size", 32)),
            "color": track.get("color") or "#ffffff",
            "position": track.get("position") or "bottom",
            "cues": track.get("cues") or [],
        }
        if not subtitle_payload["cues"]:
            raise HTTPException(status_code=422, detail="Cette piste ne contient aucun sous-titre.")
        subtitle_track_id = request.subtitle_track_id
        audio_export_job_id = audio_export.id
    signature = {
        "source_job_id": source_job.id,
        "analysis_checksum": request.checksum_sha256,
        "part_indices": canonical_indices,
        "template_id": template.id,
        "template_version": template.version,
        "image_job_id": request.image_job_id,
        "output_format": request.output_format,
        "values": values,
        "include_subtitles": request.include_subtitles,
        "audio_export_job_id": audio_export_job_id,
        "subtitle_track_id": subtitle_track_id,
    }
    previous = [
        job
        for job in manager.list_for_user(user.id, source_job.project_id)
        if job.tool == "video_render" and job.options.get("source_job_id") == source_job.id
    ]
    identical = next(
        (
            job
            for job in previous
            if all(job.options.get(key) == value for key, value in signature.items())
            and job.state not in {"failed", "cancelled", "expired"}
        ),
        None,
    )
    if identical:
        return identical.public()
    if any(job.state not in {"completed", "failed", "cancelled", "expired"} for job in previous):
        raise HTTPException(status_code=409, detail="Un rendu vidéo est déjà en cours.")

    child = manager.create(
        user.id,
        source_job.project_id,
        "video.mp4",
        "",
        source_job.language,
        settings.whisper_cpu_threads,
        execution_backend="worker",
        allocate_workspace=False,
        tool="video_render",
        options={
            **signature,
            "ranges": ranges,
            "subtitles": subtitle_payload,
            "template_source_key": (
                generated_image.storage_key if generated_image else template_source.storage_key
            ),
            "template_source_checksum": (
                generated_image.checksum_sha256 if generated_image else template_source.checksum_sha256
            ),
            "template_source_kind": "image" if generated_image else template.source_kind,
            "template_usage_mode": "static_frame" if generated_image else template.usage_mode,
            "template_frame_seconds": float(template.settings.get("frame_seconds", 0)),
            "template_zones": (
                list(generated_job.options.get("template_zones", []))
                if generated_job
                else []
            ),
        },
    )
    try:
        job_queue.enqueue(child.id)
    except Exception:
        pass
    return child.public()


@router.post("/{job_id}/exports/archive", status_code=202)
def create_archive_export(
    job_id: str,
    request: ArchiveExportRequest,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source_job = owned_completed_job(user.id, job_id)
    if source_job.tool != "audio_pipeline" or source_job.execution_backend != "worker":
        raise HTTPException(status_code=409, detail="Ce cours ne peut pas être archivé.")

    project = db.scalar(
        select(Project).where(Project.id == source_job.project_id, Project.user_id == user.id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    analysis = analysis_artifact(db, source_job, user.id, for_update=True)
    if analysis.checksum_sha256 and analysis.checksum_sha256 != request.checksum_sha256:
        raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")

    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.workspace_root) as temporary:
        path = Path(temporary) / "analysis.json"
        try:
            media_storage.download_file(analysis.storage_key, path)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Lecture de l'analyse impossible.") from exc
        if sha256_file(path) != request.checksum_sha256:
            raise HTTPException(status_code=409, detail="L'analyse a changé. Rechargez-la.")
        load_payload(path)

    source_audio = db.scalar(
        select(Artifact).where(
            Artifact.job_id == source_job.id,
            Artifact.user_id == user.id,
            Artifact.kind == "audio",
        )
    )
    if source_audio is None or not source_audio.storage_key:
        raise HTTPException(status_code=404, detail="Audio source introuvable.")

    references: dict[str, dict[str, str | None]] = {
        "analysis": archive_reference(analysis),
        "audio": archive_reference(source_audio),
    }
    render_snapshot: dict[str, Any] | None = None
    if request.audio_export_job_id:
        _, selected_audio = child_artifact(
            db,
            source_job,
            user.id,
            request.audio_export_job_id,
            "audio_selection",
            "selection_audio",
        )
        references["selection_audio"] = archive_reference(selected_audio)

    if request.video_job_id:
        video_job, video = child_artifact(
            db, source_job, user.id, request.video_job_id, "video_render", "video"
        )
        _, cover = child_artifact(
            db, source_job, user.id, request.video_job_id, "video_render", "cover"
        )
        _, rendered_audio = child_artifact(
            db,
            source_job,
            user.id,
            request.video_job_id,
            "video_render",
            "selection_audio",
        )
        references.update({
            "selection_audio": archive_reference(rendered_audio),
            "cover": archive_reference(cover),
            "video": archive_reference(video),
        })
        render_snapshot = {
            key: video_job.options.get(key)
            for key in (
                "template_id",
                "template_version",
                "output_format",
                "part_indices",
                "values",
                "template_source_kind",
                "template_usage_mode",
                "template_zones",
            )
        }

    signature = {
        "source_job_id": source_job.id,
        "analysis_checksum": request.checksum_sha256,
        "archive_files": references,
        "render": render_snapshot,
    }
    previous = [
        job
        for job in manager.list_for_user(user.id, source_job.project_id)
        if job.tool == "archive_export" and job.options.get("source_job_id") == source_job.id and not job.options.get("automatic")
    ]
    identical = next(
        (
            job
            for job in previous
            if all(job.options.get(key) == value for key, value in signature.items())
            and job.state not in {"failed", "cancelled", "expired"}
        ),
        None,
    )
    if identical is not None:
        return identical.public()
    if any(job.state not in {"completed", "failed", "cancelled", "expired"} for job in previous):
        raise HTTPException(status_code=409, detail="Une archive est déjà en cours de création.")

    child = manager.create(
        user.id,
        source_job.project_id,
        "cours.dars",
        "",
        source_job.language,
        settings.whisper_cpu_threads,
        execution_backend="worker",
        allocate_workspace=False,
        tool="archive_export",
        options={**signature, "project_title": project.title},
    )
    try:
        job_queue.enqueue(child.id)
    except Exception:
        pass
    return child.public()
