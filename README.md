# video2doc

Turn a video (YouTube URL, local file, or Google Drive / Zoho link) into a
**Word document and PowerPoint deck** that walk through the video: an AI
summary + key points up front, then a deduplicated frame every few seconds
paired with the transcript spoken around that moment.

## Pipeline

1. **Resolve input** — download from YouTube (`yt-dlp`), Google Drive (`gdown`),
   Zoho WorkDrive (best-effort direct download), or use a local file/folder.
2. **Extract frames** — every N seconds (configurable) *and* on detected scene
   changes (`PySceneDetect`), saved with timestamped filenames.
3. **Deduplicate frames** — drops blurry, near-blank, and near-duplicate frames
   using perceptual hashing (`imagehash`) + Laplacian blur detection. When two
   frames are near-duplicates, the **sharper** one is kept, not just the earlier one.
4. **Transcribe audio** — local **faster-whisper**, the **OpenAI Whisper API**,
   or **your own transcript file** (timestamped or plain paragraphs). Choppy
   fragments are merged into full sentences before anything downstream uses them.
5. **Summarize** — a short summary paragraph + bullet key points, generated via
   OpenAI (if a key is available) or a local TextRank-style extractive summarizer
   (no internet/API needed).
6. **Review & edit** (Streamlit UI only) — deselect frames you don't want, edit
   the paired transcript text, tweak the summary/key points, before export.
7. **Build outputs** — a Word doc and/or a PowerPoint deck, both with the
   summary, key points, and every kept frame paired with its transcript.

## Two ways to run it

- **`app.py`** — a Streamlit UI: upload/link a video, tune settings in the
  sidebar, run, **review and edit frames/text/summary**, then export and
  download the Word doc + PowerPoint deck.
- **`main.py`** — the command-line pipeline, driven entirely by `config/config.yaml`.
  Supports batch-processing a whole folder of videos and resuming interrupted runs.

## Setup

### 1. System dependency: ffmpeg

- **macOS:** `brew install ffmpeg`
- **Ubuntu/Debian:** `sudo apt-get install ffmpeg`
- **Windows:** download from https://ffmpeg.org/download.html and add to PATH

Verify with: `ffmpeg -version`

### 2. Python environment

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **GPU (optional, for faster local transcription):** if you have an NVIDIA GPU
> with CUDA set up, faster-whisper will use it automatically when
> `transcription.local_device: "auto"` (or set to `"cuda"` explicitly). Otherwise
> it runs on CPU — fine for `small`/`medium` models, just slower.

### 3. If using the OpenAI API for transcription

```bash
export OPENAI_API_KEY="sk-..."          # macOS/Linux
setx OPENAI_API_KEY "sk-..."            # Windows
```
And set `transcription.engine: "api"` in the config.

## Usage

### Option A: Streamlit UI (recommended for interactive use)

```bash
streamlit run app.py
```

Three steps:
1. **Setup & Run** — pick your video source, tune settings in the sidebar, click Run.
2. **Review & Edit** — uncheck any frames you don't want, edit the transcript
   text paired with each frame, tweak the summary/key points.
3. **Export** — generate and download the Word doc + PowerPoint deck.

### Option B: Command line, config-driven

1. Edit `config/config.yaml`:
   - Set `input.source_type` (`youtube` | `local` | `local_folder` | `gdrive` | `zoho`) and `input.source`.
   - Adjust frame/dedup/transcription/summarization settings as needed.
2. Run:

```bash
python main.py --config config/config.yaml
```

**Batch mode:** set `input.source_type: "local_folder"` and `input.source` to a
folder path — every video file directly inside it gets processed, each into its
own `output/<video_name>/`.

**Resuming an interrupted run:** `pipeline.resume: true` (the default) makes
re-running skip any stage whose output already exists (extraction, dedup,
transcription). Delete the relevant subfolder/file under `output/<video_name>/`
to force that stage to redo.

3. Find your results in `output/<video_name>/`:
   - `frames_raw/` — every extracted frame, before dedup
   - `frames_deduped/` — the frames actually used in the output documents
   - `transcript/` — `.json`, `.txt`, `.srt` transcript files, plus `_summary.json`
   - `video_report.docx` — Word doc with summary, key points, frames + transcript
   - `video_report.pptx` — matching PowerPoint deck

## Bringing your own transcript

If you already have a transcript (exported from YouTube, Otter.ai, Zoom, etc.),
skip Whisper entirely:

- **Streamlit UI:** choose "I already have a transcript file" under step 4 and
  upload it directly.
- **CLI:** drop the file into `input_transcripts/` (created next to `main.py`),
  then in `config/config.yaml` set:
  ```yaml
  transcription:
    engine: "external"
    external_transcript_dir: "input_transcripts"
    external_transcript_filename: "my_transcript.srt"   # your filename
  ```

