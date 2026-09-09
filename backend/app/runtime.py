from __future__ import annotations

from .config import settings
from .job_state import create_job_state_store
from .jobs import JobManager
from .storage import TemporaryStorage


storage = TemporaryStorage(settings.workspace_root, settings.job_ttl_seconds)
job_state = create_job_state_store(settings.redis_url, settings.job_ttl_seconds)
manager = JobManager(storage, job_state)
