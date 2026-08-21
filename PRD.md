# Product Requirements Document: video2doc / video2notes

## 1. Overview

**Problem.** Recorded video and audio — sales calls, property walkthroughs, CRM demos,
training sessions, meetings — contain information that's expensive to re-derive later.
Watching back a 45-minute recording to extract "what was shown, what was said, what were
the takeaways" doesn't scale, and manual note-taking during the call is either
incomplete or a distraction from actually running the call.

**Product.** A pipeline that takes a video or audio recording and automatically produces
a structured Word document and PowerPoint deck containing: the key visual moments
(deduplicated frames or, for property/product walkthroughs, screenshots of what was
shown), a clean timestamped transcript, and AI-written summaries and key takeaways —
both for the recording as a whole and for each topical section of it.

The product exists in two forms covering two different usage patterns:

- **`video2doc`** — a self-serve desktop tool (Streamlit UI + CLI) for one person to
  process their own recordings, review/edit the output, and export.
- **`video2notes`** — a backend service (REST API) for the same pipeline running
  multi-user, asynchronously, at scale, meant to sit behind a proper client application
  (web/mobile/desktop) rather than be used directly.

**Observed primary use case.** Based on this deployment's usage (recording filenames
under processing: property/project walkthroughs — e.g. site tours, CRM product demos,
sales calls, and internal meetings), the dominant use case is a **real-estate /
sales-and-CRM organization converting recorded property tours, CRM demo calls, and sales
conversations into shareable written reports** — so a prospect's or colleague's key
questions, the property/product features shown, and next steps are captured in a
document without anyone re-watching the recording. The product is general-purpose (any
video/audio → notes), but this document treats that workflow as the primary target
scenario for prioritization purposes.

## 2. Goals

1. Turn a raw recording into a **document a human would actually want to read** —
   organized by topic, not just a flat transcript — in one run, with no manual editing
   required to get something usable.
2. Support **both fully local/offline operation** (no API keys, no internet after
   download) **and higher-quality AI-assisted operation** (OpenAI-backed transcription
   and summarization) as a configuration choice, not a hard fork.
3. Let a user **correct the machine's output** before it becomes a permanent document —
   drop a bad frame, fix a misheard transcript line, edit the summary — rather than
   forcing them to accept whatever the pipeline produced.
4. Make the **backend usable by more than one interface** — the same pipeline that
   powers the Streamlit tool should be callable by a web app, mobile app, or another
   internal tool, without re-implementing the pipeline per client.
5. Scale from **"one person, one laptop, one video"** to **"many users, many
   simultaneous recordings, horizontally-scaled workers"** without changing the core
   algorithms.

## 3. Users / personas

- **Individual operator (`video2doc`)** — e.g. a sales rep, CRM admin, or analyst who
  has a recording and wants a report from it today, run on their own machine, no signup,
  no shared infrastructure. Comfortable with a Streamlit UI or, for repeat/batch work, a
  YAML config file.
- **Platform/integration user (`video2notes`)** — a developer building a client
  (internal tool, customer-facing product) that needs "upload a recording, get back a
  document" as an API capability, with multi-user auth, job status polling, and
  per-request tunable settings, without needing to know anything about frame extraction
  or Whisper internally.
- **End reader of the output document** — not a system user at all, but the actual
  audience the docx/pptx is produced for: someone who did **not** attend the original
  call/tour and needs the substance of it — what was shown, what was said, what to do
  next — in a few minutes of reading instead of watching the full recording.

## 4. Functional requirements

### 4.1 Input handling

- Accept video from: **direct upload**, **YouTube URL**, **local file path**, **Google
  Drive share link**, **Zoho WorkDrive share link** (best-effort — no official public
  download API, manual fallback documented).
- Accept **audio-only files** (`.mp3 .wav .m4a .flac .ogg .aac .wma .opus`) as a
  first-class input type, auto-detected by extension, skipping frame-related stages
  entirely and producing a transcript-centric report instead of a frame-paired one.
- Accept a **user-supplied transcript** in place of running Whisper (`.srt`, `.vtt`,
  `.json`, or `.txt`, including plain-paragraph transcripts with no per-line timestamps,
  spread proportionally across known media duration).
