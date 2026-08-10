"""
Object storage wrapper. Talks to any S3-compatible endpoint (real AWS S3, or
MinIO for local/self-hosted dev) via boto3. When `use_local_storage_fallback`
is set (the default), and no S3 credentials/endpoint are configured, falls
back to writing under a local directory instead -- so the service runs with
zero external dependencies out of the box.

Usage is deliberately tiny: put_file / put_bytes / get_bytes / presigned_url /
delete. Everything above this layer (pipeline, workers, API) just deals in
storage keys (strings) and never touches boto3 or the filesystem directly.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from config import get_settings

logger = logging.getLogger("video2notes.storage")


class StorageBackend:
    def put_file(self, local_path: Path, key: str) -> str:
        raise NotImplementedError

    def put_bytes(self, data: bytes, key: str) -> str:
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def get_file(self, key: str, dest_path: Path) -> Path:
        raise NotImplementedError

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError


class LocalStorageBackend(StorageBackend):
    """Filesystem-backed storage, keyed the same way as S3 (forward-slash
    paths). Used for local dev when S3/MinIO isn't configured."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.base_dir / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put_file(self, local_path: Path, key: str) -> str:
        dest = self._path(key)
        shutil.copyfile(local_path, dest)
        return key

    def put_bytes(self, data: bytes, key: str) -> str:
        self._path(key).write_bytes(data)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def get_file(self, key: str, dest_path: Path) -> Path:
        shutil.copyfile(self._path(key), dest_path)
        return dest_path

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        # No real HTTP serving in local mode -- callers use get_bytes/get_file
        # directly, or a dev server that maps /local_storage/* to this dir.
        return f"/local_storage/{key}"

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3StorageBackend(StorageBackend):
    def __init__(self, bucket: str, endpoint_url: Optional[str], access_key: Optional[str],
                 secret_key: Optional[str], region: str):
        import boto3
        self.bucket = bucket
        self.client = boto3.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name=region,
        )
        try:
            self.client.head_bucket(Bucket=bucket)
        except Exception:
            logger.info(f"Bucket '{bucket}' not found or inaccessible; attempting to create it.")
            try:
                self.client.create_bucket(Bucket=bucket)
            except Exception as e:
                logger.warning(f"Could not create bucket '{bucket}': {e}")

    def put_file(self, local_path: Path, key: str) -> str:
        self.client.upload_file(str(local_path), self.bucket, key)
        return key

    def put_bytes(self, data: bytes, key: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get_bytes(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def get_file(self, key: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(dest_path))
        return dest_path

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in,
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False


_backend: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    """Returns the process-wide storage backend, picking S3 vs local
    filesystem based on settings (see config.py)."""
    global _backend
    if _backend is not None:
        return _backend

    settings = get_settings()
    has_s3_config = bool(settings.s3_endpoint_url or (settings.s3_access_key and settings.s3_secret_key))

    if has_s3_config:
        try:
            _backend = S3StorageBackend(
                bucket=settings.s3_bucket, endpoint_url=settings.s3_endpoint_url,
                access_key=settings.s3_access_key, secret_key=settings.s3_secret_key,
                region=settings.s3_region,
            )
            logger.info("Using S3-compatible storage backend.")
            return _backend
        except Exception as e:
            logger.warning(f"Failed to initialize S3 storage ({e}); falling back to local storage.")

    if not settings.use_local_storage_fallback:
        raise RuntimeError(
            "No S3 configuration found and use_local_storage_fallback is disabled. "
            "Set S3_ENDPOINT_URL/S3_ACCESS_KEY/S3_SECRET_KEY or enable the local fallback."
        )
    _backend = LocalStorageBackend(settings.local_storage_dir)
    logger.info(f"Using local filesystem storage backend at {settings.local_storage_dir}")
    return _backend
