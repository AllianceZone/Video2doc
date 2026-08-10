import enum

from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.base import IDMixin, TimestampMixin


class VideoStatus(str, enum.Enum):
    PENDING = "pending"
    RESOLVING_SOURCE = "resolving_source"
    EXTRACTING_FRAMES = "extracting_frames"
    DEDUPLICATING = "deduplicating"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    BUILDING_DOCUMENTS = "building_documents"
    COMPLETED = "completed"
    FAILED = "failed"


class SourceType(str, enum.Enum):
    UPLOAD = "upload"
    YOUTUBE = "youtube"
    GDRIVE = "gdrive"
    ZOHO = "zoho"
    LOCAL_PATH = "local_path"


class MediaType(str, enum.Enum):
    VIDEO = "video"
    AUDIO = "audio"


class Video(Base, IDMixin, TimestampMixin):
    __tablename__ = "videos"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)

    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)  # URL, path, or upload storage key
    media_type: Mapped[str] = mapped_column(String(16), default=MediaType.VIDEO.value, nullable=False)

    status: Mapped[str] = mapped_column(String(32), default=VideoStatus.PENDING.value, nullable=False, index=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    progress_pct: Mapped[int] = mapped_column(default=0, nullable=False)

    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    original_storage_key: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Snapshot of the pipeline settings used for this run (frame interval,
    # dedup thresholds, chunking config, etc.) -- kept so a run is reproducible
    # and so the UI can show "what settings produced this".
    pipeline_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    owner = relationship("User", back_populates="videos")
    segments = relationship("TranscriptSegment", back_populates="video", cascade="all, delete-orphan",
                             order_by="TranscriptSegment.start_seconds")
    frames = relationship("Frame", back_populates="video", cascade="all, delete-orphan",
                           order_by="Frame.timestamp_seconds")
    summary_groups = relationship("SummaryGroup", back_populates="video", cascade="all, delete-orphan",
                                   order_by="SummaryGroup.start_seconds")
    note = relationship("Note", back_populates="video", uselist=False, cascade="all, delete-orphan")
