from __future__ import annotations

import time
from typing import Protocol

from redis import Redis


class JobQueue(Protocol):
    def enqueue(self, job_id: str) -> None: ...

    def wait(self, timeout_seconds: int) -> str | None: ...


class PollingJobQueue:
    """Database polling fallback for development without Redis."""

    def enqueue(self, job_id: str) -> None:
        return None

    def wait(self, timeout_seconds: int) -> str | None:
        time.sleep(timeout_seconds)
        return None


class RedisJobQueue:
    def __init__(self, url: str, queue_name: str = "dars:queue:audio") -> None:
        self.client = Redis.from_url(url, decode_responses=True)
        self.queue_name = queue_name

    def enqueue(self, job_id: str) -> None:
        self.client.rpush(self.queue_name, job_id)

    def wait(self, timeout_seconds: int) -> str | None:
        item = self.client.blpop(self.queue_name, timeout=timeout_seconds)
        return item[1] if item else None


def create_job_queue(redis_url: str | None) -> JobQueue:
    return RedisJobQueue(redis_url) if redis_url else PollingJobQueue()
