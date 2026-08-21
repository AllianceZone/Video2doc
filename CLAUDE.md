# CLAUDE.md

Guidance for Claude (or any AI coding assistant) working in this repository.

## What this repository is

This repo contains **two related but independently-runnable projects** that both do the
same core thing — turn a video or audio recording into a structured Word doc + PowerPoint
deck (frames/sections, transcript, AI summary, key points) — at two different points on
the maturity spectrum:

| | `Video2doc/` (repo root) | `Video2doc/video2notes/` |
|---|---|---|
| What it is | Single-user desktop tool | Multi-user async backend service |
| Interface | Streamlit UI (`app.py`) or CLI (`main.py`) | REST API (FastAPI) |
| State | Files on disk under `output/` | Postgres/SQLite + S3/MinIO/local filesystem |
| Concurrency | One video at a time, synchronous | Celery job queue, horizontally scalable |
| Config | `config/config.yaml` (CLI) or sidebar (UI) | `.env` / `Settings` + per-request overrides |
| Intended use | Personal/manual runs | Powering a web/mobile/desktop client |

**`video2notes` is a from-scratch reimplementation of the same pipeline concepts as a
service**, not a wrapper around `video2doc`. The two do not share code or a Python
package — they share *design*. If you fix a bug or improve an algorithm in one
(`src/dedup.py` vs `video2notes/pipeline/vision.py`, `src/semantic_chunker.py` vs
`video2notes/pipeline/notes.py`, etc.), check whether the same fix applies on the other
side. See the file-correspondence table in `ARCHITECTURE.md`.

Start with `README.md` (repo root) and `video2notes/README.md` — both are thorough and
kept up to date; this file does not repeat their content, only orients you around it.

## Running things

### `video2doc` (root)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# ffmpeg must be on PATH

streamlit run app.py                       # interactive UI
python main.py --config config/config.yaml # CLI, config-driven, supports batch + resume
```

### `video2notes`

```bash
cd video2notes
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export CELERY_TASK_ALWAYS_EAGER=true        # zero-dependency local dev (SQLite + local fs, synchronous tasks)
uvicorn main:app --reload                   # docs at /docs