Supported formats: `.srt`, `.vtt`, `.json` (a list of `{"start","end","text"}`
objects, or a whisper-style `{"segments": [...]}` object), or `.txt` with lines
like `[00:01:23] some text` (one segment per line; lines without a timestamp are
merged into the previous segment).

**Plain paragraph transcripts (no timestamps at all)** are also supported — e.g.
a meeting-recording export that's just blank-line-separated paragraphs of speech
with no per-line timing. In that case, each paragraph is spread proportionally
(by character length) across the video's actual runtime. This works best when
paired with a **locally uploaded video**, since that's how the pipeline knows the
exact duration to distribute against; frame/text pairing is naturally
approximate in this mode since there's no real timing data to anchor to.

## Handling large video files (up to ~2GB)

The Streamlit uploader defaults to a 200MB limit. This project raises it via
`.streamlit/config.toml` (`maxUploadSize = 2200`, in MB). If you need larger
files still, edit that file and increase the value further. Uploads are streamed
to disk in 16MB chunks rather than fully buffered, to keep memory usage
reasonable for large files.

## Summary & key points

Controlled by `summarization` in the config (or the sidebar in the UI):

- **`method: "auto"`** (default) — uses OpenAI (needs `OPENAI_API_KEY`) for a
  clean abstractive summary if a key is available, otherwise falls back to a
  local **extractive** summarizer (TF-IDF + TextRank via `scikit-learn` +
  `networkx`, no internet or API key required).
- **`method: "local"`** — always use the local extractive summarizer. It picks
  the most "central" existing sentences rather than writing new ones, so
  quality depends on how clean the underlying transcript already is.
- **`method: "openai"`** — always use OpenAI (fails if no key is set).

Both the Word doc and PPTX get a Summary section/slide and a Key Points
section/slide up front, in addition to the per-frame pairing.

## Audio-only input (mp3/wav/etc., no video)

Upload or point at an audio file instead of a video — `.mp3`, `.wav`, `.m4a`,
`.flac`, `.ogg`, `.aac`, `.wma`, `.opus` are all recognized automatically by
extension. Frame extraction and deduplication are skipped entirely (there's no
video to pull frames from); you still get local Whisper / OpenAI API /
bring-your-own transcription, a summary + key points, and a Word doc +
PowerPoint deck — just built around the full transcript instead of
frame-by-frame pairing.

- **Streamlit UI:** choose "Upload audio only (mp3/wav/etc.)" as the input source.
- **CLI:** point `input.source` at an audio file (or a folder containing a mix
  of video and audio files, with `source_type: "local_folder"`) — the type is
  auto-detected per file.

## Tuning tips

- **Too many near-duplicate frames kept?** Lower `dedup.hamming_threshold`
  isn't right — *raise* it (allows more difference to still count as duplicate).
- **Losing frames you wanted?** Lower `dedup.blur_threshold` /
  `dedup.blank_std_threshold` to be less aggressive, or set `dedup.enabled: false`.
- **Scene detection too trigger-happy (lots of extra frames)?** Raise
  `frame_extraction.scene_threshold` (e.g. 35-40).
- **Transcription slow?** Use a smaller `transcription.local_model`
  (e.g. `small` or `base`) or switch to `transcription.engine: "api"`.
- **Zoho link fails to download?** Zoho WorkDrive doesn't offer a clean public
  download API. Download the file manually via browser, then set
  `input.source_type: "local"` and point `input.source` at the downloaded file.

## Project structure

```
video2doc/
├── config/config.yaml         # all settings live here (CLI mode)
├── .streamlit/config.toml     # raises upload limit to ~2GB
├── requirements.txt
├── app.py                     # Streamlit UI (Run -> Review/Edit -> Export)
├── main.py                    # CLI pipeline (single video, batch folder, resume)
├── input_transcripts/         # drop bring-your-own transcript files here (CLI mode)
├── src/
│   ├── downloader.py          # YouTube / local / gdrive / zoho input resolution
│   ├── frame_extractor.py     # ffmpeg + PySceneDetect frame extraction
│   ├── dedup.py                # perceptual-hash + blur/blank filtering, sharpness tie-break
│   ├── transcriber.py         # faster-whisper (local) / OpenAI Whisper (API)
│   ├── transcript_parser.py   # parses external .srt/.vtt/.json/.txt transcripts
│   ├── text_utils.py          # merges choppy segments into sentences
│   ├── summarizer.py          # local TextRank / OpenAI summary + key points
│   ├── docx_builder.py        # assembles the Word document
│   ├── pptx_builder.py        # assembles the PowerPoint deck
│   └── utils.py               # timestamp helpers, logging
└── output/                    # generated per-run (git-ignored)
```
