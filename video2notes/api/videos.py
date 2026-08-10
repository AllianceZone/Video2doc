import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from api.deps import get_current_user, get_db
from config import get_settings
from models.note import SummaryGroup
from models.user import User
from models.video import Video, VideoStatus
from schemas.video import SummaryGroupOut, VideoCreate, VideoOut
from storage import get_storage

router = APIRouter(prefix="/videos", tags=["videos"])


def _pipeline_config_from_create(payload: VideoCreate) -> dict:
    return {
        "video_quality": payload.video_quality,
        "frame_mode": payload.frame_mode,
        "frame_interval_seconds": payload.frame_interval_seconds,
        "scene_threshold": payload.scene_threshold,
        "dedup_enabled": payload.dedup_enabled,
        "hamming_threshold": payload.hamming_threshold,
        "transcription_engine": payload.transcription_engine,
        "whisper_model": payload.whisper_model,
        "language": payload.language,
        "chunking_enabled": payload.chunking_enabled,
        "similarity_threshold": payload.similarity_threshold,
        "max_chunk_seconds": payload.max_chunk_seconds,
        "max_frames_per_chunk": payload.max_frames_per_chunk,
        "generate_audio_notes": payload.generate_audio_notes,
        "summary_group_size": payload.summary_group_size,
        "max_summary_group_seconds": payload.max_summary_group_seconds,
        "llm_provider": payload.llm_provider,
    }


def _enqueue_processing(video_id: str):
    from workers.screenshots import process_frames
    process_frames.delay(video_id)


@router.post("", response_model=VideoOut, status_code=status.HTTP_201_CREATED)
def create_video(payload: VideoCreate, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """Create a video-processing job from a URL/path source (youtube, gdrive,
    zoho, or a server-local path). For direct file uploads, use POST /videos/upload."""
    if payload.source_type not in ("youtube", "gdrive", "zoho", "local_path"):
        raise HTTPException(status_code=422, detail="source_type must be youtube, gdrive, zoho, or local_path")

    video = Video(owner_id=current_user.id, title=payload.title, source_type=payload.source_type,
                  source_ref=payload.source_ref, pipeline_config=_pipeline_config_from_create(payload))
    db.add(video)
    db.commit()
    db.refresh(video)

    _enqueue_processing(video.id)
    db.refresh(video)  # in eager-Celery mode the task already ran synchronously above; pick up its final state
    return _video_out(video)


@router.post("/upload", response_model=VideoOut, status_code=status.HTTP_201_CREATED)
def upload_video(
    file: UploadFile = File(...),
    title: str = Form(""),
    frame_mode: str = Form("hybrid"),
    transcription_engine: str = Form("local"),
    whisper_model: str = Form("medium"),
    language: str = Form("en"),
    chunking_enabled: bool = Form(True),
    generate_audio_notes: bool = Form(True),
    llm_provider: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a video or audio file directly (mp4/mov/mkv/webm, or mp3/wav/etc.
    for audio-only). Streams to storage in chunks to handle large files."""
    storage = get_storage()
    video_id = str(uuid.uuid4())
    safe_name = Path(file.filename or "upload").name
    upload_key = f"videos/{video_id}/uploads/{safe_name}"

    tmp_path = Path(f"/tmp/{video_id}_{safe_name}")
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f, length=16 * 1024 * 1024)
        storage.put_file(tmp_path, upload_key)
    finally:
        tmp_path.unlink(missing_ok=True)

    cfg = {
        "video_quality": "1080p", "frame_mode": frame_mode, "dedup_enabled": True,
        "transcription_engine": transcription_engine, "whisper_model": whisper_model,
        "language": language, "chunking_enabled": chunking_enabled,
        "generate_audio_notes": generate_audio_notes, "llm_provider": llm_provider or None,
    }
    video = Video(id=video_id, owner_id=current_user.id, title=title or Path(safe_name).stem,
                  source_type="upload", source_ref=upload_key, pipeline_config=cfg)
    db.add(video)
    db.commit()
    db.refresh(video)

    _enqueue_processing(video.id)
    db.refresh(video)  # in eager-Celery mode the task already ran synchronously above; pick up its final state
    return _video_out(video)


@router.get("", response_model=List[VideoOut])
def list_videos(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    videos = db.query(Video).filter(Video.owner_id == current_user.id).order_by(Video.created_at.desc()).all()
    return [_video_out(v) for v in videos]


@router.get("/{video_id}", response_model=VideoOut)
def get_video(video_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    video = _get_owned_video(db, video_id, current_user)
    return _video_out(video)


@router.get("/{video_id}/sections", response_model=List[SummaryGroupOut])
def get_video_sections(video_id: str, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    """The chunked-report structure: summary groups, each with its nested
    sections (frames + audio note + transcript)."""
    _get_owned_video(db, video_id, current_user)
    groups = (db.query(SummaryGroup).filter(SummaryGroup.video_id == video_id)
              .order_by(SummaryGroup.start_seconds).all())
    return groups


@router.get("/{video_id}/frames/{frame_id}/image")
def get_frame_image(video_id: str, frame_id: str, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    from models.transcript import Frame
    video = _get_owned_video(db, video_id, current_user)
    frame = db.get(Frame, frame_id)
    if not frame or frame.video_id != video.id:
        raise HTTPException(status_code=404, detail="Frame not found")
    return _stream_or_redirect(frame.storage_key, "image/jpeg")


@router.get("/{video_id}/download/{doc_format}")
def download_document(video_id: str, doc_format: str, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    video = _get_owned_video(db, video_id, current_user)
    if not video.note:
        raise HTTPException(status_code=404, detail="Documents not ready yet")
    key_map = {
        "docx": (video.note.docx_storage_key,
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "pptx": (video.note.pptx_storage_key,
                 "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        "transcript.json": (video.note.transcript_json_storage_key, "application/json"),
        "transcript.srt": (video.note.transcript_srt_storage_key, "text/plain"),
        "transcript.txt": (video.note.transcript_txt_storage_key, "text/plain"),
    }
    if doc_format not in key_map:
        raise HTTPException(status_code=404, detail=f"Unknown format '{doc_format}'")
    key, mime = key_map[doc_format]
    if not key:
        raise HTTPException(status_code=404, detail="That document was not generated for this video")
    return _stream_or_redirect(key, mime)


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(video_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    video = _get_owned_video(db, video_id, current_user)
    db.delete(video)
    db.commit()


# ---------------------------------------------------------------------------

def _get_owned_video(db: Session, video_id: str, current_user: User) -> Video:
    video = db.get(Video, video_id)
    if not video or video.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")
    return video


def _video_out(video: Video) -> VideoOut:
    return VideoOut(
        id=video.id, title=video.title, source_type=video.source_type, media_type=video.media_type,
        status=video.status, error_message=video.error_message, progress_pct=video.progress_pct,
        duration_seconds=video.duration_seconds,
        created_at=video.created_at.isoformat(), updated_at=video.updated_at.isoformat(),
    )


def _stream_or_redirect(storage_key: str, mime_type: str):
    settings = get_settings()
    storage = get_storage()
    if hasattr(storage, "presigned_url") and settings.s3_endpoint_url:
        return RedirectResponse(storage.presigned_url(storage_key))
    data = storage.get_bytes(storage_key)
    import io
    return StreamingResponse(io.BytesIO(data), media_type=mime_type)
