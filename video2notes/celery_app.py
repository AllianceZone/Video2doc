"""
Celery application. Workers (workers/*.py) register tasks against this
instance; the API (api/videos.py) dispatches to them.

Run a worker with:
    celery -A celery_app worker --loglevel=info

CELERY_TASK_ALWAYS_EAGER=true runs tasks synchronously in-process instead --
useful for local dev without Redis, and for tests.
"""

from celery import Celery

from config import get_settings

settings = get_settings()

celery_app = Celery(
    "video2notes",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=settings.celery_task_always_eager,
    # Pipeline stages can run long (transcription, frame extraction on long
    # videos) -- don't let the broker consider a worker dead mid-job.
    broker_transport_options={"visibility_timeout": 3600 * 6},
)

# Import task modules so Celery registers them on startup.
celery_app.autodiscover_tasks(["workers"])
