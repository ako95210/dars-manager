from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "event_fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    logger = logging.getLogger("dars")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.propagate = False


class RuntimeMetrics:
    def __init__(self) -> None:
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._requests = 0
        self._errors = 0
        self._status_codes: Counter[str] = Counter()
        self._total_duration_ms = 0.0

    def record(self, status_code: int, duration_ms: float) -> None:
        with self._lock:
            self._requests += 1
            if status_code >= 500:
                self._errors += 1
            self._status_codes[str(status_code)] += 1
            self._total_duration_ms += duration_ms

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            average = self._total_duration_ms / self._requests if self._requests else 0
            return {
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "requests": self._requests,
                "server_errors": self._errors,
                "average_duration_ms": round(average, 3),
                "status_codes": dict(self._status_codes),
            }


runtime_metrics = RuntimeMetrics()
logger = logging.getLogger("dars.http")


class OperationalMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        trusted_origins: tuple[str, ...],
        hsts: bool,
        log_requests: bool,
    ) -> None:
        super().__init__(app)
        self.trusted_origins = {origin.rstrip("/") for origin in trusted_origins}
        self.hsts = hsts
        self.log_requests = log_requests

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if REQUEST_ID.fullmatch(supplied_request_id)
            else uuid.uuid4().hex
        )
        request.state.request_id = request_id
        started = time.perf_counter()
        origin = request.headers.get("origin", "").rstrip("/")
        if (
            request.method in UNSAFE_METHODS
            and origin
            and origin not in self.trusted_origins
        ):
            response: Response = JSONResponse(
                status_code=403, content={"detail": "Origin not allowed"}
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                duration_ms = (time.perf_counter() - started) * 1000
                runtime_metrics.record(500, duration_ms)
                logger.exception(
                    "request_failed",
                    extra={
                        "event_fields": {
                            "request_id": request_id,
                            "method": request.method,
                            "path": request.url.path,
                            "duration_ms": round(duration_ms, 3),
                        }
                    },
                )
                raise
        duration_ms = (time.perf_counter() - started) * 1000
        runtime_metrics.record(response.status_code, duration_ms)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; media-src 'self' blob: https:; "
            "connect-src 'self' https:; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        if self.hsts:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if self.log_requests:
            logger.info(
                "request",
                extra={
                    "event_fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round(duration_ms, 3),
                    }
                },
            )
        return response
