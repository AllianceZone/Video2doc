from typing import Optional

from pydantic import BaseModel, ConfigDict


class NoteOut(BaseModel):
    id: str
    video_id: str
    summary: str
    key_points: list[str]
    docx_available: bool
    pptx_available: bool

    model_config = ConfigDict(from_attributes=True)


class NoteUpdate(BaseModel):
    """Lets a client edit the overall summary/key points after generation
    (mirrors the review-and-edit step in the video2doc Streamlit UI) and
    request regeneration of the documents."""
    summary: Optional[str] = None
    key_points: Optional[list[str]] = None
    regenerate_documents: bool = False


class SearchResult(BaseModel):
    video_id: str
    video_title: str
    match_type: str  # "segment" | "section" | "summary_group" | "note"
    start_seconds: float
    end_seconds: float
    snippet: str
