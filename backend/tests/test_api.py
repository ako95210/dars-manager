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

from backend.app.database import SessionLocal, engine, init_database
from backend.app.main import app
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
        self.assertEqual(revision, "20260908_0001")

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


if __name__ == "__main__":
    unittest.main()
