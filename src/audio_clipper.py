"""
Extracts short audio clips ("voice notes") for a given time window of the
source media, using ffmpeg. Used to attach a playable audio note to each
semantic chunk in the chunked report.
"""

import logging
import subprocess
from pathlib import Path

from .utils import ensure_dir, seconds_to_filename_stamp

logger = logging.getLogger("video2doc.audio_clipper")


def extract_audio_clip(source_path: Path, start: float, end: float, output_dir: Path,
                        fmt: str = "mp3") -> Path:
    """
    Cuts [start, end) seconds of audio from source_path into a standalone clip.
    Returns the output path. ffmpeg clips silently to the available duration if
    `end` runs past the end of the source, so no need to pre-clamp it.
    """
    ensure_dir(output_dir)
    stamp = seconds_to_filename_stamp(start)
    out_path = output_dir / f"clip_{stamp}.{fmt}"

    duration = max(0.1, end - start)
    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", str(source_path), "-t", str(duration),
        "-vn", "-acodec", "libmp3lame" if fmt == "mp3" else "pcm_s16le", "-q:a", "4",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        logger.warning(f"Failed to extract audio clip [{start:.1f}-{end:.1f}]s: {result.stderr[-300:]}")
        return None
    return out_path
