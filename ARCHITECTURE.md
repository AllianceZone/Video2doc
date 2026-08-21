# Architecture

This repository contains two implementations of the same product idea, at two different
points on the maturity curve. This document describes both, how they relate, and the
pipeline they share conceptually.

1. **`video2doc`** (repo root) — a single-user, single-process Streamlit UI + CLI tool.
   Runs entirely on one machine, writes results to local disk.
2. **`video2notes`** (`video2notes/`) — a multi-user, horizontally-scalable async backend:
   FastAPI + Celery + Postgres/SQLite + S3/MinIO/local filesystem. Built to be driven by
   a web app, mobile app, desktop app, or `video2doc` itself, all through the same REST
   API.

Both convert a video or audio recording into: a deduplicated set of representative
frames, a timestamped transcript, topically-grouped "sections", AI-written (or
extractive) summaries at both the whole-recording and section-group level, and generated
Word/PowerPoint documents.

---

## 1. `video2doc` — desktop tool

### 1.1 Entry points

- **`app.py`** — Streamlit UI. Three-stage session-state machine: `setup` → `review` →
  `done`. All pipeline settings live in the sidebar (frame extraction, dedup,
  transcription, summarization, output format, chunking) and are read directly into
  local variables that get passed as kwargs into `src/*` functions — there is no config
  object shared between the UI and the CLI.
- **`main.py`** — CLI. Reads `config/config.yaml` (via PyYAML) into a plain dict,
  drives the same `src/*` functions, and additionally supports:
  - **Batch mode** (`input.source_type: local_folder`): iterates every video/audio file
    directly inside a folder, each into its own `output/<name>/`.
  - **Resume** (`pipeline.resume: true`, default on): before each stage, checks whether
    that stage's output already exists on disk and skips it if so (raw frames, deduped
    frames, transcript JSON). Deleting the relevant subfolder/file forces a redo.

### 1.2 Pipeline stages (`src/`)

```
Input source
     │
     ▼
downloader.py ─────────► resolve_video()
  youtube (yt-dlp) / gdrive (gdown) / zoho (best-effort HTTP) / local (as-is)
     │
     ▼
  audio-only file? ──yes──► skip straight to transcription
     │no
     ▼
frame_extractor.py ─────► extract_frames()
  fixed interval (ffmpeg -ss) and/or PySceneDetect scene-change timestamps,
  merged + deduped by min-gap, saved as frame_HH-MM-SS.<fmt>
     │
     ▼
dedup.py ────────────────► deduplicate_frames()
  1. drop blurry (Laplacian variance < threshold)
  2. drop near-blank (stddev < threshold)
  3. perceptual-hash (pHash) walk: near-duplicate of last KEPT frame → keep
     whichever is sharper (Laplacian variance), not just the earlier one
     │
     ▼
transcriber.py ──────────► transcribe()  [or transcript_parser.py for bring-your-own]
  local: faster-whisper (CPU/GPU, tiny→large-v3/turbo/distil)
  api:   OpenAI Whisper API
  external: parse .srt/.vtt/.json/.txt (timestamped or plain-paragraph, the
            latter spread proportionally across known video duration)
  → writes .json/.txt/.srt transcript files
     │
     ▼
text_utils.py ───────────► merge_into_sentences()
  groups choppy Whisper fragments into sentence-level segments (regex sentence-end
  detection), WITHOUT touching the raw segments already saved to disk
     │
     ▼
summarizer.py ───────────► generate_summary()
  method "auto"|"local"|"openai"; "auto" tries OpenAI (if OPENAI_API_KEY set) and
  falls back to local on any failure. "local" = TF-IDF + cosine-similarity graph +
  networkx PageRank (TextRank), selecting existing sentences (extractive, no
  invention). Returns EMPTY summary/key_points below MIN_SENTENCES_FOR_SUMMARY (4)
  rather than restating a handful of sentences as a fake "summary".
     │
     ▼
semantic_chunker.py ─────► compute_chunks() + group_chunks_for_summary()
  (video only, if chunking.enabled) — walks kept frames in time order, merges
  consecutive frames into one "chunk" when: time gap ≤ max_chunk_seconds AND
  chunk size < max_frames_per_chunk AND TF-IDF cosine similarity of adjacent
  frames' transcript windows ≥ similarity_threshold. Chunks are then further
  grouped (group_chunks_for_summary) by count/time so summaries are generated
  over enough material to actually compress something — NOT one summary per
  tiny chunk.
     │
     ▼
audio_clipper.py ────────► extract_audio_clip() — ffmpeg cut per chunk, mp3
     │
     ▼
docx_builder.py / pptx_builder.py
  build_document / build_audio_document / build_chunked_document  (docx)
  build_pptx     / build_audio_pptx     / build_chunked_pptx       (pptx)
  → overall Summary + Key Points up front, then either the classic
    one-entry-per-frame report or the chunked "group → section" report
```

