from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from redis import Redis
from sqlalchemy import select

from .database import SessionLocal
from .models import JobRecord as DatabaseJobRecord


JobRecord = dict[str, Any]


class JobStateStore(Protocol):
    def save(self, record: JobRecord) -> None: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def delete(self, job_id: str) -> None: ...

    def all(self) -> list[JobRecord]: ...


class MemoryJobStateStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._records: dict[str, tuple[float, JobRecord]] = {}
        self._lock = threading.RLock()

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [
            job_id
            for job_id, (expires_at, _) in self._records.items()
            if expires_at <= now
        ]
        for job_id in expired:
            self._records.pop(job_id, None)

    def save(self, record: JobRecord) -> None:
        with self._lock:
            self._purge_expired()
            self._records[record["id"]] = (
                time.monotonic() + self.ttl_seconds,
                copy.deepcopy(record),
            )

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            self._purge_expired()
            entry = self._records.get(job_id)
            return copy.deepcopy(entry[1]) if entry else None

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._records.pop(job_id, None)

    def all(self) -> list[JobRecord]:
        with self._lock:
            self._purge_expired()
            return [copy.deepcopy(record) for _, record in self._records.values()]


class RedisJobStateStore:
    def __init__(
        self,
        url: str,
        ttl_seconds: int,
        key_prefix: str = "dars:jobs:",
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.key_prefix = key_prefix
        self.client = Redis.from_url(url, decode_responses=True)

    def _key(self, job_id: str) -> str:
        return f"{self.key_prefix}{job_id}"

    def save(self, record: JobRecord) -> None:
        self.client.set(
            self._key(record["id"]),
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            ex=self.ttl_seconds,
        )

    def get(self, job_id: str) -> JobRecord | None:
        payload = self.client.get(self._key(job_id))
        return json.loads(payload) if payload else None

    def delete(self, job_id: str) -> None:
        self.client.delete(self._key(job_id))

    def all(self) -> list[JobRecord]:
        records: list[JobRecord] = []
        for key in self.client.scan_iter(match=f"{self.key_prefix}*"):
            payload = self.client.get(key)
            if payload:
                records.append(json.loads(payload))
        return records


class DatabaseJobStateStore:
    """Durable job state. Redis remains available for queueing, not ownership."""

    def __init__(self, ttl_seconds: int, session_factory=SessionLocal) -> None:
        self.ttl_seconds = ttl_seconds
        self.session_factory = session_factory

    def save(self, record: JobRecord) -> None:
        updated_at = datetime.fromtimestamp(
            float(record.get("updated_at", time.time())), timezone.utc
        )
        with self.session_factory() as db:
            row = db.get(DatabaseJobRecord, record["id"])
            if row is None:
                row = DatabaseJobRecord(
                    id=record["id"],
                    user_id=record["user_id"],
                    project_id=record["project_id"],
                    tool="audio_pipeline",
                    created_at=datetime.fromtimestamp(
                        float(record.get("created_at", time.time())), timezone.utc
                    ),
                )
                db.add(row)
            row.model_name = record.get("model_name", "")
            row.language = record.get("language", "")
            row.state = record.get("state", "queued")
            row.stage = record.get("stage", "upload")
            row.message = record.get("message", "")
            row.progress = round(float(record.get("progress", 0)) * 100)
            row.error_code = "pipeline_failed" if row.state == "failed" else None
            row.payload = copy.deepcopy(record)
            row.updated_at = updated_at
            row.expires_at = updated_at + timedelta(seconds=self.ttl_seconds)
            db.commit()

    def get(self, job_id: str) -> JobRecord | None:
        with self.session_factory() as db:
            row = db.get(DatabaseJobRecord, job_id)
            return copy.deepcopy(row.payload) if row and row.payload else None

    def delete(self, job_id: str) -> None:
        with self.session_factory() as db:
            row = db.get(DatabaseJobRecord, job_id)
            if row is not None:
                db.delete(row)
                db.commit()

    def all(self) -> list[JobRecord]:
        with self.session_factory() as db:
            rows = db.scalars(
                select(DatabaseJobRecord).order_by(DatabaseJobRecord.updated_at.desc())
            )
            return [copy.deepcopy(row.payload) for row in rows if row.payload]


def create_job_state_store(
    redis_url: str | None,
    ttl_seconds: int,
) -> JobStateStore:
    if redis_url:
        return RedisJobStateStore(redis_url, ttl_seconds)
    return MemoryJobStateStore(ttl_seconds)
