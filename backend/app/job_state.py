from __future__ import annotations

import copy
import json
import threading
import time
from typing import Any, Protocol

from redis import Redis


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


def create_job_state_store(
    redis_url: str | None,
    ttl_seconds: int,
) -> JobStateStore:
    if redis_url:
        return RedisJobStateStore(redis_url, ttl_seconds)
    return MemoryJobStateStore(ttl_seconds)
