"""
video2notes API entrypoint.

Run for local dev with:
    uvicorn main:app --reload

Docs at /docs (Swagger) once running.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import auth, notes, search, videos
from config import get_settings
from database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fine for dev/SQLite; production against Postgres should use Alembic
    # migrations instead of create_all (see alembic/ once you set it up).
    if settings.database_url.startswith("sqlite"):
        init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    description="Video/audio -> transcript, semantically-grouped sections, audio notes, "
                 "AI summaries -> Word & PowerPoint. Backend API, UI-agnostic.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this for production deployments
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(videos.router, prefix=settings.api_v1_prefix)
app.include_router(notes.router, prefix=settings.api_v1_prefix)
app.include_router(search.router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}
