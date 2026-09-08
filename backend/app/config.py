from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    job_ttl_seconds: int
    max_upload_bytes: int
    whisper_cpu_threads: int


def load_settings() -> Settings:
    default_root = "/dev/shm/dars-manager-beta"
    root = Path(os.environ.get("DARSM_TEMP_ROOT", default_root)).expanduser().resolve()
    return Settings(
        workspace_root=root,
        job_ttl_seconds=int(os.environ.get("DARSM_JOB_TTL_SECONDS", "7200")),
        max_upload_bytes=int(os.environ.get("DARSM_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024))),
        whisper_cpu_threads=max(1, int(os.environ.get("DARSM_WHISPER_CPU_THREADS", "4"))),
    )


settings = load_settings()