- Support **batch processing** of every video/audio file in a folder in one run
  (`video2doc` CLI, `local_folder` source type), each producing its own output.
- Support files up to **~2GB** without buffering the whole upload into memory.

### 4.2 Visual extraction (video input only)

- Extract candidate frames by **fixed time interval**, **detected scene changes**, or
  **both** (hybrid, default) — configurable sensitivity for each.
- **Deduplicate** extracted frames: drop blurry frames, drop near-blank/fade-transition
  frames, and collapse visually near-identical consecutive frames into one — keeping
  the **sharper** of any near-duplicate pair, not simply the first one encountered.
- Every retained frame is timestamp-addressable and pairable with the transcript spoken
  around it.

### 4.3 Transcription

- Transcribe via **local Whisper** (`faster-whisper`, model size selectable
  tiny→large-v3/large-v3-turbo/distil-large-v3, CPU or GPU) or the **OpenAI Whisper
  API**, with a language hint or auto-detect.
- Persist the transcript in **three formats** (JSON, plain-text with timestamps, SRT)
  regardless of source, so it's independently usable outside the generated documents.
- **Merge choppy STT fragments into full sentences** before anything downstream (display,
  summarization) uses the text, without discarding the original fine-grained segments
  from the persisted transcript files.

### 4.4 Semantic organization

- Group consecutive, **topically similar and time-adjacent** frames into a single
  report "section" instead of one entry per raw frame — configurable similarity
  threshold, max section duration, and max frames folded into one section.
- Show at most **two representative images** per section (first + last), regardless of
  how many raw frames were folded in.
- Attach a **short playable audio clip** to each section, cut from the original
  recording's matching time window.
- Provide a **classic per-frame report mode** as a fallback/alternative when semantic
  grouping is turned off.

### 4.5 Summarization

- Produce an **overall summary paragraph + key points list** for the whole recording.
- Produce a **"takeaway" summary + action items** for each group of several consecutive
  sections (not per-section — a lone section rarely has enough material to meaningfully
  compress) — action-item-biased phrasing distinct from the whole-recording summary's
  narrative style.
- Support two summarization engines interchangeably via configuration: a **local,
  no-API-key, no-internet extractive summarizer** (selects existing sentences), and an
  **LLM-backed summarizer** (OpenAI; genuinely synthesizes/paraphrases and can identify
  action items not phrased as standalone sentences in the source).
- **Never fabricate a summary from too little material** — below a minimum-sentence
  threshold, return no summary/key points for that slice rather than restating the
  handful of source sentences as if they were a compression of something larger.
- Gracefully **fall back to the local summarizer** if the AI-backed path fails or no API
  key is configured, rather than failing the whole run.

### 4.6 Review & correction (interactive use)

- Before final export, let the user: **deselect individual frames** they don't want in
  the output, **edit the transcript text** paired with a frame (per-frame mode) or the
  full transcript (audio-only mode), and **edit the overall summary and key points**.
- (Backend) allow **editing the summary/key points after the fact**, with an option to
  regenerate the documents from the edited text.

### 4.7 Output generation

- Generate a **Word document (.docx)** and a **PowerPoint deck (.pptx)**, either or both,
  each containing: title, overall summary, overall key points, then either the
  per-section (image(s) + audio-note link/embed + transcript + optional takeaway
  summary) or per-frame (image + transcript) body.
- Word: audio note is a clickable relative-path hyperlink alongside the document.
  PowerPoint: audio note is embedded as a playable click-to-play icon on the slide.
- Also export the **raw transcript** in JSON/TXT/SRT alongside the generated documents.

### 4.8 Multi-user / service requirements (`video2notes` only)

- **User accounts with JWT authentication** (register, login, "who am I").
- **Per-user video ownership** — a user can only see/act on their own videos; access to
  another user's resource is rejected the same way as a nonexistent one (no existence
  leakage).
- **Asynchronous job processing** — submitting a video returns immediately with a job
  you poll for status/progress; the actual pipeline work happens on a separate worker
  process/machine.
