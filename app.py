#!/usr/bin/env python3
"""
Streamlit UI for video2doc.

Run with:
    streamlit run app.py

Workflow: configure in the sidebar -> run extraction/transcription/summary ->
review & edit frames/text/summary -> generate & download Word/PowerPoint.
"""

import os
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
from src.semantic_chunker import compute_chunks, representative_images
from src.audio_clipper import extract_audio_clip

st.set_page_config(page_title="video2doc", page_icon="🎬", layout="wide")
setup_logging("INFO")

# ============================================================================
# STYLING
# ============================================================================
st.markdown("""
<style>
    .stApp { background-color: #FAFBFC; }
    #MainMenu, footer { visibility: hidden; }

    .v2d-header {
        padding: 1.75rem 2rem;
        border-radius: 14px;
        background: linear-gradient(135deg, #1B2A41 0%, #2E6B5E 100%);
        color: white;
        margin-bottom: 1.5rem;
    }
    .v2d-header h1 { margin: 0; font-size: 1.9rem; }
    .v2d-header p { margin: 0.35rem 0 0 0; opacity: 0.9; font-size: 1.0rem; }

    .v2d-step {
        display: inline-flex; align-items: center; gap: 0.4rem;
        padding: 0.3rem 0.9rem; border-radius: 999px;
        background: #EEF1F0; color: #5A6470; font-size: 0.85rem; font-weight: 600;
        margin-right: 0.5rem;
    }
    .v2d-step.active { background: #2E6B5E; color: white; }
    .v2d-step.done { background: #DCEFE9; color: #1B4D40; }

    div[data-testid="stExpander"] {
        border: 1px solid #E6E9EC; border-radius: 10px; background: white;
    }
    .stButton > button[kind="primary"] {
        background: #2E6B5E; border: none;
    }
    .stButton > button[kind="primary"]:hover { background: #24564B; }

    .v2d-card {
        background: white; border: 1px solid #E6E9EC; border-radius: 12px;
        padding: 1.25rem 1.5rem; margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)

if "stage" not in st.session_state:
    st.session_state.stage = "setup"       # setup -> review -> done
if "data" not in st.session_state:
    st.session_state.data = {}

BASE_OUT = ensure_dir(Path("output"))
UPLOADS_DIR = ensure_dir(Path("_ui_uploads"))


def step_badge():
    steps = [("1", "Setup & Run", "setup"), ("2", "Review & Edit", "review"), ("3", "Export", "done")]
    order = {"setup": 0, "review": 1, "done": 2}
    current = order[st.session_state.stage]
    html = ""
    for i, (num, label, key) in enumerate(steps):
        cls = "active" if order[key] == current else ("done" if order[key] < current else "")
        html += f'<span class="v2d-step {cls}">{num} · {label}</span>'
    st.markdown(html, unsafe_allow_html=True)


st.markdown("""
<div class="v2d-header">
  <h1>🎬 video2doc</h1>
  <p>Video → deduplicated frames, transcript, AI summary & key points → Word doc + PowerPoint deck</p>
</div>
""", unsafe_allow_html=True)

step_badge()
st.write("")

# ============================================================================
# SIDEBAR — all tunable settings
# ============================================================================
with st.sidebar:
    st.header("⚙️ Settings")

    with st.expander("🖼️ Frame extraction", expanded=False):
        frame_mode = st.selectbox("Mode", ["hybrid", "fixed", "scene"], index=0,
                                   help="hybrid = fixed interval + scene-change detection")
        interval_seconds = st.number_input("Interval (seconds)", min_value=1, max_value=120, value=5)
        scene_threshold = st.slider("Scene sensitivity", 10.0, 50.0, 27.0,
                                     help="Lower = more scene changes detected")
        image_format = st.selectbox("Image format", ["jpg", "png"], index=0)
        jpg_quality = st.slider("JPEG quality", 50, 100, 95)

    with st.expander("🧹 Deduplication", expanded=False):
        dedup_enabled = st.checkbox("Enable deduplication", value=True)
        hamming_threshold = st.slider("Similarity threshold", 0, 20, 5,
                                       help="Higher = more tolerant before calling it a duplicate")
        blur_check = st.checkbox("Drop blurry frames", value=True)
        blur_threshold = st.slider("Blur threshold", 0.0, 200.0, 60.0)
        blank_check = st.checkbox("Drop blank frames", value=True)
        blank_std_threshold = st.slider("Blank threshold", 0.0, 30.0, 8.0)

    with st.expander("📝 Transcription", expanded=True):
        engine_label = st.radio(
            "Source", ["Local Whisper", "OpenAI Whisper API", "I have a transcript file"],
        )
        local_model = local_device = None
        api_key = None
        uploaded_transcript = None
        if engine_label == "Local Whisper":
            engine = "local"
            local_model = st.selectbox("Model size", ["tiny", "base", "small", "medium", "large-v3"], index=3)
            local_device = st.selectbox("Device", ["auto", "cpu", "cuda"], index=0)
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

    with st.expander("✨ Summary & key points", expanded=True):
        summarize_enabled = st.checkbox("Generate summary + key points", value=True)
        summary_method = st.selectbox("Method", ["auto", "local", "openai"], index=0,
                                       help="auto = OpenAI if a key is available, else local")
        max_summary_sentences = st.slider("Summary length (sentences)", 2, 10, 5)
        max_key_points = st.slider("Number of key points", 3, 15, 8)

    with st.expander("📤 Output", expanded=False):
        gen_docx = st.checkbox("Generate Word document", value=True)
        gen_pptx = st.checkbox("Generate PowerPoint deck", value=True)
        image_width_inches = st.slider("Word doc image width (in)", 3.0, 8.0, 6.0)

    with st.expander("🧩 Semantic sections & audio notes", expanded=True):
        chunking_enabled = st.checkbox(
            "Group frames into sections (image + audio note + transcript + mini-summary)",
            value=True,
            help="Instead of one entry per raw frame, groups topically-similar consecutive "
                 "frames into one section, each with a representative image (or two), a short "
                 "audio clip, its transcript, and its own mini-summary + key point(s). "
                 "Turn off to get the classic one-entry-per-frame report."
        )
        similarity_threshold = st.slider(
            "Topic similarity threshold", 0.0, 0.6, 0.15,
            help="Lower = merges more aggressively into fewer, larger sections")
        max_chunk_seconds = st.slider("Max section length (seconds)", 10, 180, 60)
        max_frames_per_chunk = st.slider("Max frames folded into one section", 2, 8, 4)
        generate_audio_notes = st.checkbox("Attach audio note clips", value=True)
        chunk_summary_method = st.selectbox(
            "Per-section summary method", ["local", "auto", "openai"], index=0,
            help="local = fast/free, no API calls -- recommended since there can be many sections")

# ============================================================================
# STAGE: SETUP & RUN
# ============================================================================
if st.session_state.stage == "setup":
    st.subheader("1. Input video")

    source_type_label = st.radio(
        "Where is your video?",
        ["Upload a file", "YouTube URL", "Local file path", "Google Drive link", "Zoho WorkDrive link",
         "Upload audio only (mp3/wav/etc.)"],
        horizontal=True,
    )

    uploaded_video = None
    video_source_value = ""
    resolved_source_type = "local"
    is_audio_upload = False

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
        is_audio_upload = True
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
    run_clicked = st.button("▶ Run extraction, transcription & summary", type="primary", use_container_width=True)

    if run_clicked:
        try:
            with st.status("Running pipeline...", expanded=True) as status:
                status.write("Resolving video source...")
                downloads_dir = ensure_dir(BASE_OUT / "_downloads")

                if resolved_source_type == "local" and uploaded_video is not None:
                    import shutil
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
                        local_compute_type="auto", api_key_env="OPENAI_API_KEY",
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
    st.subheader(f"2. Review & edit — {d['video_title']}")

    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("↺ Start over"):
            st.session_state.stage = "setup"
            st.session_state.data = {}
            st.rerun()

    st.markdown('<div class="v2d-card">', unsafe_allow_html=True)
    st.markdown("**Summary**")
    edited_summary = st.text_area("Summary", value=d["summary"], height=120,
                                   label_visibility="collapsed", key="edit_summary")
    st.markdown("**Key points** (one per line)")
    edited_key_points_raw = st.text_area(
        "Key points", value="\n".join(d["key_points"]), height=150,
        label_visibility="collapsed", key="edit_key_points",
    )
    st.markdown('</div>', unsafe_allow_html=True)

    if d["audio_only"]:
        st.markdown("**Full transcript** (edit freely — this is what goes into the final documents)")
        st.text_area(
            "Full transcript", value=d["full_transcript_text"], height=400,
            label_visibility="collapsed", key="edit_full_transcript",
        )
    else:
        st.markdown(f"**Frames ({len(d['kept_frames'])})** — uncheck any you don't want in the final documents, "
                    f"and edit the paired transcript text if needed.")
        if chunking_enabled:
            st.caption("🧩 Semantic sections mode is on: frames will be grouped into topical sections at export "
                       "time. Include/exclude checkboxes below are respected, but per-frame text edits are not "
                       "(each section's transcript is regenerated fresh from the source transcript). "
                       "Turn off grouping in the sidebar to use per-frame text edits directly.")

        for frame_path in d["kept_frames"]:
            with st.container(border=True):
                cols = st.columns([1, 2])
                with cols[0]:
                    include = st.checkbox(
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
    generate_clicked = st.button("▶ Generate Word & PowerPoint documents", type="primary", use_container_width=True)

    if generate_clicked:
        # Pull edited values back out of widget state
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
                    st.write("Grouping frames into semantic sections...")
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
                        if chunk["segments"]:
                            cr = generate_summary(
                                chunk["segments"], method=chunk_summary_method,
                                max_summary_sentences=2, max_key_points=3,
                            )
                            chunk["summary"], chunk["key_points"] = cr["summary"], cr["key_points"]
                        else:
                            chunk["summary"], chunk["key_points"] = "", []

                    if gen_docx:
                        docx_path = d["project_dir"] / "video_report.docx"
                        build_chunked_document(
                            chunks=chunks, video_title=d["video_title"], output_path=docx_path,
                            image_width_inches=image_width_inches,
                            overall_summary=final_summary, overall_key_points=final_key_points,
                        )
                        outputs["docx_path"] = docx_path
                    if gen_pptx:
                        pptx_path = d["project_dir"] / "video_report.pptx"
                        build_chunked_pptx(
                            chunks=chunks, video_title=d["video_title"], output_path=pptx_path,
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
    st.subheader(f"3. Export — {d['video_title']}")

    if st.button("↺ Start a new video"):
        st.session_state.stage = "setup"
        st.session_state.data = {}
        st.rerun()

    st.write("")
    dl_cols = st.columns(2)
    if "docx_path" in d:
        with dl_cols[0]:
            with open(d["docx_path"], "rb") as f:
                st.download_button(
                    "⬇ Download Word document (.docx)", data=f.read(), file_name=d["docx_path"].name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="primary", use_container_width=True,
                )
    if "pptx_path" in d:
        with dl_cols[1]:
            with open(d["pptx_path"], "rb") as f:
                st.download_button(
                    "⬇ Download PowerPoint deck (.pptx)", data=f.read(), file_name=d["pptx_path"].name,
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
        st.subheader(f"Frames used ({len(d.get('included_frame_paths', []))})")
        cols = st.columns(4)
        for i, frame_path in enumerate(d.get("included_frame_paths", [])):
            with cols[i % 4]:
                st.image(str(frame_path), caption=frame_path.stem.replace("frame_", ""), use_container_width=True)
