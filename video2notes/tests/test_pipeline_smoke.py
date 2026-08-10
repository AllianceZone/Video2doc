"""
End-to-end smoke test: register -> upload a real (synthetic) video ->
run the full pipeline synchronously (Celery eager mode) -> assert the video
reaches COMPLETED with real generated documents in storage -> search finds
transcript content -> notes can be edited.

The only thing mocked is the actual Whisper model download (blocked without
internet in some environments) -- transcribe_local is patched to return fixed
segments. Every other stage (frame extraction via ffmpeg, dedup, semantic
chunking, audio-note clipping, local LLM summarization, docx/pptx building,
storage, DB) runs for real.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///./test_video2notes.db"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["USE_LOCAL_STORAGE_FALLBACK"] = "true"
os.environ["LOCAL_STORAGE_DIR"] = "./test_local_storage"
os.environ["LLM_PROVIDER"] = "local"

import pytest
from fastapi.testclient import TestClient

from database import Base, engine
from main import app

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.drop_all(bind=engine)
    import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def _make_test_video() -> Path:
    tmp_dir = Path(tempfile.mkdtemp())
    video_path = tmp_dir / "test_video.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=5:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(video_path), "-loglevel", "error",
    ], check=True)
    return video_path


FAKE_SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "This is the first thing we discuss in this recording."},
    {"start": 2.0, "end": 4.5, "text": "It covers the basic setup of the new dashboard."},
    {"start": 4.5, "end": 6.0, "text": "Next we look at how leads get assigned to sales reps."},
    {"start": 6.0, "end": 8.5, "text": "The assignment rule is based on region and account size."},
    {"start": 8.5, "end": 10.0, "text": "Finally we review the reporting export options."},
    {"start": 10.0, "end": 12.0, "text": "Exports can be scheduled daily or triggered manually."},
]


def _register_and_login(email="smoketest@example.com", password="testpass123"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password, "full_name": "Smoke Test"})
    resp = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_full_pipeline_via_upload():
    headers = _register_and_login()
    video_path = _make_test_video()

    with patch("workers.transcription.transcribe_local", return_value=FAKE_SEGMENTS):
        with open(video_path, "rb") as f:
            resp = client.post(
                "/api/v1/videos/upload",
                headers=headers,
                files={"file": ("test_video.mp4", f, "video/mp4")},
                data={
                    "title": "Smoke Test Video",
                    "frame_mode": "fixed",
                    "transcription_engine": "local",
                    "chunking_enabled": "true",
                    "generate_audio_notes": "true",
                },
            )
    assert resp.status_code == 201, resp.text
    video = resp.json()
    video_id = video["id"]
    assert video["status"] == "completed", f"Expected completed, got {video['status']}: {video}"
    assert video["duration_seconds"] > 10

    # Video detail
    resp = client.get(f"/api/v1/videos/{video_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    # Sections (chunked structure) were created
    resp = client.get(f"/api/v1/videos/{video_id}/sections", headers=headers)
    assert resp.status_code == 200
    groups = resp.json()
    assert len(groups) >= 1, "Expected at least one summary group"
    total_sections = sum(len(g["sections"]) for g in groups)
    assert total_sections >= 1, "Expected at least one section"
    for g in groups:
        for section in g["sections"]:
            assert len(section["frames"]) >= 1, "Each section should have at least one representative frame"

    # Note (overall summary)
    resp = client.get(f"/api/v1/notes/{video_id}", headers=headers)
    assert resp.status_code == 200
    note = resp.json()
    assert note["docx_available"] is True
    assert note["pptx_available"] is True

    # Downloads actually contain real files
    resp = client.get(f"/api/v1/videos/{video_id}/download/docx", headers=headers)
    assert resp.status_code == 200
    assert len(resp.content) > 1000, "docx seems too small to be real"

    resp = client.get(f"/api/v1/videos/{video_id}/download/pptx", headers=headers)
    assert resp.status_code == 200
    assert len(resp.content) > 1000, "pptx seems too small to be real"

    resp = client.get(f"/api/v1/videos/{video_id}/download/transcript.srt", headers=headers)
    assert resp.status_code == 200
    assert b"-->" in resp.content

    # Search finds transcript content
    resp = client.get("/api/v1/search", headers=headers, params={"q": "reporting export"})
    assert resp.status_code == 200
    results = resp.json()
    assert any("export" in r["snippet"].lower() for r in results), f"Search didn't find expected content: {results}"

    # Edit the note
    resp = client.patch(f"/api/v1/notes/{video_id}", headers=headers,
                         json={"summary": "Edited summary for test.", "key_points": ["Point A", "Point B"]})
    assert resp.status_code == 200
    assert resp.json()["summary"] == "Edited summary for test."


def test_auth_required():
    resp = client.get("/api/v1/videos")
    assert resp.status_code == 401


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
