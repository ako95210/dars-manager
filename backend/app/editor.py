from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_user
from .config import settings
from .database import get_db
from .jobs import Job
from .media_lifecycle import meter_media
from .models import Artifact, BrandTemplate, BrandTemplateFile, Project, User, utc_now
from .runtime import job_queue, manager, media_storage


router = APIRouter(prefix="/api/jobs", tags=["editor"])


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
    template_id: str = Field(min_length=32, max_length=32)
    template_version: int = Field(ge=1)
    output_format: str = Field(pattern=r"^(16:9|1:1|9:16)$")
    title: str = Field(default="", max_length=300)
    speaker: str = Field(default="", max_length=180)
    date: str = Field(default="", max_length=80)
    episode: str = Field(default="", max_length=80)

    @field_validator("title", "speaker", "date", "episode", mode="before")
    @classmethod
    def strip_values(cls, value: str) -> str:
        return value.strip()


class ArchiveExportRequest(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    video_job_id: str | None = Field(default=None, min_length=32, max_length=32)
    audio_export_job_id: str | None = Field(default=None, min_length=32, max_length=32)


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


def response_payload(
    job: Job,
    payload: dict[str, Any],
    checksum: str,
) -> dict[str, Any]:
    return {
        "schema": payload.get("schema", 3),
        "audio_name": payload.get("audio_name", "audio"),
        "duration_seconds": duration_for(job, payload),
        "checksum_sha256": checksum,
        "segments": payload["segments"],
        "parts": payload["parts"],
    }


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


@router.get("/{job_id}/analysis")
def get_analysis(
    job_id: str,
    user: User = Depends(require_user),
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
    user: User = Depends(require_user),
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
        return response_payload(job, payload, checksum)


@router.post("/{job_id}/exports/audio", status_code=202)
def create_audio_export(
    job_id: str,
    request: AudioExportRequest,
    user: User = Depends(require_user),
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
    if any(end <= start or start < 0 for start, end in ranges):
        raise HTTPException(status_code=422, detail="Les timestamps sélectionnés sont invalides.")

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
    user: User = Depends(require_user),
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
    audio = db.scalar(
        select(Artifact).where(
            Artifact.job_id == source_job.id,
            Artifact.user_id == user.id,
            Artifact.kind == "audio",
        )
    )
    if template_source is None or audio is None or not audio.storage_key:
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
    values = {
        "title": request.title or " · ".join(str(part.get("title", "")) for part in selected),
        "speaker": request.speaker,
        "date": request.date,
        "episode": request.episode,
    }
    signature = {
        "source_job_id": source_job.id,
        "analysis_checksum": request.checksum_sha256,
        "part_indices": canonical_indices,
        "template_id": template.id,
        "template_version": template.version,
        "output_format": request.output_format,
        "values": values,
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
            "template_source_key": template_source.storage_key,
            "template_source_checksum": template_source.checksum_sha256,
            "template_source_kind": template.source_kind,
            "template_usage_mode": template.usage_mode,
            "template_frame_seconds": float(template.settings.get("frame_seconds", 0)),
            "template_zones": list(template.settings.get("zones", [])),
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
    user: User = Depends(require_user),
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
        references["audio"] = archive_reference(selected_audio)

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
            "audio": archive_reference(rendered_audio),
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
        if job.tool == "archive_export" and job.options.get("source_job_id") == source_job.id
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
