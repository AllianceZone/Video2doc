"""
Extracts audio from the video and transcribes it with timestamps, using either:
  - "local": faster-whisper, runs entirely on your machine (CPU or GPU)
  - "api"  : OpenAI's Whisper API (requires an API key + internet)

Output: a list of segment dicts: {"start": float, "end": float, "text": str}
Also writes .json (machine-readable) and .txt / .srt (human-readable) transcript files.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List

from .utils import ensure_dir, seconds_to_hhmmss

logger = logging.getLogger("video2doc.transcriber")


def extract_audio(video_path: Path, audio_out_path: Path) -> Path:
    ensure_dir(audio_out_path.parent)
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr[-500:]}")
    return audio_out_path


def _transcribe_local(audio_path: Path, model_name: str, device: str,
                       compute_type: str, language: str) -> List[Dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ImportError(
            "faster-whisper is required for local transcription. "
            "Install with: pip install faster-whisper"
        ) from e

    logger.info(f"Loading local Whisper model '{model_name}' (device={device}) ...")
    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    logger.info("Transcribing audio locally (this can take a while for long videos) ...")
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language or None,
        vad_filter=True,
    )

    segments = []
    for seg in segments_iter:
        segments.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
    logger.info(f"Local transcription complete: {len(segments)} segments "
                f"(detected language: {info.language})")
    return segments


def _transcribe_api(audio_path: Path, api_key_env: str, api_model: str, language: str) -> List[Dict]:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError(
            "openai package is required for API transcription. Install with: pip install openai"
        ) from e

    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise EnvironmentError(
            f"Environment variable '{api_key_env}' is not set. "
            "Set it to your OpenAI API key before running with transcription.engine: api."
        )

    client = OpenAI(api_key=api_key)
    logger.info(f"Transcribing audio via OpenAI API (model={api_model}) ...")

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model=api_model,
            file=f,
            language=language or None,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    segments = []
    for seg in response.segments:
        # response.segments items may be dicts or objects depending on SDK version
        start = seg["start"] if isinstance(seg, dict) else seg.start
        end = seg["end"] if isinstance(seg, dict) else seg.end
        text = seg["text"] if isinstance(seg, dict) else seg.text
        segments.append({"start": start, "end": end, "text": text.strip()})

    logger.info(f"API transcription complete: {len(segments)} segments")
    return segments


def transcribe(video_path: Path, work_dir: Path, engine: str, local_model: str,
                local_device: str, local_compute_type: str, api_key_env: str,
                api_model: str, language: str) -> List[Dict]:
    audio_path = extract_audio(video_path, work_dir / "audio.wav")

    if engine == "local":
        segments = _transcribe_local(audio_path, local_model, local_device,
                                      local_compute_type, language)
    elif engine == "api":
        segments = _transcribe_api(audio_path, api_key_env, api_model, language)
    else:
        raise ValueError(f"Unknown transcription.engine '{engine}'. Use 'local' or 'api'.")

    return segments


def save_transcript_files(segments: List[Dict], output_dir: Path, base_name: str) -> Dict[str, Path]:
    """Writes .json, .txt, and .srt versions of the transcript. Returns paths."""
    ensure_dir(output_dir)
    paths = {}

    # JSON (machine-readable, full precision)
    json_path = output_dir / f"{base_name}_transcript.json"
    json_path.write_text(json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["json"] = json_path

    # Plain text with [HH:MM:SS] timestamps (human-readable)
    txt_path = output_dir / f"{base_name}_transcript.txt"
    lines = [f"[{seconds_to_hhmmss(seg['start'])}] {seg['text']}" for seg in segments]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    paths["txt"] = txt_path

    # SRT (standard subtitle format, usable in video players/editors)
    srt_path = output_dir / f"{base_name}_transcript.srt"
    srt_lines = []
    for i, seg in enumerate(segments, start=1):
        srt_lines.append(str(i))
        srt_lines.append(f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}")
        srt_lines.append(seg["text"])
        srt_lines.append("")
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    paths["srt"] = srt_path

    logger.info(f"Saved transcript files: {json_path.name}, {txt_path.name}, {srt_path.name}")
    return paths


def _srt_time(seconds: float) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
