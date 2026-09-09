from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Settings


@dataclass(frozen=True)
class ObjectInfo:
    size_bytes: int
    content_type: str


class MediaStorage:
    """Storage contract shared by local development and S3 production."""

    backend: str

    def upload_target(
        self,
        key: str,
        content_type: str,
        size_bytes: int,
        local_url: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def stat(self, key: str) -> ObjectInfo:
        raise NotImplementedError

    def download_file(self, key: str, destination: Path) -> None:
        raise NotImplementedError

    def upload_file(self, key: str, source: Path, content_type: str) -> None:
        raise NotImplementedError

    def download_url(self, key: str, filename: str, ttl_seconds: int = 900) -> str | None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalMediaStorage(MediaStorage):
    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path_for(self, key: str) -> Path:
        logical = PurePosixPath(key)
        if logical.is_absolute() or not logical.parts or any(
            part in {"", ".", ".."} for part in logical.parts
        ):
            raise ValueError("Invalid object key")
        path = self.root.joinpath(*logical.parts).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Object key outside media storage") from exc
        return path

    def upload_target(
        self,
        key: str,
        content_type: str,
        size_bytes: int,
        local_url: str,
    ) -> dict[str, Any]:
        self.path_for(key)
        return {"method": "PUT", "url": local_url, "fields": {}}

    def stat(self, key: str) -> ObjectInfo:
        path = self.path_for(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return ObjectInfo(size_bytes=path.stat().st_size, content_type="")

    def download_file(self, key: str, destination: Path) -> None:
        source = self.path_for(key)
        if not source.is_file():
            raise FileNotFoundError(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    def upload_file(self, key: str, source: Path, content_type: str) -> None:
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        partial = destination.with_name(f".{destination.name}.part")
        shutil.copyfile(source, partial)
        partial.replace(destination)

    def download_url(self, key: str, filename: str, ttl_seconds: int = 900) -> None:
        self.path_for(key)
        return None

    def delete(self, key: str) -> None:
        path = self.path_for(key)
        path.unlink(missing_ok=True)
        current = path.parent
        while current != self.root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


class S3MediaStorage(MediaStorage):
    backend = "s3"

    def __init__(
        self,
        bucket: str,
        region: str,
        endpoint_url: str | None,
        upload_ttl_seconds: int,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError("boto3 is required when DARSM_MEDIA_BACKEND=s3") from exc
        self.bucket = bucket
        self.upload_ttl_seconds = upload_ttl_seconds
        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(signature_version="s3v4"),
        )

    def upload_target(
        self,
        key: str,
        content_type: str,
        size_bytes: int,
        local_url: str,
    ) -> dict[str, Any]:
        result = self.client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", size_bytes, size_bytes],
            ],
            ExpiresIn=self.upload_ttl_seconds,
        )
        return {"method": "POST", "url": result["url"], "fields": result["fields"]}

    def stat(self, key: str) -> ObjectInfo:
        response = self.client.head_object(Bucket=self.bucket, Key=key)
        return ObjectInfo(
            size_bytes=int(response["ContentLength"]),
            content_type=str(response.get("ContentType", "")),
        )

    def download_file(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))

    def upload_file(self, key: str, source: Path, content_type: str) -> None:
        self.client.upload_file(
            str(source),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def download_url(self, key: str, filename: str, ttl_seconds: int = 900) -> str:
        safe_filename = filename.replace('"', "").replace("\r", "").replace("\n", "")
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{safe_filename}"',
            },
            ExpiresIn=ttl_seconds,
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def create_media_storage(settings: Settings) -> MediaStorage:
    if settings.media_backend == "local":
        return LocalMediaStorage(settings.media_root)
    if not settings.s3_bucket:
        raise RuntimeError("DARSM_S3_BUCKET is required when DARSM_MEDIA_BACKEND=s3")
    return S3MediaStorage(
        bucket=settings.s3_bucket,
        region=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url,
        upload_ttl_seconds=settings.media_upload_url_ttl_seconds,
    )
