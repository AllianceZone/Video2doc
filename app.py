#!/usr/bin/env python3
"""
Streamlit UI for video2doc.

Run with:
    streamlit run app.py

Workflow: configure in the sidebar -> run extraction/transcription/summary ->
review & edit frames/text/summary -> generate & download Word/PowerPoint.
"""

import os
import shutil
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.utils import setup_logging, slugify, ensure_dir, is_audio_file, seconds_to_hhmmss
from src.downloader import resolve_video
from src.frame_extractor import extract_frames, get_video_duration
from src.dedup import deduplicate_frames
from src.transcriber import transcribe, save_transcript_files
from src.transcript_parser import load_external_transcript
from src.text_utils import merge_into_sentences
from src.summarizer import generate_summary
from src.docx_builder import build_document, build_audio_document, build_chunked_document, compute_frame_text_map
from src.pptx_builder import build_pptx, build_audio_pptx, build_chunked_pptx
from src.semantic_chunker import compute_chunks, representative_images, group_chunks_for_summary
from src.audio_clipper import extract_audio_clip

st.set_page_config(page_title="video2doc", page_icon="🎬", layout="wide")
setup_logging("INFO")

# ============================================================================
# STYLING
# ============================================================================
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
    html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }
    h1, h2, h3, .v2d-header h1 { font-family: 'Manrope', sans-serif; }

    .stApp {
        background: radial-gradient(circle at 15% 0%, #F3F7F5 0%, #FAFBFC 32%, #FAFBFC 100%);
    }
    #MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { padding-top: 2rem; max-width: 1180px; }

    /* ---------- Hero header ---------- */
    .v2d-header {
        position: relative;
        padding: 2.25rem 2.5rem;
        border-radius: 20px;
        background: linear-gradient(135deg, #16233A 0%, #1E4A3F 55%, #2E6B5E 100%);
        color: white;
        margin-bottom: 1.75rem;
        overflow: hidden;
        box-shadow: 0 10px 40px -12px rgba(20, 40, 35, 0.45);
    }
    .v2d-header::after {
        content: "";
        position: absolute; top: -60%; right: -10%;
        width: 420px; height: 420px; border-radius: 50%;
        background: radial-gradient(circle, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0) 70%);
    }
    .v2d-header h1 {
        margin: 0; font-size: 2.1rem; font-weight: 800; letter-spacing: -0.02em;
        display: flex; align-items: center; gap: 0.6rem;
    }
    .v2d-header p {
        margin: 0.5rem 0 0 0; opacity: 0.88; font-size: 1.02rem; font-weight: 400;
        max-width: 640px; line-height: 1.5;
    }
    .v2d-badges { margin-top: 1.1rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .v2d-badge {
        font-size: 0.75rem; font-weight: 600; padding: 0.3rem 0.7rem;
        border-radius: 999px; background: rgba(255,255,255,0.12);
        border: 1px solid rgba(255,255,255,0.18); color: white;
    }

    /* ---------- Step indicator ---------- */
    .v2d-stepper { display: flex; align-items: center; margin-bottom: 1.75rem; }
    .v2d-stepnode {
        display: flex; align-items: center; gap: 0.55rem;
        font-size: 0.88rem; font-weight: 700; color: #9AA5AF;
    }
    .v2d-stepnode .dot {
        width: 28px; height: 28px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        background: #EDF0F2; color: #9AA5AF; font-size: 0.8rem; flex-shrink: 0;
        transition: all 0.2s ease;
    }
    .v2d-stepnode.active .dot { background: #2E6B5E; color: white; box-shadow: 0 0 0 4px #DCEFE9; }
    .v2d-stepnode.active { color: #16233A; }
    .v2d-stepnode.done .dot { background: #1B4D40; color: white; }
    .v2d-stepnode.done { color: #1B4D40; }
    .v2d-stepline { flex: 1; height: 2px; background: #E3E7EA; margin: 0 0.9rem; border-radius: 2px; }
    .v2d-stepline.done { background: #1B4D40; }

    /* ---------- Section headers ---------- */
    .v2d-sectiontitle {
        font-family: 'Manrope', sans-serif; font-size: 1.15rem; font-weight: 700;
        color: #16233A; margin: 0 0 0.9rem 0; display: flex; align-items: center; gap: 0.5rem;
    }

    /* ---------- Cards ---------- */
    .v2d-card {
        background: white; border: 1px solid #E9ECEE; border-radius: 14px;
        padding: 1.4rem 1.6rem; margin-bottom: 1.1rem;
        box-shadow: 0 1px 3px rgba(20,30,40,0.04);
    }
    div[data-testid="stExpander"] {
        border: 1px solid #E9ECEE !important; border-radius: 12px !important;
        background: white; box-shadow: 0 1px 2px rgba(20,30,40,0.03);
    }
    div[data-testid="stExpander"] summary { font-weight: 600; }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
    }

    /* ---------- Buttons ---------- */
    .stButton > button {
        border-radius: 10px !important; font-weight: 600 !important;
        transition: all 0.15s ease !important;
    }
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #2E6B5E 0%, #24564B 100%) !important;
        border: none !important; box-shadow: 0 2px 8px -2px rgba(46,107,94,0.5) !important;
    }
    .stButton > button[kind="primary"]:hover { transform: translateY(-1px); box-shadow: 0 4px 14px -2px rgba(46,107,94,0.6) !important; }
    .stButton > button[kind="secondary"] { border-color: #D8DCE0 !important; }
    .stDownloadButton > button {
        border-radius: 10px !important; font-weight: 600 !important;
    }
    .stDownloadButton > button[kind="primary"] {
        background: linear-gradient(135deg, #2E6B5E 0%, #24564B 100%) !important;
        border: none !important;
    }

    /* ---------- Misc ---------- */
    .v2d-caption { color: #6B7580; font-size: 0.86rem; line-height: 1.5; }
    .v2d-pill {
        display: inline-block; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em;
        padding: 0.15rem 0.55rem; border-radius: 999px; background: #EAF3F0; color: #1B4D40;
        text-transform: uppercase; margin-left: 0.4rem;
    }
    hr { border-color: #E9ECEE !important; }
    div[data-testid="stFileUploaderDropzone"] {
        border-radius: 12px !important; background: #FAFBFC !important;
    }
</style>
""", unsafe_allow_html=True)

if "stage" not in st.session_state:
    st.session_state.stage = "setup"       # setup -> review -> done
if "data" not in st.session_state:
    st.session_state.data = {}

BASE_OUT = ensure_dir(Path("output"))
UPLOADS_DIR = ensure_dir(Path("_ui_uploads"))


def step_indicator():
    steps = [("Setup & Run", "setup"), ("Review & Edit", "review"), ("Export", "done")]
    order = {"setup": 0, "review": 1, "done": 2}
    current = order[st.session_state.stage]

    html = '<div class="v2d-stepper">'
    for i, (label, key) in enumerate(steps):
        idx = order[key]
        cls = "active" if idx == current else ("done" if idx < current else "")
        icon = "✓" if idx < current else str(idx + 1)
        html += f'<div class="v2d-stepnode {cls}"><span class="dot">{icon}</span><span>{label}</span></div>'
        if i < len(steps) - 1:
            line_cls = "done" if idx < current else ""
            html += f'<div class="v2d-stepline {line_cls}"></div>'
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


st.markdown("""
<div class="v2d-header">
  <h1>🎬 video2doc</h1>
  <p>Turn a video or audio file into a polished Word doc and PowerPoint deck — topically
  grouped sections, audio notes, transcript, and AI-written summaries with key takeaways.</p>
  <div class="v2d-badges">
    <span class="v2d-badge">🖼️ Smart frame grouping</span>
    <span class="v2d-badge">🔊 Audio notes</span>
    <span class="v2d-badge">✨ AI summaries</span>
    <span class="v2d-badge">🎧 Video or audio-only</span>
  </div>
</div>
""", unsafe_allow_html=True)

step_indicator()

# ============================================================================
# SIDEBAR — all tunable settings
# ============================================================================
with st.sidebar:
    st.markdown("### ⚙️ Settings")

    with st.expander("🖼️  Frame extraction", expanded=False):
        frame_mode = st.selectbox("Mode", ["hybrid", "fixed", "scene"], index=0,
                                   help="hybrid = fixed interval + scene-change detection")
        interval_seconds = st.number_input("Interval (seconds)", min_value=1, max_value=120, value=5)
        scene_threshold = st.slider("Scene sensitivity", 10.0, 50.0, 27.0,
                                     help="Lower = more scene changes detected")
        image_format = st.selectbox("Image format", ["jpg", "png"], index=0)
        jpg_quality = st.slider("JPEG quality", 50, 100, 95)

    with st.expander("🧹  Deduplication", expanded=False):
        dedup_enabled = st.checkbox("Enable deduplication", value=True)
        hamming_threshold = st.slider("Similarity threshold", 0, 20, 5,
                                       help="Higher = more tolerant before calling it a duplicate")
        blur_check = st.checkbox("Drop blurry frames", value=True)
        blur_threshold = st.slider("Blur threshold", 0.0, 200.0, 60.0)
        blank_check = st.checkbox("Drop blank frames", value=True)
        blank_std_threshold = st.slider("Blank threshold", 0.0, 30.0, 8.0)

    with st.expander("📝  Transcription", expanded=True):
        engine_label = st.radio(
            "Source", ["Local Whisper", "OpenAI Whisper API", "I have a transcript file"],
        )
        local_model = local_device = None
        api_key = None
        uploaded_transcript = None
        local_compute_type = "auto"
        if engine_label == "Local Whisper":
            engine = "local"
            local_model = st.selectbox(
                "Model size",
                ["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo", "distil-large-v3"],
                index=5,
                help="Bigger = more accurate but slower & more RAM/VRAM. large-v3 is the most "
                     "accurate; large-v3-turbo is nearly as good and noticeably faster; "
                     "distil-large-v3 trades a little accuracy for ~6x the speed of large-v3.",
            )
            st.caption("💡 Rough memory needs: tiny/base/small ~1-2GB · medium ~5GB · "
                       "large-*/turbo ~6-10GB (less with int8 below). First use downloads the "
                       "model (up to ~3GB) and caches it.")
            local_device = st.selectbox("Device", ["auto", "cpu", "cuda"], index=0)
            local_compute_type = st.selectbox(
                "Precision", ["auto", "int8", "float16", "float32"], index=0,
                help="int8 = smallest/fastest (slight accuracy loss), good for CPU or limited VRAM")
        elif engine_label == "OpenAI Whisper API":
            engine = "api"
            api_key = st.text_input("OpenAI API key", type="password",
                                     help="Or set OPENAI_API_KEY env var")
        else:
            engine = "external"
            uploaded_transcript = st.file_uploader(
                "Transcript file", type=["srt", "vtt", "json", "txt"],
                help="Timestamped (.srt/.vtt/.json/.txt) or plain paragraphs with no timestamps "
                     "(paragraphs spread evenly across the video's actual duration)."
            )
        language = st.text_input("Language hint", value="en", help="Blank = auto-detect")

    with st.expander("✨  Overall summary & key points", expanded=True):
        summarize_enabled = st.checkbox("Generate summary + key points", value=True)
        summary_method = st.selectbox("Method", ["auto", "local", "openai"], index=0,
                                       help="auto = OpenAI if a key is available, else local")
        max_summary_sentences = st.slider("Summary length (sentences)", 2, 10, 5)
        max_key_points = st.slider("Number of key points", 3, 15, 8)

    with st.expander("📤  Output formats", expanded=False):
        gen_docx = st.checkbox("Generate Word document", value=True)
        gen_pptx = st.checkbox("Generate PowerPoint deck", value=True)
        image_width_inches = st.slider("Word doc image width (in)", 3.0, 8.0, 6.0)

    with st.expander("🧩  Semantic sections & audio notes", expanded=True):
        chunking_enabled = st.checkbox(
            "Group frames into topical sections", value=True,
            help="Instead of one entry per raw frame, groups topically-similar consecutive "
                 "frames into one section, each with a representative image (or two), a short "
                 "audio clip, and its transcript. Turn off for the classic one-entry-per-frame report."
        )
        similarity_threshold = st.slider(
            "Topic similarity threshold", 0.0, 0.6, 0.15,
            help="Lower = merges more aggressively into fewer, larger sections")
        max_chunk_seconds = st.slider("Max section length (seconds)", 10, 180, 60)
        max_frames_per_chunk = st.slider("Max frames folded into one section", 2, 8, 4)
        generate_audio_notes = st.checkbox("Attach audio note clips", value=True)

        st.markdown('<div class="v2d-caption" style="margin-top:0.5rem;"><b>Takeaway summaries</b> '
                    '— combines several sections together before summarizing, so there\'s enough '
                    'transcript to actually distill (not just restate).</div>', unsafe_allow_html=True)
        summary_group_size = st.slider("Sections combined per takeaway summary", 2, 15, 6)
        max_summary_group_seconds = st.slider("...or max seconds per group", 30, 400, 180)
        group_summary_method = st.selectbox(
            "Takeaway summary method", ["local", "auto", "openai"], index=0,
            help="local = fast/free, extractive (picks existing sentences). openai/auto genuinely "
                 "writes action items & takeaways, at the cost of an API call per group.")

# ============================================================================
# STAGE: SETUP & RUN
# ============================================================================
if st.session_state.stage == "setup":
    st.markdown('<div class="v2d-sectiontitle">📁 Input</div>', unsafe_allow_html=True)

    with st.container(border=True):
        source_type_label = st.radio(
            "Where is your video?",
            ["Upload a file", "YouTube URL", "Local file path", "Google Drive link", "Zoho WorkDrive link",
             "Upload audio only (mp3/wav/etc.)"],
            horizontal=True,
            label_visibility="collapsed",
        )

        uploaded_video = None
        video_source_value = ""
        resolved_source_type = "local"

        if source_type_label == "Upload a file":
            uploaded_video = st.file_uploader(
                "Upload a video file", type=["mp4", "mov", "mkv", "avi", "webm"],
                help="Supports files up to ~2GB.")
            resolved_source_type = "local"
        elif source_type_label == "Upload audio only (mp3/wav/etc.)":
            uploaded_video = st.file_uploader(
                "Upload an audio file", type=["mp3", "wav", "m4a", "flac", "ogg", "aac", "wma", "opus"],
                help="No video/frames -- just a transcript + summary document. Supports files up to ~2GB.")
            resolved_source_type = "local"
            st.caption("🎧 Audio-only mode: frame extraction & dedup are skipped. "
                       "You'll get a transcript + summary Word doc and PowerPoint deck.")
        elif source_type_label == "YouTube URL":
            video_source_value = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...")
            resolved_source_type = "youtube"
        elif source_type_label == "Local file path":
            video_source_value = st.text_input("Path to video file on this machine")
            resolved_source_type = "local"
        elif source_type_label == "Google Drive link":
            video_source_value = st.text_input("Google Drive share link")
            resolved_source_type = "gdrive"
        else:
            video_source_value = st.text_input("Zoho WorkDrive share link")
            st.caption("⚠️ Best-effort only. If it fails, download manually and use 'Local file path'.")
            resolved_source_type = "zoho"

        video_quality = st.selectbox("Max download quality (YouTube only)", ["1080p", "720p", "480p", "best"], index=0)

    st.write("")
    run_clicked = st.button("▶  Run extraction, transcription & summary", type="primary", use_container_width=True)

    if run_clicked:
        try:
            with st.status("Running pipeline...", expanded=True) as status:
                status.write("Resolving video source...")
                downloads_dir = ensure_dir(BASE_OUT / "_downloads")

                if resolved_source_type == "local" and uploaded_video is not None:
                    ensure_dir(UPLOADS_DIR)
                    video_path = UPLOADS_DIR / uploaded_video.name
                    uploaded_video.seek(0)
                    with open(video_path, "wb") as f:
                        shutil.copyfileobj(uploaded_video, f, length=16 * 1024 * 1024)
                elif resolved_source_type == "local" and video_source_value:
                    video_path = resolve_video("local", video_source_value, video_quality, downloads_dir)
                elif resolved_source_type in ("youtube", "gdrive", "zoho"):
                    if not video_source_value:
                        st.error("Please provide a URL/link.")
                        st.stop()
                    video_path = resolve_video(resolved_source_type, video_source_value, video_quality, downloads_dir)
                else:
                    st.error("Please provide a video (upload a file or enter a path/URL).")
                    st.stop()

                status.write(f"✅ Video ready: `{video_path.name}`")
                video_title = slugify(video_path.stem)
                project_dir = ensure_dir(BASE_OUT / video_title)
                audio_only = is_audio_file(video_path)

                kept_frames = []
                if audio_only:
                    status.write("🎧 Audio-only input detected — skipping frame extraction & dedup.")
                else:
                    status.write("Extracting frames...")
                    raw_frames_dir = ensure_dir(project_dir / "frames_raw")
                    raw_frames = extract_frames(
                        video_path=video_path, output_dir=raw_frames_dir, mode=frame_mode,
                        interval_seconds=interval_seconds, scene_threshold=scene_threshold,
                        image_format=image_format, jpg_quality=jpg_quality,
                    )
                    status.write(f"✅ Extracted {len(raw_frames)} raw frames")

                    status.write("Deduplicating frames...")
                    dedup_frames_dir = ensure_dir(project_dir / "frames_deduped")
                    kept_frames = deduplicate_frames(
                        frame_paths=raw_frames, dedup_dir=dedup_frames_dir, enabled=dedup_enabled,
                        hamming_threshold=hamming_threshold, blur_check=blur_check,
                        blur_threshold=blur_threshold, blank_check=blank_check,
                        blank_std_threshold=blank_std_threshold,
                    )
                    status.write(f"✅ Kept {len(kept_frames)} frames after dedup")

                status.write("Getting transcript...")
                if engine == "external":
                    if uploaded_transcript is None:
                        st.error("Please upload a transcript file.")
                        st.stop()
                    ext_dir = ensure_dir(project_dir / "external_transcript_upload")
                    ext_path = ext_dir / uploaded_transcript.name
                    with open(ext_path, "wb") as f:
                        f.write(uploaded_transcript.getbuffer())
                    try:
                        duration = get_video_duration(video_path)
                    except Exception:
                        duration = None
                    segments = load_external_transcript(ext_path, video_duration=duration)
                else:
                    if engine == "api" and api_key:
                        os.environ["OPENAI_API_KEY"] = api_key
                    segments = transcribe(
                        video_path=video_path, work_dir=project_dir, engine=engine,
                        local_model=local_model, local_device=local_device,
                        local_compute_type=local_compute_type, api_key_env="OPENAI_API_KEY",
                        api_model="whisper-1", language=language,
                    )
                transcript_dir = ensure_dir(project_dir / "transcript")
                transcript_paths = save_transcript_files(segments, transcript_dir, video_title)
                merged_segments = merge_into_sentences(segments)
                status.write(f"✅ Got {len(segments)} transcript segments ({len(merged_segments)} sentences)")

                summary, key_points = "", []
                if summarize_enabled:
                    status.write("Generating summary & key points...")
                    if engine == "api" and api_key:
                        os.environ["OPENAI_API_KEY"] = api_key
                    result = generate_summary(
                        merged_segments, method=summary_method,
                        max_summary_sentences=max_summary_sentences, max_key_points=max_key_points,
                    )
                    summary, key_points = result["summary"], result["key_points"]
                    status.write("✅ Summary ready")

                status.update(label="Extraction complete — review below", state="complete", expanded=False)

            st.session_state.data = {
                "video_title": video_title,
                "project_dir": project_dir,
                "video_path": video_path,
                "audio_only": audio_only,
                "kept_frames": kept_frames,
                "segments": merged_segments,
                "summary": summary,
                "key_points": key_points,
                "transcript_paths": transcript_paths,
                "frame_text_map": compute_frame_text_map(kept_frames, merged_segments) if not audio_only else {},
                "included_frames": {f.name: True for f in kept_frames},
                "full_transcript_text": "\n".join(
                    f"[{seconds_to_hhmmss(seg['start'])}] {seg['text']}" for seg in merged_segments
                ) if audio_only else "",
            }
            st.session_state.stage = "review"
            st.rerun()

        except Exception as e:
            st.error(f"Pipeline failed: {e}")
            st.exception(e)

# ============================================================================
# STAGE: REVIEW & EDIT
# ============================================================================
elif st.session_state.stage == "review":
    d = st.session_state.data

    col_title, col_action = st.columns([4, 1])
    with col_title:
        st.markdown(f'<div class="v2d-sectiontitle">🔍 Review — {d["video_title"]}</div>', unsafe_allow_html=True)
    with col_action:
        if st.button("↺ Start over", use_container_width=True):
            st.session_state.stage = "setup"
            st.session_state.data = {}
            st.rerun()

    st.markdown('<div class="v2d-card">', unsafe_allow_html=True)
    st.markdown("**Overall summary**")
    st.text_area("Summary", value=d["summary"], height=110,
                 label_visibility="collapsed", key="edit_summary")
    st.markdown("**Overall key points** (one per line)")
    st.text_area(
        "Key points", value="\n".join(d["key_points"]), height=130,
        label_visibility="collapsed", key="edit_key_points",
    )
    st.markdown('</div>', unsafe_allow_html=True)

    if d["audio_only"]:
        st.markdown('<div class="v2d-sectiontitle">📝 Full transcript</div>', unsafe_allow_html=True)
        st.caption("Edit freely — this is exactly what goes into the final documents.")
        st.text_area(
            "Full transcript", value=d["full_transcript_text"], height=400,
            label_visibility="collapsed", key="edit_full_transcript",
        )
    else:
        st.markdown(f'<div class="v2d-sectiontitle">🖼️ Frames <span class="v2d-pill">{len(d["kept_frames"])}</span></div>',
                    unsafe_allow_html=True)
        st.caption("Uncheck any frame you don't want in the final documents, and edit its paired "
                   "transcript text if needed.")
        if chunking_enabled:
            st.info("🧩 Semantic sections mode is on: frames will be grouped into topical sections with "
                    "takeaway summaries at export time. Include/exclude checkboxes below are respected, "
                    "but per-frame text edits are not (each section's transcript is regenerated fresh "
                    "from the source transcript). Turn off grouping in the sidebar to use per-frame "
                    "text edits directly.", icon="🧩")

        for frame_path in d["kept_frames"]:
            with st.container(border=True):
                cols = st.columns([1, 2])
                with cols[0]:
                    st.checkbox(
                        frame_path.stem.replace("frame_", "⏱ "),
                        value=d["included_frames"].get(frame_path.name, True),
                        key=f"include_{frame_path.name}",
                    )
                    st.image(str(frame_path), use_container_width=True)
                with cols[1]:
                    st.text_area(
                        "Transcript for this frame",
                        value=d["frame_text_map"].get(frame_path.name, ""),
                        height=220,
                        key=f"text_{frame_path.name}",
                    )

    st.write("")
    generate_clicked = st.button("▶  Generate Word & PowerPoint documents", type="primary", use_container_width=True)

    if generate_clicked:
        final_summary = st.session_state.get("edit_summary", d["summary"])
        final_key_points = [l.strip() for l in st.session_state.get("edit_key_points", "").splitlines() if l.strip()]

        with st.spinner("Building documents..."):
            outputs = {}
            if d["audio_only"]:
                final_transcript_text = st.session_state.get("edit_full_transcript", d["full_transcript_text"])
                if gen_docx:
                    docx_path = d["project_dir"] / "video_report.docx"
                    build_audio_document(
                        segments=d["segments"], video_title=d["video_title"], output_path=docx_path,
                        summary=final_summary, key_points=final_key_points,
                        transcript_text_override=final_transcript_text,
                    )
                    outputs["docx_path"] = docx_path
                if gen_pptx:
                    pptx_path = d["project_dir"] / "video_report.pptx"
                    build_audio_pptx(
                        segments=d["segments"], video_title=d["video_title"], output_path=pptx_path,
                        summary=final_summary, key_points=final_key_points,
                        transcript_text_override=final_transcript_text,
                    )
                    outputs["pptx_path"] = pptx_path
                included = []
            else:
                included = [f for f in d["kept_frames"] if st.session_state.get(f"include_{f.name}", True)]

                if chunking_enabled and included:
                    chunks = compute_chunks(
                        included, d["segments"], similarity_threshold=similarity_threshold,
                        max_chunk_seconds=max_chunk_seconds, max_frames_per_chunk=max_frames_per_chunk,
                    )
                    audio_notes_dir = ensure_dir(d["project_dir"] / "audio_notes") if generate_audio_notes else None
                    for chunk in chunks:
                        chunk["images"] = representative_images(chunk)
                        chunk["transcript_text"] = " ".join(s["text"].strip() for s in chunk["segments"]).strip()
                        chunk["audio_clip"] = (
                            extract_audio_clip(d["video_path"], chunk["start"], chunk["end"], audio_notes_dir)
                            if audio_notes_dir else None
                        )

                    groups = group_chunks_for_summary(
                        chunks, group_size=summary_group_size, max_group_seconds=max_summary_group_seconds,
                    )
                    for group in groups:
                        cr = generate_summary(
                            group["segments"], method=group_summary_method,
                            max_summary_sentences=3, max_key_points=6, style="action_items",
                        )
                        group["summary"], group["key_points"] = cr["summary"], cr["key_points"]

                    if gen_docx:
                        docx_path = d["project_dir"] / "video_report.docx"
                        build_chunked_document(
                            groups=groups, video_title=d["video_title"], output_path=docx_path,
                            image_width_inches=image_width_inches,
                            overall_summary=final_summary, overall_key_points=final_key_points,
                        )
                        outputs["docx_path"] = docx_path
                    if gen_pptx:
                        pptx_path = d["project_dir"] / "video_report.pptx"
                        build_chunked_pptx(
                            groups=groups, video_title=d["video_title"], output_path=pptx_path,
                            overall_summary=final_summary, overall_key_points=final_key_points,
                        )
                        outputs["pptx_path"] = pptx_path
                else:
                    text_overrides = {f.name: st.session_state.get(f"text_{f.name}", "") for f in d["kept_frames"]}
                    if gen_docx:
                        docx_path = d["project_dir"] / "video_report.docx"
                        build_document(
                            frame_paths=included, segments=d["segments"], video_title=d["video_title"],
                            output_path=docx_path, image_width_inches=image_width_inches,
                            text_overrides=text_overrides, summary=final_summary, key_points=final_key_points,
                        )
                        outputs["docx_path"] = docx_path
                    if gen_pptx:
                        pptx_path = d["project_dir"] / "video_report.pptx"
                        build_pptx(
                            frame_paths=included, segments=d["segments"], video_title=d["video_title"],
                            output_path=pptx_path, summary=final_summary, key_points=final_key_points,
                            text_overrides=text_overrides,
                        )
                        outputs["pptx_path"] = pptx_path

        d["final_summary"] = final_summary
        d["final_key_points"] = final_key_points
        d["included_frame_paths"] = included
        d.update(outputs)
        st.session_state.stage = "done"
        st.rerun()

# ============================================================================
# STAGE: DONE
# ============================================================================
elif st.session_state.stage == "done":
    d = st.session_state.data

    col_title, col_action = st.columns([4, 1])
    with col_title:
        st.markdown(f'<div class="v2d-sectiontitle">✅ Ready — {d["video_title"]}</div>', unsafe_allow_html=True)
    with col_action:
        if st.button("↺ New video", use_container_width=True):
            st.session_state.stage = "setup"
            st.session_state.data = {}
            st.rerun()

    dl_cols = st.columns(2)
    if "docx_path" in d:
        with dl_cols[0]:
            with open(d["docx_path"], "rb") as f:
                st.download_button(
                    "⬇  Download Word document (.docx)", data=f.read(), file_name=d["docx_path"].name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="primary", use_container_width=True,
                )
    if "pptx_path" in d:
        with dl_cols[1]:
            with open(d["pptx_path"], "rb") as f:
                st.download_button(
                    "⬇  Download PowerPoint deck (.pptx)", data=f.read(), file_name=d["pptx_path"].name,
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    type="primary", use_container_width=True,
                )

    tdl_cols = st.columns(3)
    for col, fmt in zip(tdl_cols, ["json", "txt", "srt"]):
        p = d["transcript_paths"][fmt]
        with col:
            st.download_button(f"⬇ Transcript (.{fmt})", data=p.read_bytes(),
                                file_name=p.name, use_container_width=True)

    st.write("")
    if d.get("final_summary"):
        st.markdown('<div class="v2d-card">', unsafe_allow_html=True)
        st.markdown("**Summary**")
        st.write(d["final_summary"])
        if d.get("final_key_points"):
            st.markdown("**Key points**")
            for kp in d["final_key_points"]:
                st.markdown(f"- {kp}")
        st.markdown('</div>', unsafe_allow_html=True)

    if not d.get("audio_only"):
        st.markdown(f'<div class="v2d-sectiontitle">🖼️ Frames used '
                    f'<span class="v2d-pill">{len(d.get("included_frame_paths", []))}</span></div>',
                    unsafe_allow_html=True)
        cols = st.columns(4)
        for i, frame_path in enumerate(d.get("included_frame_paths", [])):
            with cols[i % 4]:
                st.image(str(frame_path), caption=frame_path.stem.replace("frame_", ""), use_container_width=True)
