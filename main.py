#!/usr/bin/env python3
"""
video2doc: Turn a video (YouTube / local / Drive) into a Word document (and
optionally a PowerPoint deck) of deduplicated frames, timestamped transcript,
an AI summary, and key points.

Usage:
    python main.py [--config config/config.yaml]

Edit config/config.yaml to point at your video and tune every stage.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.utils import setup_logging, slugify, ensure_dir
from src.downloader import resolve_video
from src.frame_extractor import extract_frames, get_video_duration
from src.dedup import deduplicate_frames
from src.transcriber import transcribe, save_transcript_files
from src.transcript_parser import load_external_transcript
from src.text_utils import merge_into_sentences
from src.summarizer import generate_summary
from src.docx_builder import build_document
from src.pptx_builder import build_pptx

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def process_one_video(video_path: Path, cfg: dict, base_out: Path, logger) -> dict:
    """Runs the full pipeline for a single already-resolved video file."""
    resume = cfg.get("pipeline", {}).get("resume", True)

    video_title = slugify(video_path.stem)
    project_dir = ensure_dir(base_out / video_title)
    logger.info(f"Project output directory: {project_dir}")

    # ---- Extract frames ----
    fe_cfg = cfg["frame_extraction"]
    raw_frames_dir = ensure_dir(project_dir / "frames_raw")
    if resume and any(raw_frames_dir.iterdir()):
        logger.info("Resume: raw frames already exist, skipping extraction.")
        raw_frames = sorted(raw_frames_dir.glob(f"*.{fe_cfg['image_format']}"))
    else:
        raw_frames = extract_frames(
            video_path=video_path, output_dir=raw_frames_dir, mode=fe_cfg["mode"],
            interval_seconds=fe_cfg["interval_seconds"], scene_threshold=fe_cfg["scene_threshold"],
            image_format=fe_cfg["image_format"], jpg_quality=fe_cfg["jpg_quality"],
        )

    # ---- Deduplicate ----
    dd_cfg = cfg["dedup"]
    dedup_frames_dir = ensure_dir(project_dir / "frames_deduped")
    if resume and any(dedup_frames_dir.iterdir()):
        logger.info("Resume: deduped frames already exist, skipping dedup.")
        kept_frames = sorted(dedup_frames_dir.glob(f"*.{fe_cfg['image_format']}"))
    else:
        kept_frames = deduplicate_frames(
            frame_paths=raw_frames, dedup_dir=dedup_frames_dir, enabled=dd_cfg["enabled"],
            hamming_threshold=dd_cfg["hamming_threshold"], blur_check=dd_cfg["blur_check"],
            blur_threshold=dd_cfg["blur_threshold"], blank_check=dd_cfg["blank_check"],
            blank_std_threshold=dd_cfg["blank_std_threshold"],
        )

    if not cfg["output"].get("keep_raw_frames", True):
        shutil.rmtree(raw_frames_dir, ignore_errors=True)

    # ---- Transcribe (or load external transcript) ----
    tr_cfg = cfg["transcription"]
    transcript_dir = ensure_dir(project_dir / "transcript")
    raw_json_path = transcript_dir / f"{video_title}_transcript.json"

    if resume and raw_json_path.exists():
        logger.info("Resume: transcript already exists, skipping transcription.")
        segments = json.loads(raw_json_path.read_text(encoding="utf-8"))
    elif tr_cfg["engine"] == "external":
        ext_path = Path(tr_cfg["external_transcript_dir"]) / tr_cfg["external_transcript_filename"]
        logger.info(f"Using external transcript: {ext_path}")
        try:
            duration = get_video_duration(video_path)
        except Exception:
            duration = None
        segments = load_external_transcript(ext_path, video_duration=duration)
        save_transcript_files(segments, transcript_dir, video_title)
    else:
        segments = transcribe(
            video_path=video_path, work_dir=project_dir, engine=tr_cfg["engine"],
            local_model=tr_cfg["local_model"], local_device=tr_cfg["local_device"],
            local_compute_type=tr_cfg["local_compute_type"], api_key_env=tr_cfg["api_key_env"],
            api_model=tr_cfg["api_model"], language=tr_cfg["language"],
        )
        save_transcript_files(segments, transcript_dir, video_title)

    merged_segments = merge_into_sentences(segments)

    # ---- Summarize ----
    sm_cfg = cfg.get("summarization", {"enabled": True})
    summary, key_points = "", []
    if sm_cfg.get("enabled", True):
        logger.info("Generating summary and key points...")
        result = generate_summary(
            merged_segments, method=sm_cfg.get("method", "auto"),
            api_key_env=sm_cfg.get("api_key_env", "OPENAI_API_KEY"),
            openai_model=sm_cfg.get("openai_model", "gpt-4o-mini"),
            max_summary_sentences=sm_cfg.get("max_summary_sentences", 5),
            max_key_points=sm_cfg.get("max_key_points", 8),
        )
        summary, key_points = result["summary"], result["key_points"]
        (transcript_dir / f"{video_title}_summary.json").write_text(
            json.dumps({"summary": summary, "key_points": key_points}, indent=2),
            encoding="utf-8",
        )

    # ---- Build outputs ----
    out_cfg = cfg["output"]
    results = {"project_dir": project_dir, "kept_frames": kept_frames,
               "segments": merged_segments, "summary": summary, "key_points": key_points}

    if out_cfg.get("generate_docx", True):
        docx_path = project_dir / out_cfg["docx_filename"]
        build_document(
            frame_paths=kept_frames, segments=merged_segments, video_title=video_title,
            output_path=docx_path, image_width_inches=out_cfg["image_width_inches"],
        )
        results["docx_path"] = docx_path

    if out_cfg.get("generate_pptx", True):
        pptx_path = project_dir / out_cfg["pptx_filename"]
        build_pptx(
            frame_paths=kept_frames, segments=merged_segments, video_title=video_title,
            output_path=pptx_path, summary=summary, key_points=key_points,
        )
        results["pptx_path"] = pptx_path

    logger.info("=" * 60)
    logger.info(f"DONE: {video_title}")
    logger.info(f"  Frames (deduped): {dedup_frames_dir}  ({len(kept_frames)} frames)")
    logger.info(f"  Transcript:       {transcript_dir}")
    if "docx_path" in results:
        logger.info(f"  Word document:    {results['docx_path']}")
    if "pptx_path" in results:
        logger.info(f"  PowerPoint deck:  {results['pptx_path']}")
    logger.info("=" * 60)
    return results


def main():
    parser = argparse.ArgumentParser(description="Video -> frames + transcript + summary -> Word/PowerPoint")
    parser.add_argument("--config", default="config/config.yaml", help="Path to config YAML")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    logger = setup_logging(cfg.get("logging", {}).get("level", "INFO"))

    in_cfg = cfg["input"]
    base_out = ensure_dir(Path(cfg["output"]["base_dir"]))
    downloads_dir = ensure_dir(base_out / "_downloads")

    if in_cfg["source_type"] == "local_folder":
        folder = Path(in_cfg["source"]).expanduser().resolve()
        videos = sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS)
        if not videos:
            logger.error(f"No video files found in {folder}")
            return
        logger.info(f"Batch mode: found {len(videos)} video(s) in {folder}")
        for i, video_path in enumerate(videos, 1):
            logger.info(f"--- [{i}/{len(videos)}] {video_path.name} ---")
            try:
                process_one_video(video_path, cfg, base_out, logger)
            except Exception as e:
                logger.error(f"Failed on {video_path.name}: {e}")
        return

    video_path = resolve_video(
        source_type=in_cfg["source_type"], source=in_cfg["source"],
        video_quality=in_cfg.get("video_quality", "1080p"), work_dir=downloads_dir,
    )
    process_one_video(video_path, cfg, base_out, logger)


if __name__ == "__main__":
    main()