- **Per-request pipeline overrides** — a client can override any of the shared default
  settings (frame interval, dedup sensitivity, transcription engine/model, chunking
  thresholds, LLM provider) per video, without changing server-wide config.
- **Full-text search** across a user's own transcripts, section summaries, and overall
  summaries, returning enough context (video, timestamp window, snippet) to jump back to
  the right moment.
- **Downloadable artifacts** (docx, pptx, transcript in 3 formats) served either as a
  direct stream or a redirect to a presigned object-storage URL.
- Run with **zero external dependencies** for local development (SQLite + local
  filesystem + synchronous task execution) as well as **fully containerized** for
  production-like environments (Postgres + Redis + MinIO/S3 + separately-scalable API
  and worker containers).

## 5. Non-functional requirements

- **Degrade, don't fail, on missing AI configuration.** No stage should hard-crash a run
  solely because an OpenAI API key isn't set — every AI-assisted stage has a documented,
  working local fallback.
- **Resumability (`video2doc` CLI).** An interrupted batch/long run should not force a
  full restart — completed stages (extraction, dedup, transcription) are detected and
  skipped on re-run.
- **Reproducibility (`video2notes`).** The exact settings used to process a given video
  are stored with that video's record, so results can be explained/reproduced later even
  if server defaults have since changed.
- **Horizontal scalability (`video2notes`).** Pipeline stages must not depend on
  in-process state or a shared local filesystem between stages, so additional worker
  capacity can be added per-stage independently (e.g. more transcription workers without
  more summarization workers).
- **Large-file tolerance.** Uploads must be streamed rather than fully buffered in
  memory, on both the desktop tool and the API.
- **Portability of generated documents.** Output docx/pptx must be genuinely
  self-contained deliverables usable outside the tool (e.g. emailed to someone without
  a functioning environment on the other end) — audio notes travel as a relative-path
  sidecar folder for Word, or embedded directly for PowerPoint.

## 6. Out of scope / explicit non-goals (current state)

- Real-time / live-call processing — input is always a completed recording.
- Speaker diarization ("who said what") — transcripts are text + timing only.
- OCR of on-screen text or slide content, or VLM-based image captioning — `pipeline/vision.py`
  currently only scores/deduplicates frames; flagged as a natural future extension, not
  implemented.
- Semantic (embedding-based) search — current search is substring (`ILIKE`) matching;
  a pgvector-based upgrade is a known intended next step, not yet built.
- Fine-grained authorization beyond single-owner-per-video (no sharing, teams, or roles
  yet).
- Rate limiting / usage quotas on the API.
- Automated DB schema migrations in production (`init_db()` is dev-only `create_all`;
  Alembic is the intended production path, not yet wired up).

## 7. Success criteria

- A user can go from "I have a recording" to "I have a document I'd actually send to
  someone" in one run, with default settings, for the common case (a single video or
  audio file, moderate length) without touching a config file or writing code.
- The local (no-API-key) path produces a genuinely usable document end-to-end — visual
  extraction, transcription, and summarization must each have a working offline mode,
  not just a stub.
- Two people using the same recording and the same settings get materially the same
  output (reproducibility), and re-running after an interruption doesn't force
  redoing already-completed work.
- The backend can process more than one user's video concurrently without one video's
  processing time affecting another's, given adequate worker capacity.

## 8. Roadmap / known next steps

(Carried over from the current codebase's documented "known limitations," not new
speculative scope.)

- Swap `video2notes`' ILIKE search for pgvector-based semantic search over embedded
  transcript/summary text, without changing the `/search` contract.
- Add Alembic migrations for Postgres in production, replacing dev-only `create_all`.
- Add rate limiting / request quotas to the API.
- Extend `pipeline/vision.py` with OCR (slide/on-screen text extraction) or VLM-based
  frame captioning, building on its existing role as the frame-scoring stage.
- Tighten CORS (`allow_origins=["*"]`) before any production exposure.
- Consider a first-class, officially-supported Zoho WorkDrive download path (current
  support is best-effort direct HTTP, with manual download as the documented fallback
  for links that require auth).
