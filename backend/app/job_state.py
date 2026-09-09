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
            row.execution_backend = record.get("execution_backend", "inline")
            row.worker_id = record.get("worker_id")
            lease_value = record.get("lease_expires_at")
            row.lease_expires_at = (
                datetime.fromtimestamp(float(lease_value), timezone.utc)
                if lease_value is not None
                else None
            )
            row.attempt_count = int(record.get("attempt_count", 0))
            row.error_code = "pipeline_failed" if row.state == "failed" else None
            row.payload = copy.deepcopy(record)
            row.updated_at = updated_at
            row.expires_at = updated_at + timedelta(seconds=self.ttl_seconds)
            db.commit()

    def claim(self, job_id: str, worker_id: str, lease_seconds: int) -> JobRecord | None:
        with self.session_factory() as db:
            row = db.scalar(
                select(DatabaseJobRecord)
                .where(DatabaseJobRecord.id == job_id)
                .with_for_update()
            )
            if (
                row is None
                or row.state != "queued"
                or row.execution_backend != "worker"
                or not row.payload
            ):
                return None
            record = copy.deepcopy(row.payload)
            now = time.time()
            record.update(
                state="running",
                stage="preparing",
                message="Worker preparing media",
                worker_id=worker_id,
                lease_expires_at=now + lease_seconds,
                attempt_count=int(record.get("attempt_count", 0)) + 1,
                updated_at=now,
            )
            row.state = record["state"]
            row.stage = record["stage"]
            row.message = record["message"]
            row.worker_id = worker_id
            row.lease_expires_at = datetime.fromtimestamp(
                record["lease_expires_at"], timezone.utc
            )
            row.attempt_count = record["attempt_count"]
            row.updated_at = datetime.fromtimestamp(now, timezone.utc)
            row.payload = record
            db.commit()
            return copy.deepcopy(record)

    def renew_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        with self.session_factory() as db:
            row = db.get(DatabaseJobRecord, job_id)
            if row is None or row.worker_id != worker_id or row.state not in {
                "running",
                "paused",
                "cancelling",
            }:
                return False
            lease_expires_at = time.time() + lease_seconds
            record = copy.deepcopy(row.payload or {})
            record["lease_expires_at"] = lease_expires_at
            row.lease_expires_at = datetime.fromtimestamp(lease_expires_at, timezone.utc)
            row.payload = record
            db.commit()
            return True

    def next_queued_id(self) -> str | None:
        with self.session_factory() as db:
            return db.scalar(
                select(DatabaseJobRecord.id)
                .where(
                    DatabaseJobRecord.execution_backend == "worker",
                    DatabaseJobRecord.state == "queued",
                )
                .order_by(DatabaseJobRecord.created_at)
                .limit(1)
            )

    def recover_expired_leases(self, max_attempts: int) -> list[str]:
        now = datetime.now(timezone.utc)
        requeued: list[str] = []
        with self.session_factory() as db:
            rows = db.scalars(
                select(DatabaseJobRecord).where(
                    DatabaseJobRecord.execution_backend == "worker",
                    DatabaseJobRecord.worker_id.is_not(None),
                    DatabaseJobRecord.lease_expires_at < now,
                )
            ).all()
            for row in rows:
                record = copy.deepcopy(row.payload or {})
                row.worker_id = None
                row.lease_expires_at = None
                record["worker_id"] = None
                record["lease_expires_at"] = None
                record["updated_at"] = time.time()
                if row.state == "cancelling":
                    row.state = record["state"] = "cancelled"
                    row.message = record["message"] = "Job cancelled after worker interruption"
                elif row.state == "paused":
                    record["message"] = row.message = "Paused; worker can resume later"
                elif row.attempt_count >= max_attempts:
                    row.state = record["state"] = "failed"
                    row.stage = record["stage"] = "worker_failed"
                    row.message = record["message"] = "Worker retry limit reached"
                    record["error"] = "Worker lease expired repeatedly"
                    row.error_code = "worker_retry_limit"
                else:
                    row.state = record["state"] = "queued"
                    row.stage = record["stage"] = "retry"
                    row.message = record["message"] = "Worker interrupted; retry queued"
                    requeued.append(row.id)
                row.payload = record
            db.commit()
        return requeued

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
