from __future__ import annotations

from .config import settings
from .job_state import DatabaseJobStateStore
from .job_queue import create_job_queue
from .jobs import JobManager
from .media_storage import create_media_storage
from .storage import TemporaryStorage


storage = TemporaryStorage(settings.workspace_root, settings.job_ttl_seconds)
job_state = DatabaseJobStateStore(settings.job_ttl_seconds)
manager = JobManager(storage, job_state)
media_storage = create_media_storage(settings)
job_queue = create_job_queue(settings.redis_url)
