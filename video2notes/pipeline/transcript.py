"""
Transcript-specific pipeline stages: speech-to-text (local faster-whisper or
OpenAI API), parsing an externally-supplied transcript file, and merging
choppy segments into full sentences before anything downstream uses them.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("video2notes.pipeline.transcript")


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_local(audio_path: Path, model_name: str = "medium", device: str = "auto",
                      compute_type: str = "auto", language: Optional[str] = None) -> List[Dict]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments_iter, info = model.transcribe(str(audio_path), language=language or None, vad_filter=True)
    segments = [{"start": seg.start, "end": seg.end, "text": seg.text.strip()} for seg in segments_iter]
    logger.info(f"Local transcription: {len(segments)} segments (language: {info.language})")
    return segments


def transcribe_api(audio_path: Path, api_key: str, model: str = "whisper-1",
                    language: Optional[str] = None) -> List[Dict]:
    from openai import OpenAI

    if not api_key:
        raise EnvironmentError("OpenAI API key is required for API transcription.")
    client = OpenAI(api_key=api_key)
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model=model, file=f, language=language or None,
            response_format="verbose_json", timestamp_granularities=["segment"],
        )
    segments = []
    for seg in response.segments:
        start = seg["start"] if isinstance(seg, dict) else seg.start
        end = seg["end"] if isinstance(seg, dict) else seg.end
        text = seg["text"] if isinstance(seg, dict) else seg.text
        segments.append({"start": start, "end": end, "text": text.strip()})
    return segments


# ---------------------------------------------------------------------------
# External transcript parsing (bring-your-own-transcript)
# ---------------------------------------------------------------------------

_TXT_LINE_RE = re.compile(r"^\[?(\d+):(\d+):(\d+)(?:[.,](\d+))?\]?\s*(.*)$")
_SRT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_VTT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+):(\d+):(\d+)[.,](\d+)")


def _ts_to_seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_external_transcript(path: Path, media_duration: Optional[float] = None) -> List[Dict]:
    """Supports .json, .srt, .vtt, and .txt (timestamped or plain paragraphs)."""
    ext = path.suffix.lower()
    if ext == ".json":
        return _parse_json(path)
    if ext == ".srt":
        return _parse_srt(path)
    if ext == ".vtt":
        return _parse_vtt(path)
    if ext == ".txt":
        raw = path.read_text(encoding="utf-8", errors="replace")
        has_timestamps = any(_TXT_LINE_RE.match(l.strip()) for l in raw.splitlines() if l.strip())
        if has_timestamps:
            return _parse_txt(path)
        return _parse_txt_no_timestamps(path, media_duration)
    raise ValueError(f"Unsupported transcript extension '{ext}'")


def _parse_json(path: Path) -> List[Dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data["segments"] if isinstance(data, dict) and "segments" in data else data
    out = []
    for seg in raw:
        start = float(seg.get("start", seg.get("start_time", 0.0)))
        end = float(seg.get("end", seg.get("end_time", start)))
        text = str(seg.get("text", "")).strip()
        if text:
            out.append({"start": start, "end": end, "text": text})
    return out


def _parse_srt(path: Path) -> List[Dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.strip())
    out = []
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        idx = next((i for i, l in enumerate(lines) if _SRT_TIME_RE.search(l)), None)
        if idx is None:
            continue
        m = _SRT_TIME_RE.search(lines[idx])
        start, end = _ts_to_seconds(*m.groups()[0:4]), _ts_to_seconds(*m.groups()[4:8])
        text = " ".join(lines[idx + 1:]).strip()
        if text:
            out.append({"start": start, "end": end, "text": text})
    return out


def _parse_vtt(path: Path) -> List[Dict]:
    raw = path.read_text(encoding="utf-8", errors="replace").replace("WEBVTT", "", 1)
    blocks = re.split(r"\n\s*\n", raw.strip())
    out = []
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        idx = next((i for i, l in enumerate(lines) if _VTT_TIME_RE.search(l)), None)
        if idx is None:
            continue
        m = _VTT_TIME_RE.search(lines[idx])
        start, end = _ts_to_seconds(*m.groups()[0:4]), _ts_to_seconds(*m.groups()[4:8])
        text = re.sub(r"<[^>]+>", "", " ".join(lines[idx + 1:]).strip())
        if text:
            out.append({"start": start, "end": end, "text": text})
    return out


def _parse_txt(path: Path) -> List[Dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TXT_LINE_RE.match(line)
        if m:
            h, mm, s, ms, text = m.groups()
            start = int(h) * 3600 + int(mm) * 60 + int(s) + (int(ms) / 1000.0 if ms else 0.0)
            if text.strip():
                out.append({"start": start, "end": start, "text": text.strip()})
        elif out:
            out[-1]["text"] += " " + line
    for i in range(len(out) - 1):
        out[i]["end"] = out[i + 1]["start"]
    if out:
        out[-1]["end"] = out[-1]["start"] + 5.0
    return out


def _parse_txt_no_timestamps(path: Path, media_duration: Optional[float]) -> List[Dict]:
    """Plain paragraph transcript, no timestamps at all -- paragraphs spread
    proportionally (by length) across the media's actual duration."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if not paragraphs:
        return []

    out = []
    if media_duration and media_duration > 0:
        lengths = [max(len(p), 1) for p in paragraphs]
        total_len = sum(lengths)
        cursor = 0.0
        for p, length in zip(paragraphs, lengths):
            span = media_duration * (length / total_len)
            out.append({"start": cursor, "end": cursor + span, "text": p})
            cursor += span
    else:
        cursor = 0.0
        for p in paragraphs:
            out.append({"start": cursor, "end": cursor + 8.0, "text": p})
            cursor += 8.0
    return out


# ---------------------------------------------------------------------------
# Sentence merging (applied after transcription/parsing, before anything
# downstream -- makes choppy Whisper fragments read as full sentences)
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r'[.!?]["\')\]]?\s*$')


def merge_into_sentences(segments: List[Dict]) -> List[Dict]:
    if not segments:
        return []
    merged, buf_text, buf_start, buf_end = [], [], None, None
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if buf_start is None:
            buf_start = seg["start"]
        buf_end = seg["end"]
        buf_text.append(text)
        if _SENTENCE_END_RE.search(text):
            merged.append({"start": buf_start, "end": buf_end, "text": " ".join(buf_text).strip()})
            buf_text, buf_start, buf_end = [], None, None
    if buf_text:
        merged.append({"start": buf_start, "end": buf_end, "text": " ".join(buf_text).strip()})
    return merged


def full_text(segments: List[Dict]) -> str:
    return " ".join(seg["text"].strip() for seg in segments if seg["text"].strip())
