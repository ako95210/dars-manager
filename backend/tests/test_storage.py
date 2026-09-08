from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.app.storage import TemporaryStorage


class TemporaryStorageTests(unittest.TestCase):
    def test_workspace_is_isolated_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = TemporaryStorage(Path(directory), ttl_seconds=60)
            workspace = storage.create_workspace("user-a", "job-a")
            self.assertEqual(workspace, Path(directory) / "user-a" / "job-a")
            (workspace / "private.bin").write_bytes(b"private")
            storage.remove_workspace(workspace)
            self.assertFalse(workspace.exists())

    def test_expired_workspace_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = TemporaryStorage(Path(directory), ttl_seconds=0)
            workspace = storage.create_workspace("user-a", "job-a")
            time.sleep(0.01)
            self.assertEqual(storage.cleanup_expired(), 1)
            self.assertFalse(workspace.exists())

    def test_workspace_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = TemporaryStorage(Path(directory), ttl_seconds=60)
            with self.assertRaises(ValueError):
                storage.create_workspace("../another-user", "job-a")


if __name__ == "__main__":
    unittest.main()
