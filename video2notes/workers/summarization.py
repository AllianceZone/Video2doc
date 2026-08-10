"""
Celery task: the final stage. Groups sections together for real (non-tiny)
summaries, calls the configured LLM provider for each group's takeaways and
for the overall video summary, builds the Word/PowerPoint artifacts, uploads
everything, and marks the video complete.
"""

import json
import logging
from pathlib import Path

from celery_app import celery_app
from config import get_settings
from llm import get_llm_provider
from models.note import Note, Section, SummaryGroup
from models.transcript import Frame, TranscriptSegment
from models.video import Video, VideoStatus
from pipeline.notes import (
    build_audio_docx, build_audio_pptx, build_docx, build_pptx,
    group_sections_for_summary, summarize_group,
)
from storage import get_storage
from workers.common import db_session, mark_failed, set_status, temp_workdir

logger = logging.getLogger("video2notes.workers.summarization")


@celery_app.task(name="workers.summarization.process_summarization", bind=True, max_retries=1)
def process_summarization(self, video_id: str):
    settings = get_settings()
    storage = get_storage()
    provider = get_llm_provider()

    try:
        with db_session() as db:
            video = db.get(Video, video_id)
            if not video:
                logger.error(f"Video {video_id} not found")
                return
            cfg = video.pipeline_config or {}
            media_type = video.media_type
            title = video.title or "Untitled"

            segment_rows = (db.query(TranscriptSegment)
                             .filter(TranscriptSegment.video_id == video_id)
                             .order_by(TranscriptSegment.start_seconds).all())
            segments = [{"start": r.start_seconds, "end": r.end_seconds, "text": r.text} for r in segment_rows]

        full_text = " ".join(s["text"].strip() for s in segments if s["text"].strip())
        overall = provider.summarize(full_text, style="narrative", max_summary_sentences=5, max_key_points=8)

        with db_session() as db:
            set_status(db, video_id, VideoStatus.BUILDING_DOCUMENTS.value, progress_pct=80)

        with temp_workdir() as work_dir:
            docx_path = work_dir / "video_report.docx"
            pptx_path = work_dir / "video_report.pptx"

            if media_type == "audio":
                build_audio_docx(segments, title, docx_path, summary=overall["summary"],
                                  key_points=overall["key_points"])
                build_audio_pptx(segments, title, pptx_path, summary=overall["summary"],
                                  key_points=overall["key_points"])
            else:
                with db_session() as db:
                    section_rows = (db.query(Section).filter(Section.video_id == video_id)
                                     .order_by(Section.start_seconds).all())
                    sections = []
                    for row in section_rows:
                        frame_rows = (db.query(Frame).filter(Frame.section_id == row.id,
                                                               Frame.is_representative == True)  # noqa: E712
                                       .order_by(Frame.timestamp_seconds).all())
                        sections.append({
                            "id": row.id, "start": row.start_seconds, "end": row.end_seconds,
                            "segments": [], "transcript_text": row.transcript_text,
                            "frame_storage_keys": [f.storage_key for f in frame_rows],
                            "audio_clip_storage_key": row.audio_clip_storage_key,
                        })
                        # re-attach this section's own transcript segments for grouping
                        sections[-1]["segments"] = [
                            s for s in segments if row.start_seconds <= s["start"] < row.end_seconds
                        ]

                if sections:
                    groups = group_sections_for_summary(
                        [{"start": s["start"], "end": s["end"], "segments": s["segments"], "_ref": s}
                         for s in sections],
                        group_size=cfg.get("summary_group_size", settings.summary_group_size),
                        max_group_seconds=cfg.get("max_summary_group_seconds", settings.summary_max_group_seconds),
                    )

                    # Download frame images + audio clips locally for document building
                    frames_dir, audio_dir = work_dir / "frames", work_dir / "audio_notes"
                    frames_dir.mkdir(); audio_dir.mkdir()

                    with db_session() as db:
                        for group in groups:
                            cr = summarize_group(group, provider, style="action_items",
                                                  max_summary_sentences=3, max_key_points=6)
                            group_row = SummaryGroup(video_id=video_id, start_seconds=group["start"],
                                                      end_seconds=group["end"], summary=cr["summary"],
                                                      key_points=cr["key_points"])
                            db.add(group_row)
                            db.flush()
                            group["summary"], group["key_points"] = cr["summary"], cr["key_points"]

                            for sec in group["sections"]:
                                ref = sec["_ref"]
                                db_section = db.get(Section, ref["id"])
                                db_section.group_id = group_row.id
                                db.add(db_section)

                                images = []
                                for key in ref["frame_storage_keys"]:
                                    local_img = frames_dir / Path(key).name
                                    if not local_img.exists():
                                        storage.get_file(key, local_img)
                                    images.append(local_img)
                                audio_clip = None
                                if ref["audio_clip_storage_key"]:
                                    local_clip = audio_dir / Path(ref["audio_clip_storage_key"]).name
                                    if not local_clip.exists():
                                        storage.get_file(ref["audio_clip_storage_key"], local_clip)
                                    audio_clip = local_clip
                                sec["images"] = images
                                sec["audio_clip"] = audio_clip
                                sec["transcript_text"] = ref["transcript_text"]

                    build_docx(groups, title, docx_path, overall_summary=overall["summary"],
                               overall_key_points=overall["key_points"],
                               image_width_inches=cfg.get("image_width_inches", 5.5))
                    build_pptx(groups, title, pptx_path, overall_summary=overall["summary"],
                               overall_key_points=overall["key_points"])
                else:
                    # No sections (chunking disabled / no frames) -- fall back to the
                    # audio-style transcript report so there's still a usable document.
                    build_audio_docx(segments, title, docx_path, summary=overall["summary"],
                                      key_points=overall["key_points"])
                    build_audio_pptx(segments, title, pptx_path, summary=overall["summary"],
                                      key_points=overall["key_points"])

            # Transcript export files
            json_path = work_dir / "transcript.json"
            json_path.write_text(json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8")
            srt_path = work_dir / "transcript.srt"
            srt_path.write_text(_to_srt(segments), encoding="utf-8")
            txt_path = work_dir / "transcript.txt"
            txt_path.write_text("\n".join(f"[{_hhmmss(s['start'])}] {s['text']}" for s in segments),
                                 encoding="utf-8")

            with db_session() as db:
                docx_key = f"videos/{video_id}/documents/video_report.docx"
                pptx_key = f"videos/{video_id}/documents/video_report.pptx"
                storage.put_file(docx_path, docx_key)
                storage.put_file(pptx_path, pptx_key)
                storage.put_file(json_path, f"videos/{video_id}/documents/transcript.json")
                storage.put_file(srt_path, f"videos/{video_id}/documents/transcript.srt")
                storage.put_file(txt_path, f"videos/{video_id}/documents/transcript.txt")

                note = Note(
                    video_id=video_id, summary=overall["summary"], key_points=overall["key_points"],
                    docx_storage_key=docx_key, pptx_storage_key=pptx_key,
                    transcript_json_storage_key=f"videos/{video_id}/documents/transcript.json",
                    transcript_srt_storage_key=f"videos/{video_id}/documents/transcript.srt",
                    transcript_txt_storage_key=f"videos/{video_id}/documents/transcript.txt",
                )
                db.add(note)
                set_status(db, video_id, VideoStatus.COMPLETED.value, progress_pct=100)
            logger.info(f"Video {video_id}: processing complete.")

    except Exception as e:
        mark_failed(video_id, e)
        raise


def _hhmmss(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _to_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        def srt_ts(sec):
            ms = int(round((sec - int(sec)) * 1000))
            h, rem = divmod(int(sec), 3600)
            m, s = divmod(rem, 60)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        lines += [str(i), f"{srt_ts(seg['start'])} --> {srt_ts(seg['end'])}", seg["text"], ""]
    return "\n".join(lines)
