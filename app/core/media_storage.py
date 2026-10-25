"""Local and S3-compatible storage for uploaded source media."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import Config

from app.core.config import get_settings


class MediaStorage:
    def __init__(self):
        self.settings = get_settings()
        if self.settings.storage_backend in {"minio", "s3"}:
            self.client = boto3.client(
                "s3",
                endpoint_url=f"http://{self.settings.minio_endpoint}",
                aws_access_key_id=self.settings.minio_access_key.get_secret_value() if self.settings.minio_access_key else None,
                aws_secret_access_key=self.settings.minio_secret_key.get_secret_value() if self.settings.minio_secret_key else None,
                config=Config(signature_version="s3v4"),
                region_name="us-east-1",
            )
        else:
            self.client = None

    async def save_upload(self, upload: BinaryIO, storage_key: str) -> str:
        if self.client is not None:
            await asyncio.to_thread(
                self.client.upload_fileobj,
                upload,
                self.settings.minio_bucket_name,
                storage_key,
            )
            return f"s3://{self.settings.minio_bucket_name}/{storage_key}"

        destination = self._local_path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._copy_stream, upload, destination)
        return str(destination.resolve())

    async def delete(self, storage_key: str) -> None:
        if self.client is not None:
            await asyncio.to_thread(self.client.delete_object, Bucket=self.settings.minio_bucket_name, Key=storage_key)
            return
        path = self._local_path(storage_key)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def local_source_path(self, storage_key: str) -> str:
        if self.client is None:
            return str(self._local_path(storage_key).resolve())
        bucket = self.settings.minio_bucket_name
        key = storage_key
        destination = self._local_path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        s3 = self.client
        await asyncio.to_thread(s3.download_file, bucket, key, str(destination))
        return str(destination.resolve())

    async def ensure_bucket(self) -> None:
        if self.client is None:
            return
        bucket = self.settings.minio_bucket_name
        exists = await asyncio.to_thread(self.client.list_buckets)
        if not any(value["Name"] == bucket for value in exists.get("Buckets", [])):
            await asyncio.to_thread(self.client.create_bucket, Bucket=bucket)

    def _local_path(self, storage_key: str) -> Path:
        root = self.settings.storage_path.resolve()
        path = (root / storage_key).resolve()
        if path != root and root not in path.parents:
            raise ValueError("Invalid media storage key.")
        return path

    @staticmethod
    def _copy_stream(source: BinaryIO, destination: Path) -> None:
        with destination.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)


def safe_media_extension(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return ".media"
    return suffix
