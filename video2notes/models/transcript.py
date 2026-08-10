from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.base import IDMixin, TimestampMixin


class TranscriptSegment(Base, IDMixin, TimestampMixin):
    __tablename__ = "transcript_segments"

    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), nullable=False, index=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    video = relationship("Video", back_populates="segments")


class Frame(Base, IDMixin, TimestampMixin):
    __tablename__ = "frames"

    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), nullable=False, index=True)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sharpness_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_kept: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Which semantic section (Chunk) this frame belongs to, and whether it was
    # chosen as one of that section's representative images.
    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id"), nullable=True, index=True)
    is_representative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    video = relationship("Video", back_populates="frames")
    section = relationship("Section", back_populates="frames")
