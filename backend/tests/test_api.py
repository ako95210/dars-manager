from __future__ import annotations

import os
import hashlib
import shutil
import unittest
import uuid
import wave
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


TEST_ID = uuid.uuid4().hex
TEST_ROOT = Path("/dev/shm") / f"dars-api-tests-{TEST_ID}"
TEST_DATABASE = Path("/dev/shm") / f"dars-api-tests-{TEST_ID}.db"
os.environ["DARSM_TEMP_ROOT"] = str(TEST_ROOT)
os.environ["DARSM_MEDIA_BACKEND"] = "local"
os.environ["DARSM_MEDIA_ROOT"] = str(TEST_ROOT / "media")
os.environ["DARSM_DATABASE_URL"] = f"sqlite+pysqlite:///{TEST_DATABASE}"
os.environ["DARSM_COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from PIL import Image

from backend.app.costs import record_usage
from backend.app.config import settings as app_settings
from backend.app.database import CURRENT_REVISION, SessionLocal, engine, init_database
from backend.app.main import app, manager
from backend.app.jobs import JobManager
from backend.app.models import Artifact, Asset, BillingPolicy, UsageEvent, User, utc_now
from backend.app.pipeline import (
    PipelineResult,
    generate_cover,
    render_static_video,
    write_analysis,
)
from backend.app.runtime import media_storage
from backend.app.security import hash_password
from backend.app.transcription import ProviderTranscription
from backend.app.transcription_checkpoint import CheckpointingTranscriptionProvider
from backend.app.semantic_analysis import SemanticAnalysisCall, SemanticAnalysisResult
from backend.worker import Worker
from backend.maintenance import MaintenanceService
from drsm_core import CoursePart, TranscriptSegment


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_database()
        with SessionLocal() as db:
            db.add_all(
                [
                    User(
                        email="pilot-a@example.com",
                        display_name="Pilote A",
                        password_hash=hash_password("mot-de-passe-a"),
                        email_verified_at=utc_now(),
                    ),
                    User(
                        email="pilot-b@example.com",
                        display_name="Pilote B",
                        password_hash=hash_password("mot-de-passe-b"),
                        email_verified_at=utc_now(),
                    ),
                    User(
                        email="admin@example.com",
                        display_name="Administration",
                        password_hash=hash_password("mot-de-passe-admin"),
                        role="admin",
                        email_verified_at=utc_now(),
                    ),
                ]
            )
            db.commit()

    @classmethod
    def tearDownClass(cls) -> None:
        engine.dispose()
        if TEST_DATABASE.exists():
            TEST_DATABASE.unlink()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def login(self, client: TestClient, email: str, password: str) -> None:
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

    def test_database_is_migrated(self) -> None:
        with SessionLocal() as db:
            revision = db.scalar(text("SELECT version_num FROM alembic_version"))
        self.assertEqual(revision, CURRENT_REVISION)

    def test_authentication_and_logout(self) -> None:
        with TestClient(app) as client:
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("Dars Manager", home.text)
            self.assertEqual(home.headers["x-content-type-options"], "nosniff")
            self.assertEqual(home.headers["x-frame-options"], "DENY")
            self.assertTrue(home.headers["x-request-id"])
            self.assertEqual(client.get("/api/auth/me").status_code, 401)
            rejected_origin = client.post(
                "/api/auth/login",
                headers={"Origin": "https://malicious.example"},
                json={"email": "pilot-a@example.com", "password": "mot-de-passe-a"},
            )
            self.assertEqual(rejected_origin.status_code, 403, rejected_origin.text)
            self.assertEqual(
                client.post(
                    "/api/auth/login",
                    json={"email": "pilot-a@example.com", "password": "incorrect"},
                ).status_code,
                401,
            )
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            self.assertEqual(client.get("/api/auth/me").json()["display_name"], "Pilote A")
            self.assertEqual(client.post("/api/auth/logout").status_code, 204)
            self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_admin_can_manage_user_accounts_and_revoke_sessions(self) -> None:
        email = f"managed-{TEST_ID}@example.com"
        updated_email = f"managed-updated-{TEST_ID}@example.com"
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get("/api/admin/users").status_code, 401)
        with TestClient(app) as client:
            self.login(client, "pilot-b@example.com", "mot-de-passe-b")
            self.assertEqual(client.get("/api/admin/users").status_code, 403)

        with TestClient(app) as admin, TestClient(app) as managed, patch(
            "backend.app.admin_users.email_delivery_configured", return_value=True
        ), patch("backend.app.admin_users.send_account_invitation") as delivery:
            self.login(admin, "admin@example.com", "mot-de-passe-admin")
            created = admin.post(
                "/api/admin/users",
                json={
                    "email": email.upper(),
                    "display_name": "  Compte Piloté  ",
                    "role": "client",
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            account = created.json()
            self.assertEqual(account["email"], email)
            self.assertEqual(account["display_name"], "Compte Piloté")
            self.assertFalse(account["is_active"])
            self.assertIsNone(account["email_verified_at"])
            self.assertIsNotNone(account["invitation_sent_at"])
            self.assertNotIn("password", account)
            account_id = account["id"]
            first_token = delivery.call_args.args[2]
            self.assertEqual(
                managed.post(
                    "/api/auth/login",
                    json={"email": email, "password": "mot-de-passe-temporaire"},
                ).status_code,
                401,
            )
            details = managed.post("/api/auth/invitation", json={"token": first_token})
            self.assertEqual(details.status_code, 200, details.text)
            self.assertEqual(details.json()["email"], email)

            resent = admin.post(f"/api/admin/users/{account_id}/invitation")
            self.assertEqual(resent.status_code, 200, resent.text)
            second_token = delivery.call_args.args[2]
            self.assertNotEqual(first_token, second_token)
            self.assertEqual(
                managed.post("/api/auth/invitation", json={"token": first_token}).status_code,
                410,
            )
            accepted = managed.post(
                "/api/auth/invitation/accept",
                json={
                    "token": second_token,
                    "new_password": "mot-de-passe-temporaire",
                },
            )
            self.assertEqual(accepted.status_code, 204, accepted.text)
            self.assertEqual(
                managed.post("/api/auth/invitation", json={"token": second_token}).status_code,
                410,
            )

            duplicate = admin.post(
                "/api/admin/users",
                json={
                    "email": email,
                    "display_name": "Doublon",
                    "role": "client",
                },
            )
            self.assertEqual(duplicate.status_code, 409, duplicate.text)
            self.login(managed, email, "mot-de-passe-temporaire")

            deactivated = admin.put(
                f"/api/admin/users/{account_id}",
                json={
                    "email": email,
                    "display_name": "Compte mis à jour",
                    "role": "client",
                    "is_active": False,
                },
            )
            self.assertEqual(deactivated.status_code, 200, deactivated.text)
            self.assertFalse(deactivated.json()["is_active"])
            self.assertEqual(managed.get("/api/auth/me").status_code, 401)

            reactivated = admin.put(
                f"/api/admin/users/{account_id}",
                json={
                    "email": email,
                    "display_name": "Compte mis à jour",
                    "role": "client",
                    "is_active": True,
                },
            )
            self.assertEqual(reactivated.status_code, 200, reactivated.text)
            self.login(managed, email, "mot-de-passe-temporaire")

            changed_email = admin.put(
                f"/api/admin/users/{account_id}",
                json={
                    "email": updated_email,
                    "display_name": "Compte vérifié à nouveau",
                    "role": "client",
                    "is_active": True,
                },
            )
            self.assertEqual(changed_email.status_code, 200, changed_email.text)
            self.assertFalse(changed_email.json()["is_active"])
            self.assertIsNone(changed_email.json()["email_verified_at"])
            self.assertEqual(managed.get("/api/auth/me").status_code, 401)
            email_change_token = delivery.call_args.args[2]
            accepted_change = managed.post(
                "/api/auth/invitation/accept",
                json={
                    "token": email_change_token,
                    "new_password": "mot-de-passe-temporaire",
                },
            )
            self.assertEqual(accepted_change.status_code, 204, accepted_change.text)
            self.login(managed, updated_email, "mot-de-passe-temporaire")
            reset = admin.put(
                f"/api/admin/users/{account_id}/password",
                json={"new_password": "nouveau-mot-de-passe"},
            )
            self.assertEqual(reset.status_code, 204, reset.text)
            self.assertEqual(managed.get("/api/auth/me").status_code, 401)
            self.assertEqual(
                managed.post(
                    "/api/auth/login",
                    json={"email": updated_email, "password": "mot-de-passe-temporaire"},
                ).status_code,
                401,
            )
            self.login(managed, updated_email, "nouveau-mot-de-passe")

            current = admin.get("/api/auth/me").json()
            self_update = admin.put(
                f"/api/admin/users/{current['id']}",
                json={
                    "email": current["email"],
                    "display_name": current["display_name"],
                    "role": "client",
                    "is_active": True,
                },
            )
            self.assertEqual(self_update.status_code, 409, self_update.text)
            self.assertEqual(
                admin.put(
                    f"/api/admin/users/{current['id']}/password",
                    json={"new_password": "nouveau-mot-de-passe"},
                ).status_code,
                409,
            )

            deleted = admin.delete(f"/api/admin/users/{account_id}")
            self.assertEqual(deleted.status_code, 204, deleted.text)
            self.assertEqual(managed.get("/api/auth/me").status_code, 401)
            self.assertEqual(
                admin.put(
                    f"/api/admin/users/{account_id}",
                    json={
                        "email": updated_email,
                        "display_name": "Réactivation interdite",
                        "role": "client",
                        "is_active": True,
                    },
                ).status_code,
                409,
            )

            listing = admin.get("/api/admin/users")
            self.assertEqual(listing.status_code, 200, listing.text)
            deleted_account = next(item for item in listing.json() if item["id"] == account_id)
            self.assertIsNotNone(deleted_account["deleted_at"])
            self.assertNotEqual(deleted_account["email"], updated_email)

    def test_account_creation_fails_closed_without_email_delivery(self) -> None:
        with TestClient(app) as admin, patch(
            "backend.app.admin_users.email_delivery_configured", return_value=False
        ):
            self.login(admin, "admin@example.com", "mot-de-passe-admin")
            status_response = admin.get("/api/admin/users/email-status")
            self.assertEqual(status_response.status_code, 200, status_response.text)
            self.assertFalse(status_response.json()["configured"])
            refused = admin.post(
                "/api/admin/users",
                json={
                    "email": f"no-delivery-{TEST_ID}@example.com",
                    "display_name": "Sans envoi",
                    "role": "client",
                },
            )
            self.assertEqual(refused.status_code, 503, refused.text)

    def test_admin_is_restricted_to_administration(self) -> None:
        with TestClient(app) as admin:
            self.login(admin, "admin@example.com", "mot-de-passe-admin")
            self.assertEqual(admin.get("/api/projects").status_code, 403)
            self.assertEqual(
                admin.post(
                    "/api/projects",
                    json={"title": "Projet administrateur interdit"},
                ).status_code,
                403,
            )
            self.assertEqual(
                admin.post(
                    "/api/transcription/quote",
                    json={"duration_seconds": 60},
                ).status_code,
                403,
            )
            self.assertEqual(admin.get("/api/billing/summary").status_code, 403)
            self.assertEqual(admin.get("/api/admin/users").status_code, 200)
            self.assertEqual(admin.get("/api/admin/system").status_code, 200)

    def test_password_change_revokes_every_session(self) -> None:
        email = f"password-{TEST_ID}@example.com"
        with SessionLocal() as db:
            db.add(
                User(
                    email=email,
                    display_name="Password Pilot",
                    password_hash=hash_password("mot-de-passe-initial"),
                    email_verified_at=utc_now(),
                )
            )
            db.commit()
        with TestClient(app) as first, TestClient(app) as second:
            self.login(first, email, "mot-de-passe-initial")
            self.login(second, email, "mot-de-passe-initial")
            wrong = first.put(
                "/api/auth/password",
                json={
                    "current_password": "mot-de-passe-incorrect",
                    "new_password": "mot-de-passe-nouveau",
                },
            )
            self.assertEqual(wrong.status_code, 401, wrong.text)
            changed = first.put(
                "/api/auth/password",
                json={
                    "current_password": "mot-de-passe-initial",
                    "new_password": "mot-de-passe-nouveau",
                },
            )
            self.assertEqual(changed.status_code, 204, changed.text)
            self.assertEqual(first.get("/api/auth/me").status_code, 401)
            self.assertEqual(second.get("/api/auth/me").status_code, 401)
            self.assertEqual(
                second.post(
                    "/api/auth/login",
                    json={"email": email, "password": "mot-de-passe-initial"},
                ).status_code,
                401,
            )
            self.login(second, email, "mot-de-passe-nouveau")

    def test_operational_endpoints_are_scoped(self) -> None:
        with TestClient(app) as client:
            live = client.get("/api/health/live")
            self.assertEqual(live.status_code, 200, live.text)
            self.assertEqual(live.json()["status"], "alive")
            ready = client.get("/api/health/ready")
            self.assertEqual(ready.status_code, 200, ready.text)
            self.assertTrue(ready.json()["checks"]["database"])
            self.assertEqual(client.get("/api/admin/system").status_code, 401)
            self.login(client, "pilot-b@example.com", "mot-de-passe-b")
            self.assertEqual(client.get("/api/admin/system").status_code, 403)
        with TestClient(app) as admin:
            self.login(admin, "admin@example.com", "mot-de-passe-admin")
            system = admin.get("/api/admin/system")
            self.assertEqual(system.status_code, 200, system.text)
            self.assertIn("metrics", system.json())
            self.assertNotIn("database_url", system.json())

    def test_transcription_quote_uses_the_versioned_rate(self) -> None:
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            cloud_settings = replace(
                app_settings,
                transcription_backend="openai",
                semantic_analysis_backend="openai",
            )
            with patch("backend.app.transcription_api.settings", cloud_settings):
                quote = client.post(
                    "/api/transcription/quote", json={"duration_seconds": 90.1}
                )
            self.assertEqual(quote.status_code, 200, quote.text)
            self.assertEqual(quote.json()["model"], "whisper-1")
            self.assertEqual(quote.json()["billed_seconds"], 91)
            self.assertEqual(quote.json()["transcription_amount"], "0.009100")
            self.assertEqual(quote.json()["semantic_analysis"]["amount"], "0.000520")
            self.assertEqual(quote.json()["amount"], "0.009620")

    def test_processing_modes_change_the_quote_independently(self) -> None:
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            quotes = {}
            for transcription_mode, chaptering_mode in (
                ("cloud", "ai"),
                ("cloud", "local"),
                ("local", "ai"),
                ("local", "local"),
            ):
                response = client.post(
                    "/api/transcription/quote",
                    json={
                        "duration_seconds": 90.1,
                        "transcription_mode": transcription_mode,
                        "chaptering_mode": chaptering_mode,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                quotes[(transcription_mode, chaptering_mode)] = response.json()

            self.assertEqual(quotes[("cloud", "ai")]["amount"], "0.009620")
            self.assertEqual(quotes[("cloud", "local")]["amount"], "0.009100")
            self.assertEqual(quotes[("local", "ai")]["amount"], "0.000520")
            self.assertEqual(quotes[("local", "local")]["amount"], "0.000000")
            self.assertEqual(quotes[("local", "local")]["model"], "base")

    def test_local_modes_are_saved_on_the_job_without_cloud_usage(self) -> None:
        content = b"local-processing-placeholder"
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            project = client.post("/api/projects", json={"title": "Traitement local"})
            self.assertEqual(project.status_code, 201, project.text)
            project_id = project.json()["id"]
            reservation = client.post(
                "/api/uploads",
                json={
                    "project_id": project_id,
                    "filename": "local.wav",
                    "content_type": "audio/wav",
                    "size_bytes": len(content),
                },
            )
            self.assertEqual(reservation.status_code, 201, reservation.text)
            asset_id = reservation.json()["asset"]["id"]
            self.assertEqual(
                client.put(reservation.json()["upload"]["url"], content=content).status_code,
                200,
            )
            with patch("backend.app.uploads.manager.start"):
                launched = client.post(
                    "/api/jobs/from-asset",
                    json={
                        "asset_id": asset_id,
                        "estimated_duration_seconds": 90,
                        "transcription_mode": "local",
                        "chaptering_mode": "local",
                    },
                )
            self.assertEqual(launched.status_code, 202, launched.text)
            job_id = launched.json()["id"]
            self.assertEqual(
                launched.json()["processing_modes"],
                {"transcription": "local", "chaptering": "local"},
            )
            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == "pilot-a@example.com"))
                events = db.scalars(
                    select(UsageEvent).where(UsageEvent.job_id == job_id)
                ).all()
                self.assertEqual(events, [])
                owner_id = owner.id
            saved = manager.get(owner_id, job_id)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.model_name, "base")
            self.assertEqual(saved.options["transcription_mode"], "local")
            self.assertEqual(saved.options["chaptering_mode"], "local")
            manager.delete(saved)
            self.assertEqual(client.delete(f"/api/uploads/{asset_id}").status_code, 204)
            self.assertEqual(client.delete(f"/api/projects/{project_id}").status_code, 204)

    def test_budget_policy_requires_confirmation_and_groups_project_costs(self) -> None:
        content = b"budget-audio-placeholder"
        with TestClient(app) as client:
            self.login(client, "pilot-b@example.com", "mot-de-passe-b")
            project = client.post("/api/projects", json={"title": "Cours sous budget"})
            self.assertEqual(project.status_code, 201, project.text)
            project_id = project.json()["id"]
            policy = client.put(
                "/api/billing/policy",
                json={
                    "monthly_budget": "0.05",
                    "warning_percent": 80,
                    "approval_threshold": "0.005",
                    "currency": "USD",
                },
            )
            self.assertEqual(policy.status_code, 200, policy.text)
            self.assertEqual(policy.json()["monthly_budget"], "0.050000")

            paid_settings = replace(
                app_settings,
                transcription_backend="openai",
                semantic_analysis_backend="openai",
            )
            with patch("backend.app.transcription_api.settings", paid_settings):
                quote = client.post(
                    "/api/transcription/quote", json={"duration_seconds": 600}
                )
            self.assertEqual(quote.status_code, 200, quote.text)
            self.assertTrue(quote.json()["requires_confirmation"])
            self.assertEqual(
                set(quote.json()["confirmation_reasons"]),
                {"approval_threshold", "monthly_budget"},
            )
            self.assertEqual(quote.json()["budget_state"], "exceeded")

            reservation = client.post(
                "/api/uploads",
                json={
                    "project_id": project_id,
                    "filename": "budget.wav",
                    "content_type": "audio/wav",
                    "size_bytes": len(content),
                },
            )
            self.assertEqual(reservation.status_code, 201, reservation.text)
            asset_id = reservation.json()["asset"]["id"]
            self.assertEqual(
                client.put(reservation.json()["upload"]["url"], content=content).status_code,
                200,
            )
            with (
                patch("backend.app.uploads.settings", paid_settings),
                patch("backend.app.uploads.manager.start"),
            ):
                refused = client.post(
                    "/api/jobs/from-asset",
                    json={"asset_id": asset_id, "estimated_duration_seconds": 600},
                )
                self.assertEqual(refused.status_code, 409, refused.text)
                launched = client.post(
                    "/api/jobs/from-asset",
                    json={
                        "asset_id": asset_id,
                        "estimated_duration_seconds": 600,
                        "cost_confirmed": True,
                    },
                )
            self.assertEqual(launched.status_code, 202, launched.text)
            job_id = launched.json()["id"]

            summary = client.get("/api/billing/summary")
            self.assertEqual(summary.status_code, 200, summary.text)
            self.assertEqual(summary.json()["budget"]["state"], "exceeded")
            project_cost = next(
                item for item in summary.json()["projects"] if item["project_id"] == project_id
            )
            self.assertEqual(project_cost["estimated_cost"], "0.060960")
            self.assertEqual(project_cost["operations"], 3)

            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == "pilot-b@example.com"))
                self.assertIsNotNone(owner)
                policy_row = db.scalar(
                    select(BillingPolicy).where(BillingPolicy.user_id == owner.id)
                )
                self.assertIsNotNone(policy_row)
                db.delete(policy_row)
                budget_events = db.scalars(
                    select(UsageEvent).where(UsageEvent.job_id == job_id)
                ).all()
                for event in budget_events:
                    db.delete(event)
                db.commit()
                user_id = owner.id
            created_job = manager.get(user_id, job_id)
            self.assertIsNotNone(created_job)
            manager.delete(created_job)
            self.assertEqual(client.delete(f"/api/uploads/{asset_id}").status_code, 204)
            self.assertEqual(client.delete(f"/api/projects/{project_id}").status_code, 204)

    def test_projects_are_isolated_by_user(self) -> None:
        with TestClient(app) as client_a:
            self.login(client_a, "pilot-a@example.com", "mot-de-passe-a")
            created = client_a.post(
                "/api/projects",
                json={"title": "Cours pilote", "description": "Projet privé"},
            )
            self.assertEqual(created.status_code, 201, created.text)
            project_id = created.json()["id"]
            self.assertEqual(len(client_a.get("/api/projects").json()), 1)

        with TestClient(app) as client_b:
            self.login(client_b, "pilot-b@example.com", "mot-de-passe-b")
            self.assertEqual(client_b.get(f"/api/projects/{project_id}").status_code, 404)
            self.assertEqual(client_b.delete(f"/api/projects/{project_id}").status_code, 404)
            upload = client_b.post(
                "/api/jobs",
                data={"project_id": project_id, "model": "base", "language": "fr"},
                files={"file": ("sample.wav", b"not-an-audio", "audio/wav")},
            )
            self.assertEqual(upload.status_code, 404, upload.text)

        with SessionLocal() as db:
            owner = db.scalar(select(User).where(User.email == "pilot-a@example.com"))
            self.assertIsNotNone(owner)

    def test_project_title_cannot_be_blank(self) -> None:
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            response = client.post("/api/projects", json={"title": "   "})
            self.assertEqual(response.status_code, 422, response.text)

    def test_project_can_be_updated_and_deleted(self) -> None:
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            created = client.post("/api/projects", json={"title": "Titre initial"})
            self.assertEqual(created.status_code, 201, created.text)
            project_id = created.json()["id"]

            updated = client.put(
                f"/api/projects/{project_id}",
                json={"title": "Titre final", "description": "Description finale"},
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["title"], "Titre final")
            self.assertEqual(updated.json()["description"], "Description finale")

            deleted = client.delete(f"/api/projects/{project_id}")
            self.assertEqual(deleted.status_code, 204, deleted.text)
            self.assertEqual(client.get(f"/api/projects/{project_id}").status_code, 404)

    def test_image_and_video_templates_are_private_and_reusable(self) -> None:
        image_buffer = BytesIO()
        Image.new("RGB", (640, 360), "#17362c").save(image_buffer, format="PNG")
        image_content = image_buffer.getvalue()

        template_root = TEST_ROOT / "template-fixtures"
        template_root.mkdir(parents=True, exist_ok=True)
        audio_path = template_root / "audio.wav"
        cover_path = template_root / "cover.png"
        video_path = template_root / "reference.mp4"
        with wave.open(str(audio_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\x00\x00" * 8000)
        generate_cover(cover_path, "Template vidéo", "Référence")
        render_static_video(cover_path, audio_path, video_path)
        video_content = video_path.read_bytes()

        def upload_template(
            client: TestClient,
            *,
            name: str,
            filename: str,
            content_type: str,
            content: bytes,
            usage_mode: str,
        ) -> dict:
            reservation = client.post(
                "/api/brand/templates",
                json={
                    "name": name,
                    "filename": filename,
                    "content_type": content_type,
                    "size_bytes": len(content),
                    "usage_mode": usage_mode,
                    "frame_seconds": 0,
                },
            )
            self.assertEqual(reservation.status_code, 201, reservation.text)
            upload = client.put(
                reservation.json()["upload"]["url"],
                content=content,
                headers={"Content-Type": content_type},
            )
            self.assertEqual(upload.status_code, 204, upload.text)
            completed = client.post(
                f"/api/brand/templates/{reservation.json()['template']['id']}/complete"
            )
            self.assertEqual(completed.status_code, 200, completed.text)
            return completed.json()

        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            image_template = upload_template(
                client,
                name="Fond institutionnel",
                filename="fond.png",
                content_type="image/png",
                content=image_content,
                usage_mode="animated",
            )
            self.assertEqual(image_template["source_kind"], "image")
            self.assertEqual(image_template["usage_mode"], "static_frame")
            self.assertEqual(image_template["width"], 640)
            video_template = upload_template(
                client,
                name="Habillage animé",
                filename="reference.mp4",
                content_type="video/mp4",
                content=video_content,
                usage_mode="animated",
            )
            self.assertEqual(video_template["source_kind"], "video")
            self.assertEqual(video_template["usage_mode"], "animated")
            self.assertGreater(video_template["duration_seconds"], 0)
            templates = client.get("/api/brand/templates")
            self.assertEqual(templates.status_code, 200, templates.text)
            self.assertEqual(len(templates.json()), 2)
            preview = client.get(image_template["preview_url"])
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.headers["content-type"], "image/png")

            self.assertEqual(client.post("/api/auth/logout").status_code, 204)
            self.login(client, "pilot-b@example.com", "mot-de-passe-b")
            self.assertEqual(client.get("/api/brand/templates").json(), [])
            self.assertEqual(client.get(image_template["preview_url"]).status_code, 404)

            self.assertEqual(client.post("/api/auth/logout").status_code, 204)
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            self.assertEqual(
                client.delete(f"/api/brand/templates/{image_template['id']}").status_code,
                204,
            )
            self.assertEqual(
                client.delete(f"/api/brand/templates/{video_template['id']}").status_code,
                204,
            )

    def test_temporary_upload_is_private_validated_and_launches_from_asset(self) -> None:
        content = b"small-audio-placeholder"
        with TestClient(app) as client_a:
            self.login(client_a, "pilot-a@example.com", "mot-de-passe-a")
            project = client_a.post("/api/projects", json={"title": "Upload objet"})
            self.assertEqual(project.status_code, 201, project.text)
            project_id = project.json()["id"]
            rejected_video = client_a.post(
                "/api/uploads",
                json={
                    "project_id": project_id,
                    "filename": "video.wav",
                    "content_type": "video/mpeg",
                    "size_bytes": len(content),
                },
            )
            self.assertEqual(rejected_video.status_code, 422, rejected_video.text)
            reservation = client_a.post(
                "/api/uploads",
                json={
                    "project_id": project_id,
                    "filename": "WhatsApp Audio.mpeg",
                    "content_type": "video/mpeg",
                    "size_bytes": len(content),
                },
            )
            self.assertEqual(reservation.status_code, 201, reservation.text)
            payload = reservation.json()
            asset_id = payload["asset"]["id"]
            self.assertEqual(payload["asset"]["status"], "pending")
            self.assertEqual(payload["asset"]["original_name"], "WhatsApp Audio.mpeg")
            self.assertEqual(payload["upload"]["method"], "PUT")

            wrong_size = client_a.put(payload["upload"]["url"], content=b"short")
            self.assertEqual(wrong_size.status_code, 422, wrong_size.text)

        with TestClient(app) as client_b:
            self.login(client_b, "pilot-b@example.com", "mot-de-passe-b")
            forbidden = client_b.put(payload["upload"]["url"], content=content)
            self.assertEqual(forbidden.status_code, 404, forbidden.text)
            self.assertEqual(
                client_b.post(f"/api/uploads/{asset_id}/complete").status_code,
                404,
            )

        with TestClient(app) as client_a:
            self.login(client_a, "pilot-a@example.com", "mot-de-passe-a")
            uploaded = client_a.put(
                payload["upload"]["url"],
                content=content,
                headers={"Content-Type": "video/mpeg"},
            )
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            self.assertEqual(uploaded.json()["status"], "ready")
            self.assertEqual(
                uploaded.json()["checksum_sha256"], hashlib.sha256(content).hexdigest()
            )
            completed = client_a.post(f"/api/uploads/{asset_id}/complete")
            self.assertEqual(completed.status_code, 200, completed.text)

            with patch("backend.app.uploads.manager.start"):
                launched = client_a.post(
                    "/api/jobs/from-asset",
                    json={"asset_id": asset_id, "model": "base", "language": "fr"},
                )
            self.assertEqual(launched.status_code, 202, launched.text)
            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == "pilot-a@example.com"))
                self.assertIsNotNone(owner)
                job = manager.get(owner.id, launched.json()["id"])
            self.assertIsNotNone(job)
            self.assertEqual(job.source_asset_id, asset_id)
            self.assertEqual(job.input_path.read_bytes(), content)
            manager.delete(job)
            self.assertEqual(client_a.delete(f"/api/uploads/{asset_id}").status_code, 204)
            self.assertEqual(client_a.delete(f"/api/projects/{project_id}").status_code, 204)

    def test_jobs_can_be_listed_by_project(self) -> None:
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            created = client.post("/api/projects", json={"title": "Suivi des jobs"})
            self.assertEqual(created.status_code, 201, created.text)
            project_id = created.json()["id"]
            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == "pilot-a@example.com"))
                self.assertIsNotNone(owner)
                user_id = owner.id

            job = manager.create(user_id, project_id, "course.wav", "base", "fr", 1)
            try:
                restored = JobManager(manager.storage, manager.state_store).get(user_id, job.id)
                self.assertIsNotNone(restored)
                self.assertEqual(restored.project_id, project_id)
                response = client.get(f"/api/jobs?project_id={project_id}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([item["id"] for item in response.json()], [job.id])
                self.assertEqual(response.json()[0]["project_id"], project_id)
            finally:
                manager.delete(job)
                client.delete(f"/api/projects/{project_id}")

    def test_external_worker_persists_artifacts_for_api_download(self) -> None:
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            project = client.post("/api/projects", json={"title": "Worker séparé"})
            self.assertEqual(project.status_code, 201, project.text)
            project_id = project.json()["id"]
            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == "pilot-a@example.com"))
                self.assertIsNotNone(owner)
                asset = Asset(
                    user_id=owner.id,
                    project_id=project_id,
                    kind="source_audio",
                    original_name="worker.wav",
                    content_type="audio/wav",
                    size_bytes=6,
                    storage_key=(
                        f"users/{owner.id}/projects/{project_id}/assets/worker/source.wav"
                    ),
                    status="ready",
                    uploaded_at=utc_now(),
                    expires_at=utc_now() + timedelta(days=7),
                )
                db.add(asset)
                db.commit()
                db.refresh(asset)
                asset_id = asset.id
                user_id = owner.id

            source = TEST_ROOT / "worker-source.wav"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"source")
            media_storage.upload_file(
                f"users/{user_id}/projects/{project_id}/assets/worker/source.wav",
                source,
                "audio/wav",
            )
            job = manager.create(
                user_id,
                project_id,
                "worker.wav",
                "base",
                "fr",
                1,
                source_asset_id=asset_id,
                execution_backend="worker",
                allocate_workspace=False,
            )

            def fake_pipeline(input_path: Path, workspace: Path, **_kwargs) -> PipelineResult:
                self.assertEqual(input_path.read_bytes(), b"source")
                analysis = workspace / "analysis.json"
                audio = workspace / "audio-export.wav"
                cover = workspace / "cover.png"
                video = workspace / "video.mp4"
                write_analysis(
                    analysis,
                    input_path,
                    [
                        TranscriptSegment(0.0, 12.0, "Introduction du cours."),
                        TranscriptSegment(12.0, 30.0, "Développement du sujet."),
                    ],
                    [
                        CoursePart(
                            1,
                            0.0,
                            30.0,
                            "Partie initiale",
                            "Description initiale",
                            "Introduction du cours. Développement du sujet.",
                        )
                    ],
                )
                audio.write_bytes(b"audio")
                cover.write_bytes(b"cover")
                video.write_bytes(b"video")
                return PipelineResult(analysis, audio, cover, video, 2, 1, 30.0, 1.0)

            with patch("backend.worker.run_pipeline", side_effect=fake_pipeline):
                self.assertTrue(Worker("test-worker").process(job.id))

            completed = manager.get(user_id, job.id)
            self.assertIsNotNone(completed)
            self.assertEqual(completed.state, "completed")
            self.assertEqual(set(completed.artifacts), {"analysis", "audio", "cover", "video"})
            with SessionLocal() as db:
                artifacts = db.scalars(select(Artifact).where(Artifact.job_id == job.id)).all()
                self.assertEqual(len(artifacts), 4)
                self.assertTrue(all(item.checksum_sha256 for item in artifacts))
            downloaded = client.get(f"/api/jobs/{job.id}/artifacts/cover")
            self.assertEqual(downloaded.status_code, 200, downloaded.text)
            self.assertEqual(downloaded.content, b"cover")
            analysis_response = client.get(f"/api/jobs/{job.id}/analysis")
            self.assertEqual(analysis_response.status_code, 200, analysis_response.text)
            original = analysis_response.json()
            self.assertEqual(original["parts"][0]["title"], "Partie initiale")
            updated = client.put(
                f"/api/jobs/{job.id}/analysis",
                json={
                    "checksum_sha256": original["checksum_sha256"],
                    "parts": [
                        {
                            "index": 1,
                            "start": 0,
                            "end": 29.5,
                            "title": "Titre corrigé",
                            "description": "Résumé corrigé",
                        }
                    ],
                },
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["parts"][0]["title"], "Titre corrigé")
            self.assertIn("Développement du sujet", updated.json()["parts"][0]["transcript"])
            self.assertNotEqual(updated.json()["checksum_sha256"], original["checksum_sha256"])
            overlapping = client.put(
                f"/api/jobs/{job.id}/analysis",
                json={
                    "checksum_sha256": updated.json()["checksum_sha256"],
                    "parts": [
                        {
                            "index": 1,
                            "start": 0,
                            "end": 20,
                            "title": "Première partie",
                            "description": "",
                        },
                        {
                            "index": 2,
                            "start": 19,
                            "end": 29,
                            "title": "Deuxième partie",
                            "description": "",
                        },
                    ],
                },
            )
            self.assertEqual(overlapping.status_code, 422, overlapping.text)
            stale = client.put(
                f"/api/jobs/{job.id}/analysis",
                json={
                    "checksum_sha256": original["checksum_sha256"],
                    "parts": original["parts"],
                },
            )
            self.assertEqual(stale.status_code, 409, stale.text)
            export_requested = client.post(
                f"/api/jobs/{job.id}/exports/audio",
                json={
                    "checksum_sha256": updated.json()["checksum_sha256"],
                    "part_indices": [1],
                },
            )
            self.assertEqual(export_requested.status_code, 202, export_requested.text)
            export_id = export_requested.json()["id"]
            self.assertEqual(export_requested.json()["tool"], "audio_selection")
            self.assertEqual(export_requested.json()["parent_job_id"], job.id)
            self.assertEqual(
                export_requested.json()["content"],
                {"title": "Titre corrigé", "part_indices": [1]},
            )
            duplicate = client.post(
                f"/api/jobs/{job.id}/exports/audio",
                json={
                    "checksum_sha256": updated.json()["checksum_sha256"],
                    "part_indices": [1],
                },
            )
            self.assertEqual(duplicate.status_code, 202, duplicate.text)
            self.assertEqual(duplicate.json()["id"], export_id)

            def fake_export(source_path: Path, output_path: Path, ranges) -> None:
                self.assertEqual(source_path.read_bytes(), b"audio")
                self.assertEqual(ranges, [(0.0, 29.5)])
                output_path.write_bytes(b"selected-audio")

            with patch("backend.worker.export_clips", side_effect=fake_export):
                self.assertTrue(Worker("export-worker").process(export_id))
            exported = manager.get(user_id, export_id)
            self.assertIsNotNone(exported)
            self.assertEqual(exported.state, "completed")
            self.assertEqual(exported.tool, "audio_selection")
            self.assertEqual(exported.metrics["selected_parts"], 1)
            selection = client.get(
                f"/api/jobs/{export_id}/artifacts/selection_audio"
            )
            self.assertEqual(selection.status_code, 200, selection.text)
            self.assertEqual(selection.content, b"selected-audio")
            self.assertIn(
                "01-titre-corrige.wav",
                selection.headers["content-disposition"],
            )

            template_buffer = BytesIO()
            Image.new("RGB", (640, 360), "#17362c").save(template_buffer, format="PNG")
            template_content = template_buffer.getvalue()
            template_reservation = client.post(
                "/api/brand/templates",
                json={
                    "name": "Template du rendu",
                    "filename": "template.png",
                    "content_type": "image/png",
                    "size_bytes": len(template_content),
                    "usage_mode": "static_frame",
                },
            )
            self.assertEqual(template_reservation.status_code, 201, template_reservation.text)
            template_id = template_reservation.json()["template"]["id"]
            self.assertEqual(
                client.put(
                    template_reservation.json()["upload"]["url"],
                    content=template_content,
                    headers={"Content-Type": "image/png"},
                ).status_code,
                204,
            )
            completed_template = client.post(f"/api/brand/templates/{template_id}/complete")
            self.assertEqual(completed_template.status_code, 200, completed_template.text)
            configured_template = client.put(
                f"/api/brand/templates/{template_id}",
                json={
                    "name": "Template configuré",
                    "zones": [
                        {
                            "kind": "title",
                            "x": 0.1,
                            "y": 0.65,
                            "width": 0.8,
                            "height": 0.2,
                            "font_scale": 0.06,
                            "color": "#ffffff",
                            "align": "center",
                        }
                    ],
                },
            )
            self.assertEqual(configured_template.status_code, 200, configured_template.text)
            self.assertEqual(configured_template.json()["version"], 2)
            video_requested = client.post(
                f"/api/jobs/{job.id}/exports/video",
                json={
                    "checksum_sha256": updated.json()["checksum_sha256"],
                    "part_indices": [1],
                    "template_id": template_id,
                    "template_version": 2,
                    "output_format": "9:16",
                    "title": "Titre du rendu",
                    "speaker": "Intervenant",
                    "date": "",
                    "episode": "",
                },
            )
            self.assertEqual(video_requested.status_code, 202, video_requested.text)
            video_job_id = video_requested.json()["id"]
            self.assertEqual(
                video_requested.json()["content"],
                {"title": "Titre du rendu", "part_indices": [1]},
            )

            def fake_cover(_source, output, **kwargs) -> None:
                self.assertEqual(kwargs["output_format"], "9:16")
                self.assertEqual(kwargs["values"]["title"], "Titre du rendu")
                output.write_bytes(b"rendered-cover")

            def fake_video(_cover, _audio, output) -> None:
                output.write_bytes(b"rendered-video")

            with (
                patch("backend.worker.export_clips", side_effect=fake_export),
                patch("backend.worker.compose_cover", side_effect=fake_cover),
                patch("backend.worker.render_static_video", side_effect=fake_video),
            ):
                self.assertTrue(Worker("video-worker").process(video_job_id))
            rendered = manager.get(user_id, video_job_id)
            self.assertIsNotNone(rendered)
            self.assertEqual(rendered.state, "completed")
            self.assertEqual(rendered.metrics["template_version"], 2)
            rendered_video = client.get(f"/api/jobs/{video_job_id}/artifacts/video")
            self.assertEqual(rendered_video.status_code, 200, rendered_video.text)
            self.assertEqual(rendered_video.content, b"rendered-video")
            self.assertIn(
                "titre-du-rendu.mp4",
                rendered_video.headers["content-disposition"],
            )

            archive_requested = client.post(
                f"/api/jobs/{job.id}/exports/archive",
                json={
                    "checksum_sha256": updated.json()["checksum_sha256"],
                    "video_job_id": video_job_id,
                    "audio_export_job_id": export_id,
                },
            )
            self.assertEqual(archive_requested.status_code, 202, archive_requested.text)
            archive_job_id = archive_requested.json()["id"]
            self.assertEqual(archive_requested.json()["tool"], "archive_export")
            self.assertTrue(Worker("archive-worker").process(archive_job_id))
            archived = manager.get(user_id, archive_job_id)
            self.assertIsNotNone(archived)
            self.assertEqual(archived.state, "completed")
            self.assertEqual(archived.metrics["archive_files"], 4)
            archive_download = client.get(f"/api/jobs/{archive_job_id}/artifacts/archive")
            self.assertEqual(archive_download.status_code, 200, archive_download.text)
            self.assertGreater(len(archive_download.content), 100)

            restored_project = client.post(
                "/api/projects", json={"title": "Cours restauré"}
            )
            self.assertEqual(restored_project.status_code, 201, restored_project.text)
            restored_project_id = restored_project.json()["id"]
            archive_reservation = client.post(
                "/api/archives",
                json={
                    "project_id": restored_project_id,
                    "filename": "cours.dars",
                    "content_type": "application/vnd.dars-manager.archive",
                    "size_bytes": len(archive_download.content),
                },
            )
            self.assertEqual(archive_reservation.status_code, 201, archive_reservation.text)
            restored_asset_id = archive_reservation.json()["asset"]["id"]
            archive_upload = client.put(
                archive_reservation.json()["upload"]["url"],
                content=archive_download.content,
                headers={"Content-Type": "application/vnd.dars-manager.archive"},
            )
            self.assertEqual(archive_upload.status_code, 200, archive_upload.text)
            restored_request = client.post(f"/api/archives/{restored_asset_id}/import")
            self.assertEqual(restored_request.status_code, 202, restored_request.text)
            restored_job_id = restored_request.json()["id"]
            self.assertEqual(restored_request.json()["tool"], "archive_import")
            with (
                patch("backend.worker.audio_duration", return_value=29.5),
                patch("backend.worker.run_pipeline", side_effect=AssertionError("no transcription")),
            ):
                self.assertTrue(Worker("archive-import-worker").process(restored_job_id))
            restored_job = manager.get(user_id, restored_job_id)
            self.assertIsNotNone(restored_job)
            self.assertEqual(restored_job.state, "completed")
            self.assertEqual(restored_job.tool, "audio_pipeline")
            self.assertEqual(restored_job.metrics["transcription_calls"], 0)
            self.assertTrue(restored_job.metrics["imported_archive"])
            self.assertEqual(
                set(restored_job.artifacts), {"analysis", "audio", "cover", "video"}
            )
            restored_analysis = client.get(f"/api/jobs/{restored_job_id}/analysis")
            self.assertEqual(restored_analysis.status_code, 200, restored_analysis.text)
            self.assertEqual(restored_analysis.json()["parts"][0]["title"], "Titre corrigé")
            impact = client.get("/api/billing/impact")
            self.assertEqual(impact.status_code, 200, impact.text)
            self.assertEqual(impact.json()["courses_completed"], 1)
            self.assertEqual(impact.json()["videos_rendered"], 1)
            self.assertEqual(impact.json()["archives_restored"], 1)
            self.assertEqual(impact.json()["courses_published"], 0)
            self.assertEqual(impact.json()["completed_duration_seconds"], 30.0)
            self.assertGreater(impact.json()["generated_storage_bytes"], 0)
            with SessionLocal() as db:
                saved_analysis = db.scalar(
                    select(Artifact).where(
                        Artifact.job_id == job.id,
                        Artifact.kind == "analysis",
                    )
                )
                self.assertEqual(
                    saved_analysis.checksum_sha256,
                    updated.json()["checksum_sha256"],
                )
            self.assertEqual(client.post("/api/auth/logout").status_code, 204)
            self.login(client, "pilot-b@example.com", "mot-de-passe-b")
            self.assertEqual(client.get(f"/api/jobs/{job.id}/analysis").status_code, 404)
            self.assertEqual(client.get(f"/api/jobs/{export_id}").status_code, 404)
            self.assertEqual(client.post("/api/auth/logout").status_code, 204)
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            source_deleted = client.delete(f"/api/jobs/{job.id}/source")
            self.assertEqual(source_deleted.status_code, 200, source_deleted.text)
            self.assertIsNone(source_deleted.json()["source_asset_id"])
            with SessionLocal() as db:
                self.assertIsNone(db.get(Asset, asset_id))
            self.assertEqual(client.delete(f"/api/jobs/{job.id}").status_code, 204)
            self.assertEqual(client.get(f"/api/jobs/{export_id}").status_code, 404)
            self.assertEqual(client.get(f"/api/jobs/{video_job_id}").status_code, 404)
            self.assertEqual(client.delete(f"/api/projects/{project_id}").status_code, 204)
            self.assertEqual(client.delete(f"/api/jobs/{restored_job_id}/source").status_code, 200)
            self.assertEqual(client.delete(f"/api/jobs/{restored_job_id}").status_code, 204)
            self.assertEqual(client.delete(f"/api/projects/{restored_project_id}").status_code, 204)
            retained_impact = client.get("/api/billing/impact")
            self.assertEqual(retained_impact.status_code, 200, retained_impact.text)
            self.assertEqual(retained_impact.json()["courses_completed"], 1)
            self.assertEqual(retained_impact.json()["videos_rendered"], 1)
            self.assertEqual(client.delete(f"/api/brand/templates/{template_id}").status_code, 204)

    def test_completed_course_can_be_semantically_reanalyzed_without_transcription(self) -> None:
        class FakeSemanticAnalyzer:
            provider = "openai"
            model = "gpt-5.6-luna"

            def analyze(self, segments, _language):
                return SemanticAnalysisResult(
                    parts=(
                        CoursePart(
                            1,
                            segments[0].start,
                            segments[0].end,
                            "Définition du sujet principal",
                            "Le premier sous-sujet est défini précisément.",
                            segments[0].text,
                        ),
                        CoursePart(
                            2,
                            segments[1].start,
                            segments[1].end,
                            "Application à un cas distinct",
                            "Le cours applique ensuite la notion à un nouveau cas.",
                            segments[1].text,
                        ),
                    ),
                    call=SemanticAnalysisCall(
                        provider=self.provider,
                        model=self.model,
                        input_tokens=1_200,
                        output_tokens=240,
                        request_id="req_reanalysis_test",
                    ),
                )

        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            project = client.post("/api/projects", json={"title": "Réanalyse sémantique"})
            self.assertEqual(project.status_code, 201, project.text)
            project_id = project.json()["id"]
            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == "pilot-a@example.com"))
                self.assertIsNotNone(owner)
                user_id = owner.id

            source_job = manager.create(
                user_id,
                project_id,
                "cours.wav",
                "whisper-1",
                "fr",
                1,
                execution_backend="worker",
                allocate_workspace=False,
            )
            source_job.state = "completed"
            source_job.stage = "done"
            source_job.progress = 1.0
            source_job.metrics = {"segments": 2, "parts": 1, "duration_seconds": 240.0}
            manager.state_store.save(source_job.record())

            analysis_path = TEST_ROOT / f"semantic-{source_job.id}.json"
            write_analysis(
                analysis_path,
                Path("cours.wav"),
                [
                    TranscriptSegment(0.0, 120.0, "Définition complète du sujet."),
                    TranscriptSegment(120.0, 240.0, "Application à un autre cas."),
                ],
                [
                    CoursePart(
                        1,
                        0.0,
                        240.0,
                        "Ancien titre incorrect",
                        "Ancienne description.",
                        "Définition complète du sujet. Application à un autre cas.",
                    )
                ],
            )
            checksum = hashlib.sha256(analysis_path.read_bytes()).hexdigest()
            storage_key = (
                f"users/{user_id}/projects/{project_id}/jobs/{source_job.id}/analysis.json"
            )
            media_storage.upload_file(storage_key, analysis_path, "application/json")
            with SessionLocal() as db:
                db.add(
                    Artifact(
                        user_id=user_id,
                        project_id=project_id,
                        job_id=source_job.id,
                        kind="analysis",
                        mime_type="application/json",
                        size_bytes=analysis_path.stat().st_size,
                        storage_key=storage_key,
                        checksum_sha256=checksum,
                        storage_metered_at=utc_now(),
                        expires_at=utc_now() + timedelta(days=7),
                    )
                )
                db.commit()

            cloud_settings = replace(app_settings, semantic_analysis_backend="openai")
            with patch("backend.app.editor.settings", cloud_settings):
                quote = client.get(
                    f"/api/jobs/{source_job.id}/analysis/reanalysis-quote"
                )
                self.assertEqual(quote.status_code, 200, quote.text)
                created = client.post(
                    f"/api/jobs/{source_job.id}/analysis/reanalyze",
                    json={"checksum_sha256": checksum, "cost_confirmed": True},
                )
            self.assertEqual(created.status_code, 202, created.text)
            child_id = created.json()["id"]
            worker = Worker("semantic-worker")
            worker.semantic_analyzer = FakeSemanticAnalyzer()
            self.assertTrue(worker.process(child_id))

            child = manager.get(user_id, child_id)
            self.assertIsNotNone(child)
            self.assertEqual(child.state, "completed")
            refreshed = client.get(f"/api/jobs/{source_job.id}/analysis")
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            self.assertEqual(len(refreshed.json()["parts"]), 2)
            self.assertEqual(
                refreshed.json()["parts"][0]["title"],
                "Définition du sujet principal",
            )
            with SessionLocal() as db:
                events = db.scalars(
                    select(UsageEvent).where(
                        UsageEvent.job_id == child_id,
                        UsageEvent.service == "content_analysis",
                    )
                ).all()
                self.assertEqual(
                    sorted(event.status for event in events),
                    ["confirmed", "confirmed", "reconciled", "reconciled"],
                )
            self.assertEqual(client.delete(f"/api/jobs/{source_job.id}").status_code, 204)
            self.assertEqual(client.delete(f"/api/projects/{project_id}").status_code, 204)

    def test_paid_transcription_checkpoint_is_reused(self) -> None:
        class FakePaidProvider:
            provider = "openai"
            model = "whisper-1"

            def __init__(self) -> None:
                self.calls = 0

            def transcribe(self, _path: Path, _language: str) -> ProviderTranscription:
                self.calls += 1
                return ProviderTranscription(
                    segments=(TranscriptSegment(0.0, 1.0, "contenu"),),
                    duration_seconds=1.0,
                    request_id="req_checkpoint",
                )

        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            project = client.post("/api/projects", json={"title": "Reprise cloud"})
            self.assertEqual(project.status_code, 201, project.text)
            project_id = project.json()["id"]
            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == "pilot-a@example.com"))
                self.assertIsNotNone(owner)
                user_id = owner.id
            job = manager.create(user_id, project_id, "source.wav", "whisper-1", "fr", 1)
            chunk = job.workspace / "chunk.wav"
            chunk.write_bytes(b"normalized-audio")
            paid = FakePaidProvider()
            provider = CheckpointingTranscriptionProvider(
                paid,
                job,
                storage=media_storage,
                session_factory=SessionLocal,
                retention_seconds=604800,
            )
            first = provider.transcribe(chunk, "fr")
            second = provider.transcribe(chunk, "fr")
            self.assertEqual(paid.calls, 1)
            self.assertFalse(first.reused)
            self.assertTrue(second.reused)
            self.assertEqual(second.request_id, "req_checkpoint")
            with SessionLocal() as db:
                checkpoints = db.scalars(
                    select(Artifact).where(
                        Artifact.job_id == job.id,
                        Artifact.kind == "transcription_checkpoint",
                    )
                ).all()
                self.assertEqual(len(checkpoints), 1)
            job.state = "cancelled"
            manager.state_store.save(job.record())
            self.assertEqual(client.delete(f"/api/jobs/{job.id}").status_code, 204)
            self.assertEqual(client.delete(f"/api/projects/{project_id}").status_code, 204)

    def test_maintenance_meters_then_purges_expired_media(self) -> None:
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            project = client.post("/api/projects", json={"title": "Cycle de rétention"})
            self.assertEqual(project.status_code, 201, project.text)
            project_id = project.json()["id"]
            now = utc_now()
            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == "pilot-a@example.com"))
                self.assertIsNotNone(owner)
                asset = Asset(
                    user_id=owner.id,
                    project_id=project_id,
                    kind="source_audio",
                    original_name="expired.wav",
                    content_type="audio/wav",
                    size_bytes=1_000_000_000,
                    storage_key=(
                        f"users/{owner.id}/projects/{project_id}/assets/expired/source.wav"
                    ),
                    status="ready",
                    uploaded_at=now - timedelta(days=30),
                    storage_metered_at=now - timedelta(days=30),
                    expires_at=now,
                )
                db.add(asset)
                db.commit()
                db.refresh(asset)
                asset_id = asset.id
                user_id = owner.id
            source = TEST_ROOT / "expired-source.wav"
            source.write_bytes(b"expired")
            media_storage.upload_file(
                f"users/{user_id}/projects/{project_id}/assets/expired/source.wav",
                source,
                "audio/wav",
            )

            metered, removed = MaintenanceService().run_cycle()
            self.assertEqual(metered, 1_000_000)
            self.assertEqual(removed, 1)
            with SessionLocal() as db:
                self.assertIsNone(db.get(Asset, asset_id))
                event = db.scalar(
                    select(UsageEvent).where(
                        UsageEvent.idempotency_key
                        == f"storage:asset:{asset_id}:total:1000000"
                    )
                )
                self.assertIsNotNone(event)
                self.assertEqual(event.quantity, 1_000_000)
                self.assertEqual(event.amount_nanos, 0)
            self.assertEqual(client.delete(f"/api/projects/{project_id}").status_code, 204)

    def test_usage_and_manual_payment_are_isolated_and_auditable(self) -> None:
        with TestClient(app) as client_b:
            self.login(client_b, "pilot-b@example.com", "mot-de-passe-b")
            created = client_b.post("/api/projects", json={"title": "Cours financé"})
            self.assertEqual(created.status_code, 201, created.text)
            project_id = created.json()["id"]
            with SessionLocal() as db:
                billed_user = db.scalar(
                    select(User).where(User.email == "pilot-b@example.com")
                )
                self.assertIsNotNone(billed_user)
                estimated = record_usage(
                    db,
                    user_id=billed_user.id,
                    project_id=project_id,
                    job_id="job-cost-test",
                    provider="openai",
                    service="transcription",
                    model="whisper-1",
                    quantity=600,
                    unit="audio_second",
                    status="estimated",
                    idempotency_key=f"estimate-{TEST_ID}",
                )
                confirmed = record_usage(
                    db,
                    user_id=billed_user.id,
                    project_id=project_id,
                    job_id="job-cost-test",
                    provider="openai",
                    service="transcription",
                    model="whisper-1",
                    quantity=500,
                    unit="audio_second",
                    status="confirmed",
                    idempotency_key=f"confirmed-{TEST_ID}",
                    provider_request_id="provider-request-test",
                )
                repeated = record_usage(
                    db,
                    user_id=billed_user.id,
                    project_id=project_id,
                    job_id="job-cost-test",
                    provider="openai",
                    service="transcription",
                    model="whisper-1",
                    quantity=500,
                    unit="audio_second",
                    status="confirmed",
                    idempotency_key=f"confirmed-{TEST_ID}",
                )
                self.assertEqual(confirmed.id, repeated.id)
                self.assertEqual(estimated.amount_nanos, 60_000_000)
                self.assertEqual(confirmed.amount_nanos, 50_000_000)
                db.commit()
                billed_user_id = billed_user.id

            summary = client_b.get("/api/billing/summary?month=2026-09")
            self.assertEqual(summary.status_code, 200, summary.text)
            self.assertEqual(summary.json()["estimated_cost"], "0.060000")
            self.assertEqual(summary.json()["confirmed_cost"], "0.050000")
            self.assertEqual(summary.json()["balance"], "0.050000")
            csv_statement = client_b.get("/api/billing/statement.csv?month=2026-09")
            self.assertEqual(csv_statement.status_code, 200, csv_statement.text)
            self.assertIn("text/csv", csv_statement.headers["content-type"])
            self.assertIn("releve-dars-2026-09.csv", csv_statement.headers["content-disposition"])
            self.assertTrue(csv_statement.content.startswith(b"\xef\xbb\xbf"))
            self.assertIn("Cours financé".encode(), csv_statement.content)
            pdf_statement = client_b.get("/api/billing/statement.pdf?month=2026-09")
            self.assertEqual(pdf_statement.status_code, 200, pdf_statement.text)
            self.assertEqual(pdf_statement.headers["content-type"], "application/pdf")
            self.assertTrue(pdf_statement.content.startswith(b"%PDF-1.4"))
            self.assertTrue(pdf_statement.content.rstrip().endswith(b"%%EOF"))
            forbidden = client_b.post(
                "/api/admin/billing/payments",
                json={"user_id": billed_user_id, "amount": "0.03", "period": "2026-09"},
            )
            self.assertEqual(forbidden.status_code, 403, forbidden.text)

        with TestClient(app) as admin_client:
            self.login(admin_client, "admin@example.com", "mot-de-passe-admin")
            payment = admin_client.post(
                "/api/admin/billing/payments",
                json={
                    "user_id": billed_user_id,
                    "amount": "0.03",
                    "period": "2026-09",
                    "reference": "VIR-001",
                },
            )
            self.assertEqual(payment.status_code, 201, payment.text)
            matched_invoice = admin_client.post(
                "/api/admin/billing/provider-invoices",
                json={
                    "provider": "OpenAI",
                    "service": "transcription",
                    "reference": "OPENAI-2026-09",
                    "period": "2026-09",
                    "invoiced_amount": "0.05",
                    "tolerance": "0.000001",
                },
            )
            self.assertEqual(matched_invoice.status_code, 201, matched_invoice.text)
            self.assertEqual(matched_invoice.json()["internal_amount"], "0.050000")
            self.assertEqual(matched_invoice.json()["variance"], "0.000000")
            self.assertEqual(matched_invoice.json()["status"], "matched")
            duplicate_invoice = admin_client.post(
                "/api/admin/billing/provider-invoices",
                json={
                    "provider": "openai",
                    "service": "transcription",
                    "reference": "OPENAI-2026-09",
                    "period": "2026-09",
                    "invoiced_amount": "0.05",
                },
            )
            self.assertEqual(duplicate_invoice.status_code, 409, duplicate_invoice.text)
            variance_invoice = admin_client.post(
                "/api/admin/billing/provider-invoices",
                json={
                    "provider": "openai",
                    "service": "transcription",
                    "reference": "OPENAI-2026-09-CORRECTION",
                    "period": "2026-09",
                    "invoiced_amount": "0.055",
                },
            )
            self.assertEqual(variance_invoice.status_code, 201, variance_invoice.text)
            self.assertEqual(variance_invoice.json()["variance"], "0.005000")
            self.assertEqual(variance_invoice.json()["status"], "variance")
            invoices = admin_client.get(
                "/api/admin/billing/provider-invoices?month=2026-09"
            )
            self.assertEqual(invoices.status_code, 200, invoices.text)
            self.assertEqual(len(invoices.json()), 2)
            clients = admin_client.get("/api/admin/billing/clients?month=2026-09")
            self.assertEqual(clients.status_code, 200, clients.text)
            pilot_b = next(item for item in clients.json() if item["user"]["id"] == billed_user_id)
            self.assertEqual(pilot_b["paid"], "0.030000")
            self.assertEqual(pilot_b["balance"], "0.020000")

    def test_community_contributions_are_allocated_without_overfunding(self) -> None:
        community_email = f"community-{TEST_ID}@example.com"
        with SessionLocal() as db:
            db.add(
                User(
                    email=community_email,
                    display_name="Pilote communauté",
                    password_hash=hash_password("mot-de-passe-community"),
                    email_verified_at=utc_now(),
                )
            )
            db.commit()
        with TestClient(app) as client:
            self.login(client, community_email, "mot-de-passe-community")
            first = client.post("/api/projects", json={"title": "Cours communauté A"})
            second = client.post("/api/projects", json={"title": "Cours communauté B"})
            self.assertEqual(first.status_code, 201, first.text)
            self.assertEqual(second.status_code, 201, second.text)
            first_project_id = first.json()["id"]
            second_project_id = second.json()["id"]
            forbidden = client.post(
                "/api/admin/billing/community-contributions",
                json={"amount": "0.06", "contributor_name": "Soutien privé"},
            )
            self.assertEqual(forbidden.status_code, 403, forbidden.text)
            with SessionLocal() as db:
                owner = db.scalar(select(User).where(User.email == community_email))
                self.assertIsNotNone(owner)
                for project_id, project_title, suffix in (
                    (first_project_id, "Cours communauté A", "a"),
                    (second_project_id, "Cours communauté B", "b"),
                ):
                    event = UsageEvent(
                        user_id=owner.id,
                        project_id=project_id,
                        project_title=project_title,
                        job_id=f"community-job-{suffix}",
                        provider="community-test",
                        service="processing",
                        model="fixed-test-rate",
                        quantity=500,
                        unit="audio_second",
                        currency="USD",
                        amount_nanos=50_000_000,
                        status="confirmed",
                        idempotency_key=f"community-{suffix}-{TEST_ID}",
                    )
                    db.add(event)
                    self.assertEqual(event.amount_nanos, 50_000_000)
                db.commit()

        with TestClient(app) as admin:
            self.login(admin, "admin@example.com", "mot-de-passe-admin")
            created = admin.post(
                "/api/admin/billing/community-contributions",
                json={
                    "contributor_name": "Soutien privé",
                    "is_anonymous": True,
                    "amount": "0.06",
                    "reference": "COMM-001",
                    "campaign": "Cours de septembre",
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            contribution_id = created.json()["id"]
            self.assertEqual(created.json()["contributor_display"], "Anonyme")
            first_allocation = admin.post(
                "/api/admin/billing/community-allocations",
                json={
                    "contribution_id": contribution_id,
                    "project_id": first_project_id,
                    "period": "2026-09",
                    "amount": "0.04",
                    "category": "transcription",
                },
            )
            self.assertEqual(first_allocation.status_code, 201, first_allocation.text)
            cost_overflow = admin.post(
                "/api/admin/billing/community-allocations",
                json={
                    "contribution_id": contribution_id,
                    "project_id": first_project_id,
                    "period": "2026-09",
                    "amount": "0.02",
                },
            )
            self.assertEqual(cost_overflow.status_code, 409, cost_overflow.text)
            self.assertIn("coût confirmé", cost_overflow.json()["detail"])
            second_allocation = admin.post(
                "/api/admin/billing/community-allocations",
                json={
                    "contribution_id": contribution_id,
                    "project_id": second_project_id,
                    "period": "2026-09",
                    "amount": "0.02",
                },
            )
            self.assertEqual(second_allocation.status_code, 201, second_allocation.text)
            contribution_overflow = admin.post(
                "/api/admin/billing/community-allocations",
                json={
                    "contribution_id": contribution_id,
                    "project_id": second_project_id,
                    "period": "2026-09",
                    "amount": "0.001",
                },
            )
            self.assertEqual(contribution_overflow.status_code, 409, contribution_overflow.text)
            self.assertIn("contribution", contribution_overflow.json()["detail"])
            contributions = admin.get("/api/admin/billing/community-contributions")
            self.assertEqual(contributions.status_code, 200, contributions.text)
            contribution = next(
                item for item in contributions.json() if item["id"] == contribution_id
            )
            self.assertEqual(contribution["allocated"], "0.060000")
            self.assertEqual(contribution["remaining"], "0.000000")
            allocations = admin.get(
                "/api/admin/billing/community-allocations?month=2026-09"
            )
            self.assertEqual(allocations.status_code, 200, allocations.text)
            contribution_allocations = [
                item
                for item in allocations.json()
                if item["contribution_id"] == contribution_id
            ]
            self.assertEqual(len(contribution_allocations), 2)

        with TestClient(app) as client:
            self.login(client, community_email, "mot-de-passe-community")
            summary = client.get("/api/billing/summary?month=2026-09")
            self.assertEqual(summary.status_code, 200, summary.text)
            self.assertEqual(summary.json()["community_funded"], "0.060000")
            self.assertEqual(len(summary.json()["community_allocations"]), 2)
            projects = {
                item["project_id"]: item for item in summary.json()["projects"]
            }
            self.assertEqual(projects[first_project_id]["community_funded"], "0.040000")
            self.assertEqual(projects[first_project_id]["amount_due"], "0.010000")
            statement = client.get("/api/billing/statement.csv?month=2026-09")
            self.assertEqual(statement.status_code, 200, statement.text)
            self.assertIn("Financement communautaire".encode(), statement.content)


if __name__ == "__main__":
    unittest.main()
