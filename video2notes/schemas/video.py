from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class VideoCreate(BaseModel):
    """Used for URL-based sources (youtube/gdrive/zoho/local_path). File
    uploads go through a separate multipart endpoint instead."""
    source_type: str = Field(..., description="youtube | gdrive | zoho | local_path")
    source_ref: str = Field(..., description="URL or local path")
    title: str = ""
    video_quality: str = "1080p"

    # Pipeline overrides -- omit to use server defaults (config.py)
    frame_mode: str = "hybrid"
    frame_interval_seconds: Optional[int] = None
    scene_threshold: Optional[float] = None
    dedup_enabled: bool = True
    hamming_threshold: Optional[int] = None
    transcription_engine: Optional[str] = None      # "local" | "api"
    whisper_model: Optional[str] = None
    language: str = "en"
    chunking_enabled: bool = True
    similarity_threshold: Optional[float] = None
    max_chunk_seconds: Optional[float] = None
    max_frames_per_chunk: Optional[int] = None
    generate_audio_notes: bool = True
    summary_group_size: Optional[int] = None
    max_summary_group_seconds: Optional[float] = None
    llm_provider: Optional[str] = None               # "local" | "openai"


class VideoOut(BaseModel):
    id: str
    title: str
    source_type: str
    media_type: str
    status: str
    error_message: str
    progress_pct: int
    duration_seconds: float
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class FrameOut(BaseModel):
    id: str
    timestamp_seconds: float
    storage_key: str
    is_representative: bool

    model_config = ConfigDict(from_attributes=True)


class TranscriptSegmentOut(BaseModel):
    start_seconds: float
    end_seconds: float
    text: str

    model_config = ConfigDict(from_attributes=True)


class SectionOut(BaseModel):
    id: str
    start_seconds: float
    end_seconds: float
    transcript_text: str
    audio_clip_storage_key: str
    frames: list[FrameOut] = []

    model_config = ConfigDict(from_attributes=True)


class SummaryGroupOut(BaseModel):
    id: str
    start_seconds: float
    end_seconds: float
    summary: str
    key_points: list[str]
    sections: list[SectionOut] = []

    model_config = ConfigDict(from_attributes=True)
