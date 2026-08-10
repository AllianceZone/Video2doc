"""
Video-specific pipeline stages: resolving the input source to a local file
(download if needed), and extracting candidate frames (fixed interval +
scene-change detection).
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger("video2notes.pipeline.video")


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------

def resolve_source(source_type: str, source_ref: str, work_dir: Path,
                    video_quality: str = "1080p") -> Path:
    """
    Resolves a Video's (source_type, source_ref) to a local file path.
      - "upload": source_ref is already a local path (the worker downloaded
        it from storage before calling this)
      - "youtube": downloads via yt-dlp
      - "gdrive": downloads via gdown
      - "zoho": best-effort direct HTTP download
      - "local_path": used as-is (server-local file path)
    """
    source_type = source_type.lower().strip()
    if source_type in ("upload", "local_path"):
        path = Path(source_ref).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {path}")
        return path
    elif source_type == "youtube":
        return _download_youtube(source_ref, video_quality, work_dir)
    elif source_type == "gdrive":
        return _download_gdrive(source_ref, work_dir)
    elif source_type == "zoho":
        return _download_zoho(source_ref, work_dir)
    else:
        raise ValueError(f"Unknown source_type '{source_type}'")


def _download_youtube(url: str, video_quality: str, work_dir: Path) -> Path:
    import yt_dlp

    work_dir.mkdir(parents=True, exist_ok=True)
    height = "".join(ch for ch in video_quality if ch.isdigit()) or "1080"
    fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
    ydl_opts = {
        "format": fmt,
        "outtmpl": str(work_dir / "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = Path(ydl.prepare_filename(info))
        if not filepath.exists():
            filepath = filepath.with_suffix(".mp4")
        if not filepath.exists():
            candidates = sorted(work_dir.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
            if not candidates:
                raise FileNotFoundError("yt-dlp reported success but no output file was found.")
            filepath = candidates[0]
    return filepath


def _download_gdrive(url: str, work_dir: Path) -> Path:
    import gdown
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = gdown.download(url=url, output=str(work_dir) + "/", fuzzy=True, quiet=False)
    if not output_path:
        raise RuntimeError("gdown failed to download the file. Check sharing permissions.")
    return Path(output_path)


def _download_zoho(url: str, work_dir: Path) -> Path:
    import requests
    work_dir.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        filename = "zoho_download.mp4"
        cd = r.headers.get("content-disposition", "")
        if "filename=" in cd:
            filename = cd.split("filename=")[-1].strip('"; ')
        out_path = work_dir / filename
        with open(out_path, "wb") as f:
            shutil.copyfileobj(r.raw, f)
    return out_path


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

def get_media_duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def _scene_change_timestamps(video_path: Path, threshold: float) -> List[float]:
    from scenedetect import detect, ContentDetector
    scene_list = detect(str(video_path), ContentDetector(threshold=threshold))
    return [scene[0].get_seconds() for scene in scene_list]


def _fixed_interval_timestamps(duration: float, interval_seconds: float) -> List[float]:
    ts, t = [], 0.0
    safe_duration = max(0.0, duration - 0.2)
    while t < safe_duration:
        ts.append(t)
        t += interval_seconds
    return ts


def _merge_timestamps(*lists: List[float], min_gap: float = 1.0) -> List[float]:
    all_ts = sorted(t for lst in lists for t in lst)
    merged: List[float] = []
    for t in all_ts:
        if not merged or (t - merged[-1]) >= min_gap:
            merged.append(t)
    return merged


def extract_frame_at(video_path: Path, timestamp: float, out_path: Path, jpg_quality: int = 95) -> bool:
    qscale = max(2, round((100 - jpg_quality) / 3) + 2)
    cmd = ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path),
           "-frames:v", "1", "-q:v", str(qscale), str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        retry_ts = max(0.0, timestamp - 0.3)
        if retry_ts != timestamp:
            cmd2 = ["ffmpeg", "-y", "-ss", str(retry_ts), "-i", str(video_path),
                    "-frames:v", "1", "-q:v", str(qscale), str(out_path)]
            result2 = subprocess.run(cmd2, capture_output=True, text=True)
            if result2.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                return True
        logger.warning(f"Failed to extract frame at {timestamp:.2f}s: {result.stderr[-300:]}")
        return False
    return True


def extract_frames(video_path: Path, output_dir: Path, mode: str = "hybrid",
                    interval_seconds: float = 5, scene_threshold: float = 27.0,
                    jpg_quality: int = 95) -> List[Path]:
    """Extracts candidate frames (before dedup) into output_dir. Returns sorted paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = get_media_duration(video_path)

    if mode == "fixed":
        timestamps = _fixed_interval_timestamps(duration, interval_seconds)
    elif mode == "scene":
        timestamps = _scene_change_timestamps(video_path, scene_threshold)
    elif mode == "hybrid":
        fixed_ts = _fixed_interval_timestamps(duration, interval_seconds)
        scene_ts = _scene_change_timestamps(video_path, scene_threshold)
        timestamps = _merge_timestamps(fixed_ts, scene_ts, min_gap=1.0)
    else:
        raise ValueError(f"Unknown frame extraction mode '{mode}'")

    if not timestamps or timestamps[0] > 0.5:
        timestamps = [0.0] + timestamps

    saved: List[Path] = []
    for t in timestamps:
        stamp = f"{int(t // 3600):02d}-{int((t % 3600) // 60):02d}-{int(t % 60):02d}"
        out_path = output_dir / f"frame_{stamp}.jpg"
        if extract_frame_at(video_path, t, out_path, jpg_quality):
            saved.append(out_path)

    saved.sort()
    logger.info(f"Extracted {len(saved)} frames (mode={mode}) from {video_path.name}")
    return saved
