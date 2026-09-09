from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    media_backend: str
    media_root: Path
    media_retention_seconds: int
    media_upload_url_ttl_seconds: int
    s3_bucket: str | None
    s3_region: str
    s3_endpoint_url: str | None
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
    media_backend = os.environ.get("DARSM_MEDIA_BACKEND", "local").strip().lower()
    if media_backend not in {"local", "s3"}:
        raise ValueError("DARSM_MEDIA_BACKEND must be 'local' or 's3'")
    return Settings(
        workspace_root=root,
        media_backend=media_backend,
        media_root=Path(
            os.environ.get("DARSM_MEDIA_ROOT", f"{root}-media")
        ).expanduser().resolve(),
        media_retention_seconds=int(
            os.environ.get("DARSM_MEDIA_RETENTION_SECONDS", str(7 * 86400))
        ),
        media_upload_url_ttl_seconds=int(
            os.environ.get("DARSM_MEDIA_UPLOAD_URL_TTL_SECONDS", "900")
        ),
        s3_bucket=os.environ.get("DARSM_S3_BUCKET", "").strip() or None,
        s3_region=os.environ.get("DARSM_S3_REGION", "eu-west-3"),
        s3_endpoint_url=os.environ.get("DARSM_S3_ENDPOINT_URL", "").strip() or None,
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
