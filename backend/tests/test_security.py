from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.app.config import load_settings, settings, validate_settings
from backend.app.security import LoginThrottle


class SecurityTests(unittest.TestCase):
    def test_login_throttle_expires_and_clears_failures(self) -> None:
        throttle = LoginThrottle(max_failures=2, window_seconds=60)
        self.assertEqual(throttle.retry_after("client:user"), 0)
        throttle.failure("client:user")
        throttle.failure("client:user")
        self.assertGreater(throttle.retry_after("client:user"), 0)
        throttle.clear("client:user")
        self.assertEqual(throttle.retry_after("client:user"), 0)

    def test_secret_file_takes_precedence_over_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "openai"
            secret.write_text("file-secret\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "environment-secret",
                    "OPENAI_API_KEY_FILE": str(secret),
                },
            ):
                loaded = load_settings()
            self.assertEqual(loaded.openai_api_key, "file-secret")

    def test_beta_configuration_fails_closed(self) -> None:
        unsafe = replace(settings, environment="beta", cookie_secure=False)
        with self.assertRaisesRegex(ValueError, "COOKIE_SECURE"):
            validate_settings(unsafe)


if __name__ == "__main__":
    unittest.main()
