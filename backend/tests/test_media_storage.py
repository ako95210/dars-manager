from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.media_storage import LocalMediaStorage, S3MediaStorage


class FakeS3Client:
    def __init__(self) -> None:
        self.arguments = {}

    def generate_presigned_post(self, **kwargs):
        self.arguments = kwargs
        return {"url": "https://storage.example/upload", "fields": {"key": kwargs["Key"]}}


class LocalMediaStorageTests(unittest.TestCase):
    def test_object_key_cannot_escape_storage_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalMediaStorage(Path(directory))
            for key in ("../secret", "/absolute", "user/../../secret"):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    storage.path_for(key)

    def test_download_and_delete_follow_storage_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = LocalMediaStorage(root / "objects")
            key = "users/user-a/projects/project-a/assets/asset-a/source.wav"
            source = storage.path_for(key)
            source.parent.mkdir(parents=True)
            source.write_bytes(b"audio")

            self.assertEqual(storage.stat(key).size_bytes, 5)
            destination = root / "workspace" / "input.wav"
            storage.download_file(key, destination)
            self.assertEqual(destination.read_bytes(), b"audio")
            storage.delete(key)
            self.assertFalse(source.exists())


class S3MediaStorageTests(unittest.TestCase):
    def test_presigned_post_is_short_lived_and_size_limited(self) -> None:
        storage = object.__new__(S3MediaStorage)
        storage.bucket = "private-media"
        storage.upload_ttl_seconds = 900
        storage.client = FakeS3Client()

        target = storage.upload_target(
            "users/user-a/assets/asset-a/source.wav",
            "audio/wav",
            1234,
            "/unused",
        )

        self.assertEqual(target["method"], "POST")
        self.assertEqual(storage.client.arguments["ExpiresIn"], 900)
        self.assertIn(
            ["content-length-range", 1234, 1234],
            storage.client.arguments["Conditions"],
        )


if __name__ == "__main__":
    unittest.main()
