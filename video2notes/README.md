# video2notes

A standalone, UI-agnostic backend for turning video/audio into structured
notes: transcript, semantically-grouped sections with representative frames
and audio clips, AI summaries with key takeaways, and generated Word/PowerPoint
documents. REST API + async job queue -- built to be driven by a web app,
mobile app, desktop app, or the `video2doc` Streamlit tool in this repo, all
through the same endpoints.

This is the backend counterpart to `video2doc` (the Streamlit tool one level
up in this repo): same pipeline concepts (frame extraction/dedup, semantic
chunking, audio notes, group-level summaries, docx/pptx generation), rewritten
as an async, multi-user, horizontally-scalable service instead of a
single-session desktop tool.

## Architecture

```
                 Web / Mobile / Desktop
                         |
                         v
                    FastAPI  (main.py, api/*)
                         |
             +-----------+-----------+
             |                       |
             v                       v
         PostgreSQL              Object Storage
       (models/*, one row      (S3 / MinIO / local
        per video/frame/           filesystem
        section/note)              fallback)
             |                       |
             +-----------+-----------+
                         v
                Celery job queue (Redis broker)
                         |
        +----------------+----------------+-----------------+
        v                v                v                 v
  workers/          workers/          workers/          workers/
  screenshots.py   transcription.py   audio.py         summarization.py
  (resolve source,  (extract audio,   (semantic         (group sections,
   extract+dedup     transcribe)       chunking,         LLM summaries,
   frames)                             audio-note        build docx/pptx)
                                       clips)
        |                |                |                 |
        +----------------+----------------+-----------------+
                         |
                  pipeline/*  (the actual algorithms --
                  video/audio/transcript/vision/notes)
                         |
                         v
                    llm/*  (pluggable: local TextRank
                    or OpenAI -- same interface)
```

Each Celery task downloads what it needs from object storage, does its work
in a temp directory, uploads results, updates Postgres, and enqueues the next
stage -- so workers can run on different machines and scale independently of
the API.

## Pipeline stages (one video's journey)

1. **`workers/screenshots.py`** -- resolve the source (download from
   YouTube/Drive/Zoho, or pull an uploaded file from storage), extract frames
   (fixed interval + scene-change detection), deduplicate them (perceptual
   hash + blur/blank filtering, keeping the sharper of any near-duplicates).
   Audio-only input skips straight past this stage.
2. **`workers/transcription.py`** -- extract the audio track, transcribe
   (local faster-whisper or OpenAI Whisper API), merge choppy fragments into
   full sentences.
3. **`workers/audio.py`** -- group frames into semantically-coherent
   **sections** (topically similar + time-adjacent, via TF-IDF cosine
   similarity), pick up to 2 representative images per section, cut a short
   audio-note clip per section.
4. **`workers/summarization.py`** -- group several sections together
   (summarizing one tiny section has nothing to compress -- grouping ~4-10
   gives the LLM provider real material), generate a takeaway summary +
   key points per group and one overall summary, build the Word doc and
   PowerPoint deck, upload everything, mark the video `completed`.

## Data model

- **User** -- owns videos, authenticates via JWT.
- **Video** -- one processing job; tracks `status` through the pipeline
  stages, stores a snapshot of the settings used (`pipeline_config`).
- **Frame** -- an extracted, deduplicated frame; optionally linked to a
  Section as one of its representative images.
- **TranscriptSegment** -- one sentence of the transcript, with timing.
- **Section** -- a semantically-grouped chunk of the video (image(s) + audio
  note + transcript); belongs to a SummaryGroup.
- **SummaryGroup** -- several Sections combined for one real summary +
  key-takeaways block.
- **Note** -- the overall video-level summary/key points + links to the
  generated docx/pptx/transcript files.

## Setup

### Option A: Docker Compose (Postgres + Redis + MinIO + API + worker, all at once)

```bash
cp .env.example .env   # defaults already match docker-compose.yml
docker compose up --build
```

API is at `http://localhost:8000` (docs at `/docs`). MinIO console at
`http://localhost:9001` (minioadmin/minioadmin).

### Option B: Local dev, zero external dependencies

