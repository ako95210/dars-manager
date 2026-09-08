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
    database_url: str
    redis_url: str | None
    session_cookie: str
    session_ttl_seconds: int
    cookie_secure: bool
    frontend_origin: str
    frontend_dist: Path


def load_settings() -> Settings:
    default_root = "/dev/shm/dars-manager-beta"
    default_frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    root = Path(os.environ.get("DARSM_TEMP_ROOT", default_root)).expanduser().resolve()
    return Settings(
        workspace_root=root,
        job_ttl_seconds=int(os.environ.get("DARSM_JOB_TTL_SECONDS", "7200")),
        max_upload_bytes=int(os.environ.get("DARSM_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024))),
        whisper_cpu_threads=max(1, int(os.environ.get("DARSM_WHISPER_CPU_THREADS", "4"))),
        database_url=os.environ.get(
            "DARSM_DATABASE_URL",
            "sqlite+pysqlite:////dev/shm/dars-manager-beta.db",
        ),
        redis_url=os.environ.get("DARSM_REDIS_URL", "").strip() or None,
        session_cookie=os.environ.get("DARSM_SESSION_COOKIE", "dars_session"),
        session_ttl_seconds=int(os.environ.get("DARSM_SESSION_TTL_SECONDS", str(7 * 86400))),
        cookie_secure=os.environ.get("DARSM_COOKIE_SECURE", "true").strip().lower()
        in {"1", "true", "yes", "on"},
        frontend_origin=os.environ.get("DARSM_FRONTEND_ORIGIN", "http://localhost:5173"),
        frontend_dist=Path(
            os.environ.get("DARSM_FRONTEND_DIST", str(default_frontend))
        ).expanduser().resolve(),
    )


settings = load_settings()
