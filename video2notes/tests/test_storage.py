"""Tests storage/s3.py's S3StorageBackend against a mocked S3 (moto), so the
real AWS/MinIO code path gets exercised too, not just the local fallback."""

import pytest
from moto import mock_aws

from storage.s3 import S3StorageBackend


@mock_aws
def test_s3_backend_roundtrip(tmp_path):
    backend = S3StorageBackend(bucket="test-bucket", endpoint_url=None, access_key="fake",
                                secret_key="fake", region="us-east-1")

    # put_bytes / get_bytes
    backend.put_bytes(b"hello world", "docs/test.txt")
    assert backend.get_bytes("docs/test.txt") == b"hello world"
    assert backend.exists("docs/test.txt") is True
    assert backend.exists("docs/missing.txt") is False

    # put_file / get_file
    src = tmp_path / "source.bin"
    src.write_bytes(b"\x00\x01\x02binary-content")
    backend.put_file(src, "bin/source.bin")
    dest = tmp_path / "downloaded.bin"
    backend.get_file("bin/source.bin", dest)
    assert dest.read_bytes() == src.read_bytes()

    # presigned URL is a real (mocked) URL
    url = backend.presigned_url("docs/test.txt")
    assert "test-bucket" in url or "docs/test.txt" in url

    # delete
    backend.delete("docs/test.txt")
    assert backend.exists("docs/test.txt") is False


@mock_aws
def test_s3_backend_creates_missing_bucket():
    # No bucket exists yet -- backend should create it rather than failing
    backend = S3StorageBackend(bucket="brand-new-bucket", endpoint_url=None, access_key="fake",
                                secret_key="fake", region="us-east-1")
    backend.put_bytes(b"x", "a.txt")
    assert backend.get_bytes("a.txt") == b"x"
