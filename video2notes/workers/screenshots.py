"""
Celery task: resolves the video's source (download if needed), and -- for
video input -- extracts and deduplicates frames, uploading survivors to
object storage and recording them as Frame rows. Audio-only input skips
straight past this stage to transcription.
"""

import logging
from pathlib import Path

from celery_app import celery_app
from config import get_settings
from models.transcript import Frame
from models.video import Video, VideoStatus
from pipeline.audio import is_audio_file
from pipeline.video import extract_frames, get_media_duration, resolve_source
from pipeline.vision import deduplicate_frames, sharpness_score
from storage import get_storage
from workers.common import db_session, mark_failed, set_status, temp_workdir

logger = logging.getLogger("video2notes.workers.screenshots")


@celery_app.task(name="workers.screenshots.process_frames", bind=True, max_retries=2)
def process_frames(self, video_id: str):
    settings = get_settings()
    storage = get_storage()

    try:
        with db_session() as db:
            video = db.get(Video, video_id)
            if not video:
                logger.error(f"Video {video_id} not found")
                return
            source_type, source_ref = video.source_type, video.source_ref
            cfg = video.pipeline_config or {}
            set_status(db, video_id, VideoStatus.RESOLVING_SOURCE.value, progress_pct=5)

        with temp_workdir() as work_dir:
            # For uploads, source_ref is a storage key -- pull it down first.
            if source_type == "upload":
                local_path = work_dir / Path(source_ref).name
                storage.get_file(source_ref, local_path)
            else:
                local_path = resolve_source(
                    source_type, source_ref, work_dir,
                    video_quality=cfg.get("video_quality", "1080p"),
                )

            duration = get_media_duration(local_path)
            audio_only = is_audio_file(local_path)

            with db_session() as db:
                video = db.get(Video, video_id)
                video.duration_seconds = duration
                video.media_type = "audio" if audio_only else "video"
                if not video.title:
                    video.title = local_path.stem
                db.add(video)

                original_key = f"videos/{video_id}/original/{local_path.name}"
                storage.put_file(local_path, original_key)
                video.original_storage_key = original_key
                db.add(video)
                db.commit()

            if audio_only:
                logger.info(f"Video {video_id}: audio-only input, skipping frame extraction.")
                with db_session() as db:
                    set_status(db, video_id, VideoStatus.TRANSCRIBING.value, progress_pct=25)
            else:
                set_status_local = VideoStatus.EXTRACTING_FRAMES.value
                with db_session() as db:
                    set_status(db, video_id, set_status_local, progress_pct=10)

                raw_frames = extract_frames(
                    local_path, work_dir / "frames_raw",
                    mode=cfg.get("frame_mode", "hybrid"),
                    interval_seconds=cfg.get("frame_interval_seconds", settings.frame_interval_seconds),
                    scene_threshold=cfg.get("scene_threshold", settings.frame_scene_threshold),
                    jpg_quality=95,
                )

                with db_session() as db:
                    set_status(db, video_id, VideoStatus.DEDUPLICATING.value, progress_pct=18)

                kept_frames = deduplicate_frames(
                    raw_frames, work_dir / "frames_deduped",
                    enabled=cfg.get("dedup_enabled", True),
                    hamming_threshold=cfg.get("hamming_threshold", settings.dedup_hamming_threshold),
                )

                with db_session() as db:
                    for f in kept_frames:
                        stamp = f.stem.replace("frame_", "")
                        h, m, s = (int(x) for x in stamp.split("-"))
                        ts = h * 3600 + m * 60 + s
                        key = f"videos/{video_id}/frames/{f.name}"
                        storage.put_file(f, key)
                        db.add(Frame(video_id=video_id, timestamp_seconds=ts, storage_key=key,
                                      sharpness_score=sharpness_score(f), is_kept=True))
                    set_status(db, video_id, VideoStatus.TRANSCRIBING.value, progress_pct=30)
                logger.info(f"Video {video_id}: {len(kept_frames)} frames kept and uploaded.")

        from workers.transcription import process_transcription
        process_transcription.delay(video_id)

    except Exception as e:
        mark_failed(video_id, e)
        raise
