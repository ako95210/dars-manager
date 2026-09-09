from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path


TEST_ID = uuid.uuid4().hex
TEST_ROOT = Path("/dev/shm") / f"dars-api-tests-{TEST_ID}"
TEST_DATABASE = Path("/dev/shm") / f"dars-api-tests-{TEST_ID}.db"
os.environ["DARSM_TEMP_ROOT"] = str(TEST_ROOT)
os.environ["DARSM_DATABASE_URL"] = f"sqlite+pysqlite:///{TEST_DATABASE}"
os.environ["DARSM_COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from backend.app.costs import record_usage
from backend.app.database import CURRENT_REVISION, SessionLocal, engine, init_database
from backend.app.main import app, manager
from backend.app.jobs import JobManager
from backend.app.models import User
from backend.app.security import hash_password


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
                        role="admin",
                    ),
                    User(
                        email="pilot-b@example.com",
                        display_name="Pilote B",
                        password_hash=hash_password("mot-de-passe-b"),
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
            self.assertEqual(client.get("/api/auth/me").status_code, 401)
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
            forbidden = client_b.post(
                "/api/admin/billing/payments",
                json={"user_id": billed_user_id, "amount": "0.03", "period": "2026-09"},
            )
            self.assertEqual(forbidden.status_code, 403, forbidden.text)

        with TestClient(app) as admin_client:
            self.login(admin_client, "pilot-a@example.com", "mot-de-passe-a")
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
            clients = admin_client.get("/api/admin/billing/clients?month=2026-09")
            self.assertEqual(clients.status_code, 200, clients.text)
            pilot_b = next(item for item in clients.json() if item["user"]["id"] == billed_user_id)
            self.assertEqual(pilot_b["paid"], "0.030000")
            self.assertEqual(pilot_b["balance"], "0.020000")


if __name__ == "__main__":
    unittest.main()