### 1.3 Output layout (per run)

```
output/<video_title>/
├── frames_raw/            # every extracted frame, pre-dedup (kept unless output.keep_raw_frames: false)
├── frames_deduped/         # frames actually used downstream
├── audio_notes/            # per-chunk audio clips (chunked mode)
├── transcript/
│   ├── <name>_transcript.json / .txt / .srt
│   └── <name>_summary.json
├── video_report.docx
└── video_report.pptx
```

### 1.4 Streamlit review/edit loop

`app.py`'s `review` stage lets the user, before final export: toggle individual frames
in/out (`included_frames`), edit the transcript text paired with each frame
(per-frame mode only — chunked mode regenerates section text fresh from the source
transcript instead, since per-frame edits don't map onto merged sections), and edit the
overall summary/key points. Only on clicking "Generate" does it call
`docx_builder`/`pptx_builder`/`semantic_chunker` — the pipeline run and the document
build are two separate steps, with the review stage sitting between them.

---

## 2. `video2notes` — backend service

### 2.1 High-level topology

```
                     Web / Mobile / Desktop client
                                │
                                ▼
                    FastAPI  (main.py, api/*)  ── JWT auth (api/auth.py, api/security.py)
                                │
                  ┌─────────────┴─────────────┐
                  ▼                             ▼
             PostgreSQL/SQLite              Object storage
           (models/* — one row per          (S3 / MinIO / local filesystem
            video/frame/section/note)        fallback — storage/s3.py)
                  │                             │
                  └─────────────┬───────────────┘
                                 ▼
                     Celery job queue (Redis broker,
                     or synchronous in-process when
                     CELERY_TASK_ALWAYS_EAGER=true)
                                 │
      ┌──────────────┬───────────────────┬────────────────────┐
      ▼              ▼                   ▼                    ▼
 workers/        workers/            workers/             workers/
 screenshots.py  transcription.py    audio.py             summarization.py
 resolve source, extract audio,      group sections,      group sections for
 extract+dedup   transcribe          cut audio-note        summary, call LLM,
 frames                              clips                 build docx/pptx,
      │              │                   │                 mark COMPLETED
      └──────────────┴───────────────────┴─────────────────────┘
                                 │
                          pipeline/*  (video.py, audio.py, transcript.py,
                          vision.py, notes.py — the actual algorithms,
                          each stateless / pure-function style)
                                 │
                                 ▼
                          llm/*  (pluggable: LocalTextRankProvider or
                          OpenAIProvider, same LLMProvider interface)
```

Each Celery task is self-contained: it opens its own short DB session
(`workers/common.py:db_session`), pulls whatever inputs it needs from object storage into
a `tempfile.TemporaryDirectory` (`workers/common.py:temp_workdir`), does its work, uploads
outputs, updates Postgres, and enqueues the next stage's task by importing and calling
`.delay(video_id)`. No task holds state in process memory across a stage boundary, so
workers can run on different machines and scale independently of the API and of each
other (e.g. run 5 `transcription` workers and 1 `summarization` worker).

### 2.2 Video processing state machine

`models/video.py:VideoStatus`:

```
PENDING → RESOLVING_SOURCE → EXTRACTING_FRAMES → DEDUPLICATING → TRANSCRIBING
        → SUMMARIZING → BUILDING_DOCUMENTS → COMPLETED
                                            ↘ FAILED (from any stage, via mark_failed())
```

Audio-only input skips `EXTRACTING_FRAMES`/`DEDUPLICATING` (set directly to
`TRANSCRIBING` from `workers/screenshots.py`), and `workers/audio.py` skips section
grouping entirely (going straight to `SUMMARIZING`) when chunking is disabled or there
are no frames — falling back to the audio-style transcript report in
`workers/summarization.py`.

### 2.3 Data model (`models/`)

```
User 1───* Video 1───* TranscriptSegment
                 │
                 ├──* Frame ────* (optional) Section
                 │                     │
                 ├──* Section──1 SummaryGroup   (Section.group_id nullable until
                 │                                grouped by workers/summarization.py)
                 ├──* SummaryGroup
                 │
                 └──1 Note   (overall summary/key points + docx/pptx/transcript storage keys)
```

- **`Video`** — one processing job. Tracks `status`/`progress_pct`/`error_message`
  through the pipeline, and stores a full **snapshot** of the settings used
  (`pipeline_config: JSON`) so a run is reproducible and auditable independent of later
  changes to server defaults.
- **`Frame`** — one deduplicated frame; `section_id` + `is_representative` link it to
  the Section it belongs to and whether it's one of that section's up-to-2 shown images.
- **`TranscriptSegment`** — one sentence-level segment (post `merge_into_sentences`),
  `order_index` preserves original order.
- **`Section`** — a semantically-grouped chunk (image(s) + audio note + transcript);
  belongs to a `SummaryGroup` once `workers/summarization.py` runs.
- **`SummaryGroup`** — several Sections combined for one real, non-degenerate takeaway
  summary + key points.
- **`Note`** — the video-level summary/key points plus storage keys for the generated
  docx/pptx/transcript(json/srt/txt) artifacts.

All primary keys are UUID strings (`models/base.py:IDMixin`); every table gets
`created_at`/`updated_at` (`TimestampMixin`).

### 2.4 API surface (`api/`, all under `/api/v1`)

| Router | Endpoints | Notes |
|---|---|---|
| `auth.py` | `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | JWT (`python-jose`), OAuth2-password-flow compatible (works with Swagger's Authorize button) |
| `videos.py` | `POST /videos`, `POST /videos/upload`, `GET /videos`, `GET /videos/{id}`, `GET /videos/{id}/sections`, `GET /videos/{id}/frames/{frame_id}/image`, `GET /videos/{id}/download/{fmt}`, `DELETE /videos/{id}` | `/videos` = URL/path sources (youtube/gdrive/zoho/local_path); `/videos/upload` = direct multipart upload, streamed to storage in 16MB chunks. Both enqueue `workers.screenshots.process_frames` |
| `notes.py` | `GET /notes/{video_id}`, `PATCH /notes/{video_id}` | Edit summary/key points post-hoc; `regenerate_documents=true` re-enqueues `workers.summarization.process_summarization` |
| `search.py` | `GET /search?q=` | `ILIKE` across `TranscriptSegment.text`, `SummaryGroup.summary`, `Note.summary`, scoped to the caller's own videos |

Ownership is enforced per-request (`_get_owned_video` / `_get_owned_note` — 404, not
403, on access to another user's video, to avoid leaking existence). Downloads either
redirect to a presigned S3 URL or stream bytes directly, depending on whether an S3
endpoint is configured (`api/videos.py:_stream_or_redirect`).

### 2.5 Pluggable backends

**Storage** (`storage/s3.py`) — `StorageBackend` ABC with `put_file`/`put_bytes`/
`get_bytes`/`get_file`/`presigned_url`/`delete`/`exists`. Two implementations:
`S3StorageBackend` (boto3, works against real AWS S3 or MinIO via `s3_endpoint_url`) and
`LocalStorageBackend` (plain filesystem under `local_storage_dir`, same key scheme).
`get_storage()` picks S3 if any S3 config is present, else falls back to local
filesystem if `use_local_storage_fallback` (default true) — so the service runs with
zero external dependencies out of the box.

**LLM provider** (`llm/`) — `LLMProvider` ABC with a single `summarize(text, style,
max_summary_sentences, max_key_points)` method. `LocalTextRankProvider` (same TF-IDF +
PageRank extractive approach as `video2doc/src/summarizer.py`'s local path, no API key)
and `OpenAIProvider` (genuine synthesis via chat completion, JSON-mode prompt).
`get_llm_provider()` picks OpenAI only if `llm_provider == "openai"` **and** an API key
is actually set, otherwise logs a warning and falls back to local — mirroring
`video2doc`'s "never hard-fail on missing AI config" behavior.

### 2.6 Deployment (`docker-compose.yml`, `Dockerfile`)

Five services: `postgres` (16-alpine), `redis` (7-alpine, Celery broker+backend),
`minio` (S3-compatible object storage + web console on :9001), `api` (uvicorn,
port 8000), `worker` (`celery -A celery_app worker --concurrency=2`). `api` and `worker`
share the same image/build (`Dockerfile`: `python:3.11-slim` + `ffmpeg`/`libgl1`/
`libglib2.0-0` for opencv) and the same environment variables; only the `command`
differs. Both depend on the three infra services being `service_healthy` before
starting.

For local dev without Docker: `CELERY_TASK_ALWAYS_EAGER=true` runs Celery tasks
synchronously in-process, and unset `DATABASE_URL`/`S3_*` default to SQLite + local
filesystem — the same code path the test suite uses.

### 2.7 Testing

`tests/test_pipeline_smoke.py` is an end-to-end integration test: registers a user,
generates a real short video with `ffmpeg` (`testsrc` + `sine` lavfi sources), uploads
it through the actual API, and runs the **entire pipeline synchronously** (frame
extraction, dedup, chunking, audio-note clipping, local-provider summarization,
docx/pptx building, storage, DB) with only the Whisper model call mocked (patched to
return fixed segments, avoiding a model download in CI). Asserts the video reaches
`COMPLETED` with real generated documents, that `/search` finds transcript content, and
that `PATCH /notes/{id}` editing works. `tests/test_storage.py` separately exercises the
S3 backend against a mocked S3 (`moto`).

---

## 3. Correspondence between the two implementations

`video2notes` reimplements (does not import) the same algorithms as `video2doc`, adapted
to be stateless/service-friendly (functions take/return plain paths and dicts instead of
touching a shared `output/` tree). Keep these in sync when the underlying algorithm
changes:

| Concept | `video2doc` | `video2notes` |
|---|---|---|
| Source resolution | `src/downloader.py` | `pipeline/video.py` (`resolve_source`) |
| Frame extraction | `src/frame_extractor.py` | `pipeline/video.py` (`extract_frames`) |
| Frame dedup | `src/dedup.py` | `pipeline/vision.py` |
| Transcription | `src/transcriber.py` | `pipeline/transcript.py` (`transcribe_local`/`transcribe_api`) |
| External transcript parsing | `src/transcript_parser.py` | `pipeline/transcript.py` (`parse_external_transcript`) |
| Sentence merging | `src/text_utils.py` | `pipeline/transcript.py` (`merge_into_sentences`) |
| Audio clip extraction | `src/audio_clipper.py` | `pipeline/audio.py` |
| Semantic chunking / grouping | `src/semantic_chunker.py` | `pipeline/notes.py` (`compute_sections`/`group_sections_for_summary`) |
| Summarization (extractive) | `src/summarizer.py` (`_summarize_local`) | `llm/local.py` |
| Summarization (OpenAI) | `src/summarizer.py` (`_summarize_openai`) | `llm/openai.py` |
| Document building | `src/docx_builder.py`, `src/pptx_builder.py` | `pipeline/notes.py` (`build_docx`/`build_pptx`/`build_audio_docx`/`build_audio_pptx`) |

Config knobs are named consistently across both (`similarity_threshold`,
`max_chunk_seconds`, `max_frames_per_chunk`, `summary_group_size`,
`max_summary_group_seconds`, `hamming_threshold`, …) — `video2notes/config.py`'s
`Settings` fields are explicitly documented as mirroring `video2doc`'s
`config/config.yaml`, with per-request overrides available via `VideoCreate`.

---

## 4. Known architectural limitations (as of the current codebase)

- **No DB migrations.** `init_db()` is `create_all`-only, invoked automatically against
  SQLite; Postgres in production needs Alembic set up separately (not yet present).
- **Search is `ILIKE`, not semantic.** Fine at small scale; the intended upgrade is a
  pgvector similarity query over embedded transcript/summary text, designed to slot in
  without changing the `/search` endpoint's contract.
- **No rate limiting / request quotas** on the API.
- **`pipeline/vision.py` only scores/deduplicates frames** — no OCR or VLM-based
  captioning yet, though the module is structured to add either without touching the
  rest of the pipeline.
- **Zoho WorkDrive download is best-effort** in both implementations (plain HTTP GET,
  no official public download API) — links requiring login/session cookies require a
  manual download + `local`/`local_path` source type instead.
- **CORS is wide open** (`allow_origins=["*"]`) in `video2notes/main.py`, explicitly
  flagged in-code as needing tightening for production.
