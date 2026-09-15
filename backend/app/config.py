from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote_plus


def secret_value(name: str, default: str = "") -> str:
    """Read a secret from a mounted file, falling back to an environment value."""
    file_path = os.environ.get(f"{name}_FILE", "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Unable to read {name}_FILE") from exc
    return os.environ.get(name, default).strip()


def comma_separated(name: str, default: str) -> tuple[str, ...]:
    values = tuple(
        value.strip()
        for value in os.environ.get(name, default).split(",")
        if value.strip()
    )
    if not values:
        raise ValueError(f"{name} must contain at least one value")
    return values


@dataclass(frozen=True)
class Settings:
    environment: str
    release: str
    log_level: str
    workspace_root: Path
    media_backend: str
    media_root: Path
    media_retention_seconds: int
    media_upload_url_ttl_seconds: int
    execution_backend: str
    worker_poll_seconds: int
    worker_lease_seconds: int
    worker_max_attempts: int
    s3_bucket: str | None
    s3_region: str
    s3_endpoint_url: str | None
    storage_provider: str
    storage_model: str
    storage_gb_month_usd: Decimal
    storage_price_source_url: str
    maintenance_interval_seconds: int
    job_ttl_seconds: int
    max_upload_bytes: int
    whisper_cpu_threads: int
    transcription_backend: str
    transcription_model: str
    local_whisper_model: str
    transcription_chunk_seconds: int
    transcription_chunk_max_bytes: int
    openai_api_key: str | None
    openai_timeout_seconds: float
    database_url: str
    redis_url: str | None
    session_cookie: str
    session_ttl_seconds: int
    cookie_secure: bool
    frontend_origin: str
    allowed_hosts: tuple[str, ...]
    trusted_origins: tuple[str, ...]
    frontend_dist: Path
    smtp_host: str | None
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_starttls: bool
    smtp_ssl: bool
    email_from: str | None
    invitation_ttl_seconds: int


def validate_settings(settings: Settings) -> None:
    if settings.environment not in {"development", "test", "beta", "production"}:
        raise ValueError(
            "DARSM_ENVIRONMENT must be development, test, beta or production"
        )
    if settings.environment not in {"beta", "production"}:
        return
    errors: list[str] = []
    if not settings.cookie_secure:
        errors.append("DARSM_COOKIE_SECURE must be true")
    if not settings.frontend_origin.startswith("https://"):
        errors.append("DARSM_FRONTEND_ORIGIN must use https://")
    if "*" in settings.allowed_hosts:
        errors.append("DARSM_ALLOWED_HOSTS cannot contain '*' ")
    if settings.database_url.startswith("sqlite"):
        errors.append("PostgreSQL is required")
    if settings.execution_backend != "worker":
        errors.append("DARSM_EXECUTION_BACKEND must be worker")
    if settings.frontend_origin.rstrip("/") not in settings.trusted_origins:
        errors.append("DARSM_FRONTEND_ORIGIN must be a trusted origin")
    if errors:
        raise ValueError("Unsafe deployment configuration: " + "; ".join(errors))


