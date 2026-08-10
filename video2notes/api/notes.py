from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.deps import get_current_user, get_db
from models.note import Note
from models.user import User
from models.video import Video
from schemas.note import NoteOut, NoteUpdate

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("/{video_id}", response_model=NoteOut)
def get_note(video_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    note = _get_owned_note(db, video_id, current_user)
    return _note_out(note)


@router.patch("/{video_id}", response_model=NoteOut)
def update_note(video_id: str, payload: NoteUpdate, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Edit the overall summary/key points (mirrors the review-and-edit step
    in the video2doc UI). Set regenerate_documents=true to rebuild the
    docx/pptx with the edited text -- this re-enqueues the final build stage."""
    note = _get_owned_note(db, video_id, current_user)
    if payload.summary is not None:
        note.summary = payload.summary
    if payload.key_points is not None:
        note.key_points = payload.key_points
    db.add(note)
    db.commit()
    db.refresh(note)

    if payload.regenerate_documents:
        from workers.summarization import process_summarization
        process_summarization.delay(video_id)

    return _note_out(note)


def _get_owned_note(db: Session, video_id: str, current_user: User) -> Note:
    video = db.get(Video, video_id)
    if not video or video.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")
    if not video.note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not generated yet")
    return video.note


def _note_out(note: Note) -> NoteOut:
    return NoteOut(id=note.id, video_id=note.video_id, summary=note.summary, key_points=note.key_points,
                    docx_available=bool(note.docx_storage_key), pptx_available=bool(note.pptx_storage_key))
