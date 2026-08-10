"""
Celery task: extracts the audio track from the original media and transcribes
it (local faster-whisper or OpenAI API), merges choppy segments into
sentences, and stores them as TranscriptSegment rows.
"""

import logging

from celery_app import celery_app
from config import get_settings
from models.transcript import TranscriptSegment
from models.video import Video, VideoStatus
from pipeline.audio import extract_audio_track
from pipeline.transcript import merge_into_sentences, transcribe_api, transcribe_local
from storage import get_storage
from workers.common import db_session, mark_failed, set_status, temp_workdir

logger = logging.getLogger("video2notes.workers.transcription")


@celery_app.task(name="workers.transcription.process_transcription", bind=True, max_retries=1)
def process_transcription(self, video_id: str):
    settings = get_settings()
    storage = get_storage()

    try:
        with db_session() as db:
            video = db.get(Video, video_id)
            if not video:
                logger.error(f"Video {video_id} not found")
                return
            original_key = video.original_storage_key
            media_type = video.media_type
            cfg = video.pipeline_config or {}

        with temp_workdir() as work_dir:
            local_media = work_dir / "source_media"
            storage.get_file(original_key, local_media)

            audio_path = work_dir / "audio.wav"
            extract_audio_track(local_media, audio_path)

            engine = cfg.get("transcription_engine", settings.whisper_engine)
            language = cfg.get("language", "en")

            if engine == "api":
                api_key = cfg.get("openai_api_key") or settings.openai_api_key
                segments = transcribe_api(audio_path, api_key=api_key, language=language)
            else:
                segments = transcribe_local(
                    audio_path,
                    model_name=cfg.get("whisper_model", settings.whisper_local_model),
                    device=settings.whisper_local_device,
                    compute_type=settings.whisper_local_compute_type,
                    language=language,
                )

            merged = merge_into_sentences(segments)

            with db_session() as db:
                for i, seg in enumerate(merged):
                    db.add(TranscriptSegment(video_id=video_id, order_index=i,
                                              start_seconds=seg["start"], end_seconds=seg["end"],
                                              text=seg["text"]))
                set_status(db, video_id, VideoStatus.SUMMARIZING.value, progress_pct=55)
            logger.info(f"Video {video_id}: {len(merged)} transcript segments saved.")

        if media_type == "audio":
            from workers.summarization import process_summarization
            process_summarization.delay(video_id)
        else:
            from workers.audio import process_audio_notes
            process_audio_notes.delay(video_id)

    except Exception as e:
        mark_failed(video_id, e)
        raise
