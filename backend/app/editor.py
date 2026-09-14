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
from .models import Artifact, User, utc_now
from .runtime import manager, media_storage


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
