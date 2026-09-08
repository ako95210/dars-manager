from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.app.job_state import MemoryJobStateStore
from backend.app.jobs import JobManager
from backend.app.storage import TemporaryStorage


class JobStateTests(unittest.TestCase):
    def test_job_can_be_read_by_another_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = TemporaryStorage(Path(directory), ttl_seconds=60)
            state = MemoryJobStateStore(ttl_seconds=60)
            first = JobManager(storage, state)
            created = first.create("user-a", "course.wav", "base", "fr", 4)

            second = JobManager(storage, state)
            restored = second.get("user-a", created.id)

            self.assertIsNotNone(restored)
            self.assertEqual(restored.id, created.id)
            self.assertEqual(restored.workspace, created.workspace)
            self.assertIsNone(second.get("user-b", created.id))

    def test_interrupted_job_is_marked_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = TemporaryStorage(Path(directory), ttl_seconds=60)
            state = MemoryJobStateStore(ttl_seconds=60)
            first = JobManager(storage, state)
            created = first.create("user-a", "course.wav", "base", "fr", 4)
            created.state = "running"
            first._save(created)

            second = JobManager(storage, state)
            self.assertEqual(second.recover_interrupted(), 1)
            recovered = second.get("user-a", created.id)

            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.state, "failed")
            self.assertEqual(recovered.stage, "interrupted")

    def test_memory_state_expires(self) -> None:
        state = MemoryJobStateStore(ttl_seconds=0.01)
        state.save({"id": "job-a"})
        time.sleep(0.02)
        self.assertIsNone(state.get("job-a"))
