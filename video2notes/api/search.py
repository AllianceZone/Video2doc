"""
Search across a user's processed videos. Uses simple ILIKE matching (portable
across SQLite dev and Postgres prod) over transcript segments, sections, and
summaries -- returns the top matches with enough context to jump to a
timestamp. For semantic (embedding-based) search at scale, swap this
implementation for a pgvector similarity query without changing the endpoint
contract; the query cost of ILIKE across many videos is the natural point
where that upgrade starts to matter.
"""

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_current_user, get_db
from models.note import Note, SummaryGroup
from models.transcript import TranscriptSegment
from models.user import User
from models.video import Video
from schemas.note import SearchResult

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=List[SearchResult])
def search(q: str = Query(..., min_length=2), limit: int = Query(25, ge=1, le=100),
           db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    pattern = f"%{q}%"
    owned_video_ids = [v.id for v in db.query(Video.id).filter(Video.owner_id == current_user.id).all()]
    if not owned_video_ids:
        return []

    titles = {v.id: v.title for v in db.query(Video).filter(Video.id.in_(owned_video_ids)).all()}
    results: List[SearchResult] = []

    segment_hits = (db.query(TranscriptSegment)
                     .filter(TranscriptSegment.video_id.in_(owned_video_ids), TranscriptSegment.text.ilike(pattern))
                     .limit(limit).all())
    for seg in segment_hits:
        results.append(SearchResult(video_id=seg.video_id, video_title=titles.get(seg.video_id, ""),
                                     match_type="segment", start_seconds=seg.start_seconds,
                                     end_seconds=seg.end_seconds, snippet=seg.text[:280]))

    group_hits = (db.query(SummaryGroup)
                  .filter(SummaryGroup.video_id.in_(owned_video_ids), SummaryGroup.summary.ilike(pattern))
                  .limit(limit).all())
    for g in group_hits:
        results.append(SearchResult(video_id=g.video_id, video_title=titles.get(g.video_id, ""),
                                     match_type="summary_group", start_seconds=g.start_seconds,
                                     end_seconds=g.end_seconds, snippet=g.summary[:280]))

    note_hits = (db.query(Note)
                 .filter(Note.video_id.in_(owned_video_ids), Note.summary.ilike(pattern))
                 .limit(limit).all())
    for n in note_hits:
        results.append(SearchResult(video_id=n.video_id, video_title=titles.get(n.video_id, ""),
                                     match_type="note", start_seconds=0, end_seconds=0,
                                     snippet=n.summary[:280]))

    return results[:limit]
