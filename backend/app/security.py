from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

from pwdlib import PasswordHash


password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(24))


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: int = 300,
        max_keys: int = 10_000,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            failures = self._failures.get(key)
            if failures is None:
                return 0
            while failures and now - failures[0] >= self.window_seconds:
                failures.popleft()
            if not failures:
                self._failures.pop(key, None)
                return 0
            self._failures.move_to_end(key)
            if len(failures) < self.max_failures:
                return 0
            return max(1, round(self.window_seconds - (now - failures[0])))

    def failure(self, key: str) -> None:
        with self._lock:
            failures = self._failures.get(key)
            if failures is None:
                if len(self._failures) >= self.max_keys:
                    self._failures.popitem(last=False)
                failures = deque()
                self._failures[key] = failures
            failures.append(time.monotonic())
            self._failures.move_to_end(key)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Le mot de passe doit contenir au moins 10 caractères.")
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_invitation_token() -> str:
    return secrets.token_urlsafe(32)


def hash_invitation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiration(ttl_seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


def is_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)