By default (no `.env`, or `DATABASE_URL`/`S3_*` left unset), the service runs
against **SQLite** and the **local filesystem** (`./local_storage/`) with
**synchronous, in-process task execution** if you set
`CELERY_TASK_ALWAYS_EAGER=true` -- no Postgres, Redis, or MinIO needed at all.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# ffmpeg must be installed on your system (frame/audio extraction)

export CELERY_TASK_ALWAYS_EAGER=true
uvicorn main:app --reload
```

For real async processing without Docker: run Postgres/Redis/MinIO yourself
(or `docker compose up postgres redis minio`), point `.env` at them, then run
the API and a worker as separate processes:

```bash
uvicorn main:app --reload
celery -A celery_app worker --loglevel=info    # in a second terminal
```

### Database migrations

`init_db()` (called automatically against SQLite on startup) just does
`create_all` -- fine for dev. For Postgres in production, set up Alembic
(`alembic init alembic`, point `sqlalchemy.url` at `DATABASE_URL`, generate a
migration from `models/`) instead of relying on `create_all`.

## API overview

All endpoints are under `/api/v1`. Interactive docs at `/docs` once running.

| Endpoint | Purpose |
|---|---|
| `POST /auth/register`, `/auth/login`, `GET /auth/me` | JWT auth |
| `POST /videos` | Start a job from a YouTube/Drive/Zoho/local-path URL |
| `POST /videos/upload` | Start a job from a direct file upload (video or audio) |
| `GET /videos` | List your videos |
| `GET /videos/{id}` | Status/progress of one video |
| `GET /videos/{id}/sections` | The chunked report structure (groups -> sections -> frames) |
| `GET /videos/{id}/frames/{frame_id}/image` | A frame's image |
| `GET /videos/{id}/download/{docx\|pptx\|transcript.json\|transcript.srt\|transcript.txt}` | Generated artifacts |
| `GET /notes/{video_id}` | Overall summary + key points |
| `PATCH /notes/{video_id}` | Edit summary/key points; optionally regenerate documents |
| `GET /search?q=...` | Search transcript/section/summary text across your videos |

A typical client flow: `POST /videos/upload` -> poll `GET /videos/{id}` until
`status == "completed"` -> `GET /videos/{id}/sections` to render the review UI
-> `PATCH /notes/{id}` to edit -> `GET /videos/{id}/download/docx`.

## Configuration

See `.env.example` for every setting (all have working defaults). Highlights:

- `LLM_PROVIDER`: `local` (free, extractive, no API key) or `openai` (needs
  `OPENAI_API_KEY`, genuinely synthesizes summaries/action items instead of
  just selecting existing sentences).
- `WHISPER_ENGINE` / `WHISPER_LOCAL_MODEL`: local faster-whisper (`tiny`
  through `large-v3`/`large-v3-turbo`/`distil-large-v3`) or the OpenAI API.
- `chunk_similarity_threshold`, `chunk_max_seconds`, `summary_group_size`,
  etc. mirror the same tuning knobs as `video2doc`'s `config.yaml`, with
  per-request overrides available via `VideoCreate`'s fields.

## Testing

```bash
pip install -r requirements.txt   # includes pytest, httpx, moto
pytest tests/ -v
```

`tests/test_pipeline_smoke.py` runs the **entire pipeline for real** --
register a user, upload a real ffmpeg-generated test video, run frame
extraction/dedup/chunking/audio-notes/summarization/document-building
synchronously (`CELERY_TASK_ALWAYS_EAGER`), and assert on the actual
generated documents, search results, and note editing. Only the Whisper model
call itself is mocked (to avoid requiring a model download in CI); everything
else runs against real code. `tests/test_storage.py` exercises the S3-backend
code path against a mocked S3 (moto), separately from the local-filesystem
fallback used by the smoke test.

## Known limitations / next steps

- Search is simple `ILIKE` matching -- fine at small scale, but swap in a
  pgvector similarity query (embed transcript/summary text, store the
  vectors, query by cosine distance) for real semantic search at scale
  without changing the `/search` endpoint's contract.
- No rate limiting / request quotas yet.
- `init_db()`'s `create_all` is dev-only; production needs Alembic
  migrations.
- The `vision.py` module currently only scores/deduplicates frames; it's a
  natural place to add OCR (slide text extraction) or VLM-based captioning
  later without touching the rest of the pipeline.
