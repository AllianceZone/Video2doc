"""
Celery task: groups frames into semantically-coherent sections (topically
similar + time-adjacent), cuts a short audio-note clip for each section's
time window, and stores both as Section rows. Only runs for video input
(audio-only input has no frames to group -- it goes straight to summarization).
"""

import logging
from pathlib import Path

from celery_app import celery_app
from config import get_settings
from models.note import Section
from models.transcript import Frame, TranscriptSegment
from models.video import Video, VideoStatus
from pipeline.audio import extract_audio_clip
from pipeline.notes import compute_sections, representative_images
from storage import get_storage
from workers.common import db_session, mark_failed, set_status, temp_workdir

logger = logging.getLogger("video2notes.workers.audio")


@celery_app.task(name="workers.audio.process_audio_notes", bind=True, max_retries=1)
def process_audio_notes(self, video_id: str):
    settings = get_settings()
    storage = get_storage()

    try:
        with db_session() as db:
            video = db.get(Video, video_id)
            if not video:
                logger.error(f"Video {video_id} not found")
                return
            cfg = video.pipeline_config or {}
            original_key = video.original_storage_key

            frame_rows = db.query(Frame).filter(Frame.video_id == video_id, Frame.is_kept == True).all()  # noqa: E712
            segment_rows = db.query(TranscriptSegment).filter(TranscriptSegment.video_id == video_id).all()
            # Extract plain data before the session closes -- ORM instances
            # become detached once their session ends, and this task's later
            # stages (downloading files, computing sections) run outside any
            # session, so touching lazy attributes on `frame_rows`/`segment_rows`
            # after this point would raise DetachedInstanceError.
            frame_data = [{"id": r.id, "storage_key": r.storage_key} for r in frame_rows]
            segments = [{"start": r.start_seconds, "end": r.end_seconds, "text": r.text} for r in segment_rows]

        if not cfg.get("chunking_enabled", True) or not frame_data:
            logger.info(f"Video {video_id}: chunking disabled or no frames; skipping section grouping.")
            with db_session() as db:
                set_status(db, video_id, VideoStatus.SUMMARIZING.value, progress_pct=65)
            from workers.summarization import process_summarization
            process_summarization.delay(video_id)
            return

        with temp_workdir() as work_dir:
            # Pull frame images + original media locally to compute sections and clips
            frames_dir = work_dir / "frames"
            frames_dir.mkdir()
            frame_paths = []
            frame_id_by_path = {}
            for fd in frame_data:
                local_path = frames_dir / Path(fd["storage_key"]).name
                storage.get_file(fd["storage_key"], local_path)
                frame_paths.append(local_path)
                frame_id_by_path[local_path] = fd["id"]

            local_media = work_dir / "source_media"
            storage.get_file(original_key, local_media)

            sections = compute_sections(
                frame_paths, segments,
                similarity_threshold=cfg.get("similarity_threshold", settings.chunk_similarity_threshold),
                max_chunk_seconds=cfg.get("max_chunk_seconds", settings.chunk_max_seconds),
                max_frames_per_chunk=cfg.get("max_frames_per_chunk", settings.chunk_max_frames),
            )

            audio_notes_dir = work_dir / "audio_notes"
            want_audio = cfg.get("generate_audio_notes", True)

            with db_session() as db:
                for section in sections:
                    audio_key = ""
                    if want_audio:
                        clip = extract_audio_clip(local_media, section["start"], section["end"], audio_notes_dir)
                        if clip:
                            key = f"videos/{video_id}/audio_notes/{clip.name}"
                            storage.put_file(clip, key)
                            audio_key = key

                    transcript_text = " ".join(s["text"].strip() for s in section["segments"]).strip()
                    section_row = Section(video_id=video_id, start_seconds=section["start"],
                                           end_seconds=section["end"], transcript_text=transcript_text,
                                           audio_clip_storage_key=audio_key)
                    db.add(section_row)
                    db.flush()  # get section_row.id

                    rep_images = set(representative_images(section))
                    for frame_path in section["frames"]:
                        frame_row = db.get(Frame, frame_id_by_path[frame_path])
                        frame_row.section_id = section_row.id
                        frame_row.is_representative = frame_path in rep_images
                        db.add(frame_row)

                set_status(db, video_id, VideoStatus.SUMMARIZING.value, progress_pct=65)
            logger.info(f"Video {video_id}: {len(sections)} sections computed with audio notes.")

        from workers.summarization import process_summarization
        process_summarization.delay(video_id)

    except Exception as e:
        mark_failed(video_id, e)
        raise
