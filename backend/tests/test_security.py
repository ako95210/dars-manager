from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.app.config import load_settings, settings, validate_settings
from backend.app.email_delivery import (
    email_delivery_configured,
    send_account_invitation,
)
from backend.app.security import LoginThrottle


class SecurityTests(unittest.TestCase):
    def test_smtp_invitation_uses_tls_and_authentication(self) -> None:
        smtp_settings = replace(
            settings,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="smtp-user",
            smtp_password="smtp-secret",
            smtp_starttls=True,
            smtp_ssl=False,
            email_from="Dars Manager <accounts@dars-manager.com>",
        )
        with (
            patch("backend.app.email_delivery.settings", smtp_settings),
            patch("backend.app.email_delivery.smtplib.SMTP") as smtp_class,
        ):
            self.assertTrue(email_delivery_configured())
            send_account_invitation(
                "client@example.com",
                "secure-invitation-token",
            )

        smtp_class.assert_called_once_with("smtp.example.com", 587, timeout=15)
        smtp = smtp_class.return_value.__enter__.return_value
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("smtp-user", "smtp-secret")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "client@example.com")
        plain_body = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("#invitation=secure-invitation-token", plain_body)
        self.assertIn("nom affiché", plain_body)

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
