from __future__ import annotations

import os
import hashlib
import shutil
import unittest
import uuid
from datetime import timedelta
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

from backend.app.costs import record_usage
from backend.app.database import CURRENT_REVISION, SessionLocal, engine, init_database
from backend.app.main import app, manager
from backend.app.jobs import JobManager
from backend.app.models import Artifact, Asset, UsageEvent, User, utc_now
from backend.app.pipeline import PipelineResult, write_analysis
from backend.app.runtime import media_storage
from backend.app.security import hash_password
from backend.app.transcription import ProviderTranscription
from backend.app.transcription_checkpoint import CheckpointingTranscriptionProvider
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

    def test_transcription_quote_uses_the_versioned_rate(self) -> None:
        with TestClient(app) as client:
            self.login(client, "pilot-a@example.com", "mot-de-passe-a")
            quote = client.post(
                "/api/transcription/quote", json={"duration_seconds": 90.1}
            )
            self.assertEqual(quote.status_code, 200, quote.text)
            self.assertEqual(quote.json()["model"], "whisper-1")
            self.assertEqual(quote.json()["billed_seconds"], 91)
            self.assertEqual(quote.json()["amount"], "0.009100")

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

    def test_temporary_upload_is_private_validated_and_launches_from_asset(self) -> None:
        content = b"small-audio-placeholder"
        with TestClient(app) as client_a:
            self.login(client_a, "pilot-a@example.com", "mot-de-passe-a")
            project = client_a.post("/api/projects", json={"title": "Upload objet"})
            self.assertEqual(project.status_code, 201, project.text)
            project_id = project.json()["id"]
            reservation = client_a.post(
                "/api/uploads",
                json={
                    "project_id": project_id,
                    "filename": "cours.wav",
                    "content_type": "audio/wav",
                    "size_bytes": len(content),
                },
            )
            self.assertEqual(reservation.status_code, 201, reservation.text)
            payload = reservation.json()
            asset_id = payload["asset"]["id"]
            self.assertEqual(payload["asset"]["status"], "pending")
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
                headers={"Content-Type": "audio/wav"},
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
