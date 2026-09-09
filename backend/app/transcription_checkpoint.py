from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from drsm_core import TranscriptSegment

from .jobs import Job
from .media_storage import MediaStorage
from .models import Artifact, utc_now
from .transcription import ProviderTranscription, TranscriptionProvider


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class CheckpointingTranscriptionProvider:
    """Persist paid responses so a worker retry can resume without another call."""

    def __init__(
        self,
        provider: TranscriptionProvider,
        job: Job,
        *,
        storage: MediaStorage,
        session_factory: Callable[[], Session],
        retention_seconds: int,
    ) -> None:
        self.provider = provider.provider
        self.model = provider.model
        self._provider = provider
        self._job = job
        self._storage = storage
        self._session_factory = session_factory
        self._retention_seconds = retention_seconds

    def _key(self, checksum: str) -> str:
        job = self._job
        return (
            f"users/{job.user_id}/projects/{job.project_id}/jobs/{job.id}/"
            f"transcription/{checksum}.json"
        )

    def _load(self, key: str, checksum: str, path: Path) -> ProviderTranscription | None:
        with self._session_factory() as db:
            row = db.scalar(
                select(Artifact).where(
                    Artifact.job_id == self._job.id,
                    Artifact.user_id == self._job.user_id,
                    Artifact.kind == "transcription_checkpoint",
                    Artifact.storage_key == key,
                )
            )
            if row is None:
                return None
            expected_checksum = row.checksum_sha256
        checkpoint = path.with_name(f".{path.stem}-{checksum[:12]}-checkpoint.json")
        try:
            self._storage.download_file(key, checkpoint)
            if expected_checksum and sha256_file(checkpoint) != expected_checksum:
                raise ValueError("Transcription checkpoint checksum mismatch")
            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            return ProviderTranscription(
                segments=tuple(
                    TranscriptSegment(
                        float(item["start"]), float(item["end"]), str(item["text"])
                    )
                    for item in payload["segments"]
                ),
                duration_seconds=float(payload["duration_seconds"]),
                request_id=payload.get("request_id"),
                reused=True,
            )
        except Exception:
            with self._session_factory() as db:
                stale = db.scalar(
                    select(Artifact).where(
                        Artifact.job_id == self._job.id,
                        Artifact.user_id == self._job.user_id,
                        Artifact.kind == "transcription_checkpoint",
                        Artifact.storage_key == key,
                    )
                )
                if stale is not None:
                    db.delete(stale)
                    db.commit()
            try:
                self._storage.delete(key)
            except Exception:
                pass
            return None
        finally:
            checkpoint.unlink(missing_ok=True)

    def _save(
        self,
        key: str,
        checksum: str,
        path: Path,
        result: ProviderTranscription,
    ) -> None:
        checkpoint = path.with_name(f".{path.stem}-{checksum[:12]}-checkpoint.json")
        payload = {
            "schema": 1,
            "provider": self.provider,
            "model": self.model,
            "request_id": result.request_id,
            "duration_seconds": result.duration_seconds,
            "segments": [asdict(segment) for segment in result.segments],
        }
        checkpoint.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            content_checksum = sha256_file(checkpoint)
            self._storage.upload_file(key, checkpoint, "application/json")
            now = utc_now()
            with self._session_factory() as db:
                existing = db.scalar(
                    select(Artifact).where(
                        Artifact.job_id == self._job.id,
                        Artifact.user_id == self._job.user_id,
                        Artifact.kind == "transcription_checkpoint",
                        Artifact.storage_key == key,
                    )
                )
                if existing is None:
                    db.add(
                        Artifact(
                            user_id=self._job.user_id,
                            project_id=self._job.project_id,
                            job_id=self._job.id,
                            kind="transcription_checkpoint",
                            mime_type="application/json",
                            size_bytes=checkpoint.stat().st_size,
                            storage_key=key,
                            checksum_sha256=content_checksum,
                            storage_metered_at=now,
                            expires_at=now + timedelta(seconds=self._retention_seconds),
                        )
                    )
                    db.commit()
        finally:
            checkpoint.unlink(missing_ok=True)

    def transcribe(self, path: Path, language: str) -> ProviderTranscription:
        checksum = sha256_file(path)
        key = self._key(checksum)
        cached = self._load(key, checksum, path)
        if cached is not None:
            return cached
        result = self._provider.transcribe(path, language)
        self._save(key, checksum, path, result)
        return result
