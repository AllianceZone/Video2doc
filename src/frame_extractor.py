"""
Extracts high-quality frames from a video at:
  - fixed intervals (e.g. every N seconds), and/or
  - detected scene changes (via PySceneDetect)

Frames are saved as <output_dir>/frame_HH-MM-SS.jpg (timestamp encoded in filename),
so they can be matched back to transcript segments later.
"""

import logging
import subprocess
from pathlib import Path
from typing import List

from .utils import ensure_dir, seconds_to_filename_stamp

logger = logging.getLogger("video2doc.frames")


def get_video_duration(video_path: Path) -> float:
    """Return duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def get_scene_change_timestamps(video_path: Path, threshold: float) -> List[float]:
    """Return a list of timestamps (seconds) where PySceneDetect detects a scene change."""
    try:
        from scenedetect import detect, ContentDetector
    except ImportError as e:
        raise ImportError(
            "scenedetect is required for scene-change detection. "
            "Install with: pip install scenedetect[opencv]"
        ) from e

    logger.info(f"Running scene-change detection (threshold={threshold}) ...")
    scene_list = detect(str(video_path), ContentDetector(threshold=threshold))
    timestamps = [scene[0].get_seconds() for scene in scene_list]
    logger.info(f"Detected {len(timestamps)} scene changes.")
    return timestamps


def get_fixed_interval_timestamps(duration: float, interval_seconds: float) -> List[float]:
    ts = []
    t = 0.0
    # Leave a small safety margin so we never seek past the last decodable frame
    safe_duration = max(0.0, duration - 0.2)
    while t < safe_duration:
        ts.append(t)
        t += interval_seconds
    return ts


def merge_timestamps(*timestamp_lists: List[float], min_gap: float = 1.0) -> List[float]:
    """Merge multiple timestamp lists, dropping near-duplicates within min_gap seconds."""
    all_ts = sorted(t for lst in timestamp_lists for t in lst)
    merged: List[float] = []
    for t in all_ts:
        if not merged or (t - merged[-1]) >= min_gap:
            merged.append(t)
    return merged


def extract_frame_at(video_path: Path, timestamp: float, out_path: Path,
                      image_format: str = "jpg", jpg_quality: int = 95) -> bool:
    """
    Extract a single high-quality frame at `timestamp` seconds using ffmpeg.
    Uses accurate (slow) seeking: -ss placed after -i for frame-accurate results.
    """
    cmd = ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path)]

    if image_format == "jpg":
        # qscale 2 = high quality (ffmpeg jpeg quality scale is 2-31, lower is better)
        qscale = max(2, round((100 - jpg_quality) / 3) + 2)
        cmd += ["-frames:v", "1", "-q:v", str(qscale), str(out_path)]
    else:
        cmd += ["-frames:v", "1", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        # Retry slightly earlier -- common failure mode right at end-of-stream
        retry_ts = max(0.0, timestamp - 0.3)
        if retry_ts != timestamp:
            cmd_retry = ["ffmpeg", "-y", "-ss", str(retry_ts), "-i", str(video_path)]
            if image_format == "jpg":
                qscale = max(2, round((100 - jpg_quality) / 3) + 2)
                cmd_retry += ["-frames:v", "1", "-q:v", str(qscale), str(out_path)]
            else:
                cmd_retry += ["-frames:v", "1", str(out_path)]
            result2 = subprocess.run(cmd_retry, capture_output=True, text=True)
            if result2.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                return True
        logger.warning(f"Failed to extract frame at {timestamp:.2f}s: {result.stderr[-300:]}")
        return False
    return True


def extract_frames(video_path: Path, output_dir: Path, mode: str, interval_seconds: float,
                    scene_threshold: float, image_format: str, jpg_quality: int) -> List[Path]:
    """
    Run the configured extraction mode and save frames to output_dir.
    Returns the sorted list of saved frame paths.
    """
    ensure_dir(output_dir)
    duration = get_video_duration(video_path)
    logger.info(f"Video duration: {duration:.1f}s")

    if mode == "fixed":
        timestamps = get_fixed_interval_timestamps(duration, interval_seconds)
    elif mode == "scene":
        timestamps = get_scene_change_timestamps(video_path, scene_threshold)
    elif mode == "hybrid":
        fixed_ts = get_fixed_interval_timestamps(duration, interval_seconds)
        scene_ts = get_scene_change_timestamps(video_path, scene_threshold)
        timestamps = merge_timestamps(fixed_ts, scene_ts, min_gap=1.0)
    else:
        raise ValueError(f"Unknown frame_extraction.mode '{mode}'. Use fixed, scene, or hybrid.")

    if not timestamps or timestamps[0] > 0.5:
        timestamps = [0.0] + timestamps

    logger.info(f"Extracting {len(timestamps)} frames (mode={mode}) ...")
    saved_paths: List[Path] = []
    for t in timestamps:
        stamp = seconds_to_filename_stamp(t)
        out_path = output_dir / f"frame_{stamp}.{image_format}"
        if extract_frame_at(video_path, t, out_path, image_format, jpg_quality):
            saved_paths.append(out_path)

    saved_paths.sort()
    logger.info(f"Saved {len(saved_paths)} raw frames to {output_dir}")
    return saved_paths