def load_settings() -> Settings:
    default_root = "/dev/shm/dars-manager-beta"
    default_frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    root = Path(os.environ.get("DARSM_TEMP_ROOT", default_root)).expanduser().resolve()
    media_backend = os.environ.get("DARSM_MEDIA_BACKEND", "local").strip().lower()
    if media_backend not in {"local", "s3"}:
        raise ValueError("DARSM_MEDIA_BACKEND must be 'local' or 's3'")
    execution_backend = os.environ.get("DARSM_EXECUTION_BACKEND", "inline").strip().lower()
    if execution_backend not in {"inline", "worker"}:
        raise ValueError("DARSM_EXECUTION_BACKEND must be 'inline' or 'worker'")
    transcription_backend = os.environ.get(
        "DARSM_TRANSCRIPTION_BACKEND", "local"
    ).strip().lower()
    if transcription_backend not in {"local", "openai"}:
        raise ValueError("DARSM_TRANSCRIPTION_BACKEND must be 'local' or 'openai'")
    raw_storage_price = os.environ.get("DARSM_STORAGE_GB_MONTH_USD", "").strip()
    if media_backend == "s3" and not raw_storage_price:
        raise ValueError(
            "DARSM_STORAGE_GB_MONTH_USD is required when DARSM_MEDIA_BACKEND=s3"
        )
    try:
        storage_price = Decimal(raw_storage_price or "0")
    except InvalidOperation as exc:
        raise ValueError("DARSM_STORAGE_GB_MONTH_USD must be a decimal number") from exc
    if storage_price < 0:
        raise ValueError("DARSM_STORAGE_GB_MONTH_USD cannot be negative")
    database_url = os.environ.get("DARSM_DATABASE_URL", "").strip()
    database_host = os.environ.get("DARSM_DATABASE_HOST", "").strip()
    if not database_url and database_host:
        database_user = os.environ.get("DARSM_DATABASE_USER", "dars_manager").strip()
        database_name = os.environ.get("DARSM_DATABASE_NAME", "dars_manager").strip()
        database_port = int(os.environ.get("DARSM_DATABASE_PORT", "5432"))
        database_password = secret_value("DARSM_DATABASE_PASSWORD")
        if not database_password:
            raise ValueError(
                "DARSM_DATABASE_PASSWORD or DARSM_DATABASE_PASSWORD_FILE is required"
            )
        database_url = (
            "postgresql+psycopg://"
            f"{quote_plus(database_user)}:{quote_plus(database_password)}@"
            f"{database_host}:{database_port}/{quote_plus(database_name)}"
        )
    if not database_url:
        database_url = "sqlite+pysqlite:////dev/shm/dars-manager-beta.db"
    frontend_origin = os.environ.get(
        "DARSM_FRONTEND_ORIGIN", "http://localhost:5173"
    ).rstrip("/")
    loaded = Settings(
        environment=os.environ.get("DARSM_ENVIRONMENT", "development").strip().lower(),
        release=os.environ.get("DARSM_RELEASE", "dev").strip() or "dev",
        log_level=os.environ.get("DARSM_LOG_LEVEL", "INFO").strip().upper(),
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
        execution_backend=execution_backend,
        worker_poll_seconds=max(1, int(os.environ.get("DARSM_WORKER_POLL_SECONDS", "2"))),
        worker_lease_seconds=max(30, int(os.environ.get("DARSM_WORKER_LEASE_SECONDS", "120"))),
        worker_max_attempts=max(1, int(os.environ.get("DARSM_WORKER_MAX_ATTEMPTS", "3"))),
        s3_bucket=os.environ.get("DARSM_S3_BUCKET", "").strip() or None,
        s3_region=os.environ.get("DARSM_S3_REGION", "eu-west-3"),
        s3_endpoint_url=os.environ.get("DARSM_S3_ENDPOINT_URL", "").strip() or None,
        storage_provider=(
            os.environ.get("DARSM_STORAGE_PROVIDER", "").strip()
            or ("local" if media_backend == "local" else "s3-compatible")
        ),
        storage_model=(
            os.environ.get("DARSM_STORAGE_MODEL", "").strip() or "temporary-standard"
        ),
        storage_gb_month_usd=storage_price,
        storage_price_source_url=os.environ.get(
            "DARSM_STORAGE_PRICE_SOURCE_URL", ""
        ).strip(),
        maintenance_interval_seconds=max(
            60, int(os.environ.get("DARSM_MAINTENANCE_INTERVAL_SECONDS", "3600"))
        ),
        job_ttl_seconds=int(os.environ.get("DARSM_JOB_TTL_SECONDS", "7200")),
        max_upload_bytes=int(os.environ.get("DARSM_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024))),
        whisper_cpu_threads=max(1, int(os.environ.get("DARSM_WHISPER_CPU_THREADS", "4"))),
        transcription_backend=transcription_backend,
        transcription_model=os.environ.get(
            "DARSM_TRANSCRIPTION_MODEL", "whisper-1"
        ).strip(),
        local_whisper_model=os.environ.get(
            "DARSM_LOCAL_WHISPER_MODEL", "base"
        ).strip(),
        # Nine-minute mono/16 kHz WAV fragments stay comfortably small while
        # keeping enough context for Whisper. Oversized fragments are split
        # again after encoding.
        transcription_chunk_seconds=max(
            30, int(os.environ.get("DARSM_TRANSCRIPTION_CHUNK_SECONDS", "540"))
        ),
        transcription_chunk_max_bytes=max(
            1_000_000,
            int(os.environ.get("DARSM_TRANSCRIPTION_CHUNK_MAX_BYTES", "24000000")),
        ),
        openai_api_key=secret_value("OPENAI_API_KEY") or None,
        openai_timeout_seconds=max(
            10.0, float(os.environ.get("DARSM_OPENAI_TIMEOUT_SECONDS", "900"))
        ),
        database_url=database_url,
        redis_url=os.environ.get("DARSM_REDIS_URL", "").strip() or None,
        session_cookie=os.environ.get("DARSM_SESSION_COOKIE", "dars_session"),
        session_ttl_seconds=int(os.environ.get("DARSM_SESSION_TTL_SECONDS", str(7 * 86400))),
        cookie_secure=os.environ.get("DARSM_COOKIE_SECURE", "true").strip().lower()
        in {"1", "true", "yes", "on"},
        frontend_origin=frontend_origin,
        allowed_hosts=comma_separated(
            "DARSM_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
        ),
        trusted_origins=tuple(
            origin.rstrip("/")
            for origin in comma_separated("DARSM_TRUSTED_ORIGINS", frontend_origin)
        ),
        frontend_dist=Path(
            os.environ.get("DARSM_FRONTEND_DIST", str(default_frontend))
        ).expanduser().resolve(),
        smtp_host=os.environ.get("DARSM_SMTP_HOST", "").strip() or None,
        smtp_port=max(1, int(os.environ.get("DARSM_SMTP_PORT", "587"))),
        smtp_username=os.environ.get("DARSM_SMTP_USERNAME", "").strip(),
        smtp_password=secret_value("DARSM_SMTP_PASSWORD"),
        smtp_starttls=os.environ.get("DARSM_SMTP_STARTTLS", "true").strip().lower()
        in {"1", "true", "yes", "on"},
        smtp_ssl=os.environ.get("DARSM_SMTP_SSL", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        email_from=os.environ.get("DARSM_EMAIL_FROM", "").strip() or None,
        invitation_ttl_seconds=max(
            900, int(os.environ.get("DARSM_INVITATION_TTL_SECONDS", str(48 * 3600)))
        ),
    )
    validate_settings(loaded)
    return loaded


settings = load_settings()
