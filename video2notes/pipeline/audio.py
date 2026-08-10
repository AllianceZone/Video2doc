"""
Audio-specific pipeline stages: extracting the full audio track (for
transcription) and cutting short clips for a given time window ("audio
notes" attached to each section in the notes pipeline).
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("video2notes.pipeline.audio")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".opus"}


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def extract_audio_track(source_path: Path, output_path: Path) -> Path:
    """Extracts a 16kHz mono WAV track -- the format Whisper wants. Works on
    audio-only sources too (ffmpeg just ignores -vn if there's no video)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(source_path), "-vn", "-acodec", "pcm_s16le",
           "-ar", "16000", "-ac", "1", str(output_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr[-500:]}")
    return output_path


def extract_audio_clip(source_path: Path, start: float, end: float, output_dir: Path) -> Optional[Path]:
    """Cuts a short standalone audio clip for [start, end) seconds -- used as
    the per-section 'audio note'. Clips silently to available duration if
    `end` runs past the end of the source."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(start // 3600):02d}-{int((start % 3600) // 60):02d}-{int(start % 60):02d}"
    out_path = output_dir / f"clip_{stamp}.mp3"
    duration = max(0.1, end - start)
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(source_path), "-t", str(duration),
           "-vn", "-acodec", "libmp3lame", "-q:a", "4", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        logger.warning(f"Failed to extract audio clip [{start:.1f}-{end:.1f}]s: {result.stderr[-300:]}")
        return None
    return out_path
