from __future__ import annotations

from .config import settings
from .job_state import DatabaseJobStateStore
from .jobs import JobManager
from .storage import TemporaryStorage


storage = TemporaryStorage(settings.workspace_root, settings.job_ttl_seconds)
job_state = DatabaseJobStateStore(settings.job_ttl_seconds)
manager = JobManager(storage, job_state)
