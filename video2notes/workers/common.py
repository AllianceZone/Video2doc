"""Shared helpers for Celery task modules (workers/*.py)."""

import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path

from database import SessionLocal
from models.video import Video

logger = logging.getLogger("video2notes.workers")


@contextmanager
def db_session():
    """Celery tasks run outside FastAPI's request lifecycle, so they open
    their own short-lived DB session instead of using the `get_db` dependency."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def temp_workdir(prefix: str = "v2n_"):
    with tempfile.TemporaryDirectory(prefix=prefix) as d:
        yield Path(d)


def set_status(db, video_id: str, status: str, progress_pct: int = None, error_message: str = None):
    video = db.get(Video, video_id)
    if not video:
        logger.warning(f"set_status: video {video_id} not found")
        return
    video.status = status
    if progress_pct is not None:
        video.progress_pct = progress_pct
    if error_message is not None:
        video.error_message = error_message
    db.add(video)
    db.commit()


def mark_failed(video_id: str, error: Exception):
    with db_session() as db:
        set_status(db, video_id, "failed", error_message=str(error))
    logger.exception(f"Video {video_id} processing failed: {error}")