# OR, for real async processing:
docker compose up --build                   # Postgres + Redis + MinIO + api + worker
```

### Tests

```bash
cd video2notes
pytest tests/ -v
```

`tests/test_pipeline_smoke.py` runs the **entire pipeline for real** (ffmpeg-generated
test video, real frame extraction/dedup/chunking/audio-notes/local-LLM
summarization/docx-pptx building) with only the Whisper model call mocked. Treat this as
the primary regression guard for both `video2notes/pipeline/*` and, by extension, the
mirrored logic in `video2doc/src/*` — there is no equivalent automated test for the root
`video2doc` project, so changes there should be checked manually (`streamlit run app.py`
or a CLI run against a short sample clip) before considering them done.

## Repository conventions to follow

- **Module-per-concern, function-per-stage.** Both `src/` and `video2notes/pipeline/`
  are organized as one file per pipeline stage (extraction, dedup, transcription,
  chunking, summarization, document building) with small, mostly-pure functions taking
  explicit arguments — not classes with hidden state. Follow this shape when adding a
  stage rather than introducing a new pattern.
- **Every module gets a module-level docstring** explaining what it does and, often,
  *why* it's built the way it is (see `src/summarizer.py`, `src/semantic_chunker.py`,
  `video2notes/pipeline/notes.py` for good examples). Preserve this — the docstrings
  carry real design rationale (e.g. why summaries are generated one level up from
  sections, why near-duplicate frames keep the sharper one, why a tiny amount of
  transcript returns an empty summary instead of restating itself).
- **Logging, not print.** Every module gets its own `logging.getLogger("video2doc.<module>")`
  / `logging.getLogger("video2notes.<module>")` logger. Use it for anything a user
  running the CLI/worker would want to see; don't add `print()`.
- **Config flows through explicit parameters, not globals.** `main.py`/`app.py` read
  `config.yaml` / sidebar widgets and pass values as keyword arguments into `src/*`
  functions. `video2notes` does the same via `Settings` (`config.py`) merged with a
  per-`Video` `pipeline_config` JSON snapshot. When adding a new tunable, thread it
  through this way — don't reach for `os.environ` inside a pipeline function.
- **Never hard-fail on missing AI/API config.** `summarization.method: "auto"` and
  `LLM_PROVIDER` both degrade to the local, dependency-light, no-API-key extractive
  summarizer (TF-IDF + TextRank) rather than crashing. Keep this fallback behavior
  intact in anything you touch in `summarizer.py` / `llm/`.
- **Resume / idempotency matters in `main.py`.** `pipeline.resume: true` skips stages
  whose output directory/file already exists. If you change what a stage writes or
  where, make sure the resume-detection logic (`if resume and any(dir.iterdir())`, `if
  resume and raw_json_path.exists()`) still matches reality.
- **Frame filenames encode timestamps** (`frame_HH-MM-SS.jpg`) and are the join key
  between frames and transcript segments throughout the codebase
  (`hhmmss_to_seconds` / `seconds_to_filename_stamp` in `src/utils.py`, duplicated in
  `video2notes/pipeline/video.py` and `pipeline/notes.py`). Don't change this format
  without updating every place that parses it.
- **`video2notes` workers are stateless between stages.** Each Celery task
  (`workers/*.py`) opens its own short DB session (`workers/common.db_session`),
  downloads what it needs from `storage.get_storage()` into a temp dir, does its work,
  uploads results, and enqueues the next task. Don't hold a DB session or local files
  open across a stage boundary, and don't rely on two tasks running on the same
  machine/filesystem.
- **Storage and LLM provider are both behind small abstract interfaces**
  (`video2notes/storage/s3.py:StorageBackend`, `video2notes/llm/base.py:LLMProvider`).
  Add new backends/providers by implementing the interface and wiring them into the
  respective factory function (`get_storage()`, `get_llm_provider()`) — callers should
  never import `boto3` or `openai` directly.

## Known gotchas

- **`bcrypt` is pinned `<4.0`** in `video2notes/requirements.txt` — passlib 1.7.4's
  backend self-test breaks against bcrypt 4.x's changed internals. If you bump passlib,
  re-check this; the comment in `requirements.txt` explains the workaround (or switch
  `api/security.py` to call `bcrypt` directly).
- **`ffmpeg`/`ffprobe` must be on `PATH`** for both projects — there's no bundled
  binary and no pure-Python fallback for frame/audio extraction.
- **`init_db()`'s `create_all` is dev-only.** It's only invoked automatically against
  SQLite in `video2notes/main.py`'s lifespan hook. Production against Postgres needs
  Alembic migrations (not yet set up — see `video2notes/README.md`'s "Known
  limitations").
- **Search in `video2notes/api/search.py` is plain `ILIKE`.** Fine at small scale;
  swapping in pgvector is called out explicitly as the intended upgrade path and should
  not require changing the `/search` endpoint's contract.
- **Large file uploads are streamed, not buffered**, in both projects (`app.py`'s
  Streamlit uploader, `video2notes/api/videos.py`'s `/videos/upload`) — 16MB chunks via
  `shutil.copyfileobj`. Keep this pattern for anything else that accepts a large upload;
  don't read an `UploadFile`/Streamlit file into memory whole.
- **`.streamlit/config.toml` raises the upload cap to ~2.2GB** (`maxUploadSize`). If
  Streamlit uploads start failing for large files, check this file before assuming it's
  a code bug.
- **`_ui_uploads/`, `output/`, `input_transcripts/`, `video2notes/local_storage/` are
  data directories**, not source — don't treat their contents as part of the codebase
  to review or refactor. They're git-ignored.

## When making changes

- If a change affects the pipeline's *behavior* (thresholds, grouping logic, summary
  triggering, document layout), update the corresponding README section — both READMEs
  are written as user-facing documentation of exact current behavior, not aspirational
  docs, and are treated as accurate by users tuning `config.yaml` / request payloads.
- If a change touches the semantic-chunking or summary-grouping logic, update it in
  **both** `src/semantic_chunker.py` + `video2notes/pipeline/notes.py`, and in both
  `src/summarizer.py` + `video2notes/llm/local.py` — they're intentionally kept in
  lockstep (same thresholds, same docstrigs-as-rationale, same `MIN_SENTENCES_FOR_SUMMARY`
  behavior) even though they don't share code.
- After changing anything in `video2notes/pipeline/`, `workers/`, or `llm/`, run
  `pytest video2notes/tests/ -v` — the smoke test exercises the full path end to end.
