"""
Importing this package registers every model class on the shared declarative
Base (database.Base), so database.init_db() / Alembic autogenerate can see
all of them.
"""

from models.user import User  # noqa: F401
from models.video import Video, VideoStatus, SourceType, MediaType  # noqa: F401
from models.transcript import TranscriptSegment, Frame  # noqa: F401
from models.note import Section, SummaryGroup, Note  # noqa: F401
