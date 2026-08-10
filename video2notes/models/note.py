from sqlalchemy import Float, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.base import IDMixin, TimestampMixin


class Section(Base, IDMixin, TimestampMixin):
    """A semantically-grouped section: a handful of consecutive frames whose
    transcript is topically similar, shown as one report entry (image(s) +
    audio note + transcript) -- see pipeline/notes.py."""
    __tablename__ = "sections"

    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("summary_groups.id"), nullable=True, index=True)

    start_seconds: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    transcript_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    audio_clip_storage_key: Mapped[str] = mapped_column(Text, default="", nullable=False)

    group = relationship("SummaryGroup", back_populates="sections")
    frames = relationship("Frame", back_populates="section")


class SummaryGroup(Base, IDMixin, TimestampMixin):
    """Several Sections combined together to generate one real "takeaway"
    summary from -- summarizing a single tiny section restates it instead of
    compressing anything. See pipeline/notes.py / llm/."""
    __tablename__ = "summary_groups"

    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), nullable=False, index=True)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    key_points: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    video = relationship("Video", back_populates="summary_groups")
    sections = relationship("Section", back_populates="group", order_by="Section.start_seconds")


class Note(Base, IDMixin, TimestampMixin):
    """The overall, video-level note: whole-video summary + key points, and
    the generated document artifacts."""
    __tablename__ = "notes"

    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), unique=True, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    key_points: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    docx_storage_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    pptx_storage_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    transcript_json_storage_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    transcript_srt_storage_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    transcript_txt_storage_key: Mapped[str] = mapped_column(Text, default="", nullable=False)

    video = relationship("Video", back_populates="note")
