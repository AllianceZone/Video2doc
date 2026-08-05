"""
Parses a user-supplied transcript file into the same segment format the rest of
the pipeline expects: List[{"start": float, "end": float, "text": str}]

Supported formats (chosen by file extension):
  - .json  : either a flat list of {"start","end","text"} dicts, or a
             whisper-style dict with a top-level "segments" list.
  - .srt   : standard SubRip subtitle format.
  - .vtt   : WebVTT subtitle format.
  - .txt   : one segment per line, formatted as "[HH:MM:SS] text" or
             "HH:MM:SS text" or "HH:MM:SS,mmm text". If no timestamp is found
             on a line, that line is merged into the previous segment's text.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("video2doc.transcript_parser")


def load_external_transcript(path: Path, video_duration: Optional[float] = None) -> List[Dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"External transcript file not found: {path}\n"
            f"Place your transcript there and check "
            f"transcription.external_transcript_dir / external_transcript_filename in the config."
        )

    ext = path.suffix.lower()
    if ext == ".json":
        segments = _parse_json(path)
    elif ext == ".srt":
        segments = _parse_srt(path)
    elif ext == ".vtt":
        segments = _parse_vtt(path)
    elif ext == ".txt":
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        if _TXT_LINE_RE.search(raw_text.split("\n", 1)[0]) is None and not any(
            _TXT_LINE_RE.match(l.strip()) for l in raw_text.splitlines() if l.strip()
        ):
            # No timestamps anywhere in the file -- plain paragraph transcript
            # (e.g. a meeting-recording export with no per-line times).
            logger.warning(
                f"No timestamps found in {path.name}; treating it as a plain paragraph "
                "transcript and spreading paragraphs evenly across the video duration. "
                "Frame/text pairing will be approximate."
            )
            segments = _parse_txt_no_timestamps(path, video_duration)
        else:
            segments = _parse_txt(path)
    else:
        raise ValueError(
            f"Unsupported external transcript extension '{ext}'. "
            "Use .json, .srt, .vtt, or .txt."
        )

    if not segments:
        raise ValueError(f"No transcript segments could be parsed from {path}")

    logger.info(f"Loaded {len(segments)} segments from external transcript: {path.name}")
    return segments


def _parse_json(path: Path) -> List[Dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_segments = data["segments"] if isinstance(data, dict) and "segments" in data else data

    segments = []
    for seg in raw_segments:
        start = float(seg.get("start", seg.get("start_time", 0.0)))
        end = float(seg.get("end", seg.get("end_time", start)))
        text = str(seg.get("text", "")).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return segments


def _parse_txt_no_timestamps(path: Path, video_duration: Optional[float]) -> List[Dict]:
    """
    Handles a plain transcript with no timestamps at all -- just paragraphs of
    speech, usually separated by blank lines (e.g. a raw meeting-recording export).

    Each paragraph becomes one segment. If a video duration is known, paragraphs
    are spread evenly across it (proportional to paragraph length, so longer
    paragraphs get a longer time slice). If duration is unknown, falls back to a
    fixed 8 seconds per paragraph -- timing will be inaccurate in that case, but
    the transcript text is still preserved and paired with frames in order.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    paragraphs = [" ".join(p.split()) for p in paragraphs]  # collapse internal whitespace

    if not paragraphs:
        return []

    segments = []
    if video_duration and video_duration > 0:
        # Distribute time proportionally to each paragraph's character length,
        # so a one-line paragraph doesn't eat the same time as a long one.
        lengths = [max(len(p), 1) for p in paragraphs]
        total_len = sum(lengths)
        cursor = 0.0
        for p, length in zip(paragraphs, lengths):
            span = video_duration * (length / total_len)
            segments.append({"start": cursor, "end": cursor + span, "text": p})
            cursor += span
    else:
        # No duration available -- fall back to a fixed spacing per paragraph.
        cursor = 0.0
        fallback_span = 8.0
        for p in paragraphs:
            segments.append({"start": cursor, "end": cursor + fallback_span, "text": p})
            cursor += fallback_span

    return segments


_SRT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _srt_ts_to_seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_srt(path: Path) -> List[Dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.strip())
    segments = []
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        time_line_idx = None
        for i, line in enumerate(lines):
            if _SRT_TIME_RE.search(line):
                time_line_idx = i
                break
        if time_line_idx is None:
            continue
        match = _SRT_TIME_RE.search(lines[time_line_idx])
        start = _srt_ts_to_seconds(*match.groups()[0:4])
        end = _srt_ts_to_seconds(*match.groups()[4:8])
        text = " ".join(lines[time_line_idx + 1:]).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return segments


_VTT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+):(\d+):(\d+)[.,](\d+)")


def _parse_vtt(path: Path) -> List[Dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("WEBVTT", "", 1)
    blocks = re.split(r"\n\s*\n", raw.strip())
    segments = []
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        time_line_idx = None
        for i, line in enumerate(lines):
            if _VTT_TIME_RE.search(line):
                time_line_idx = i
                break
        if time_line_idx is None:
            continue
        match = _VTT_TIME_RE.search(lines[time_line_idx])
        start = _srt_ts_to_seconds(*match.groups()[0:4])
        end = _srt_ts_to_seconds(*match.groups()[4:8])
        text = " ".join(lines[time_line_idx + 1:]).strip()
        text = re.sub(r"<[^>]+>", "", text)  # strip VTT styling tags
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return segments


_TXT_LINE_RE = re.compile(r"^\[?(\d+):(\d+):(\d+)(?:[.,](\d+))?\]?\s*(.*)$")


def _parse_txt(path: Path) -> List[Dict]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    segments = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = _TXT_LINE_RE.match(line)
        if match:
            h, m, s, ms, text = match.groups()
            start = int(h) * 3600 + int(m) * 60 + int(s) + (int(ms) / 1000.0 if ms else 0.0)
            if text.strip():
                segments.append({"start": start, "end": start, "text": text.strip()})
        elif segments:
            # No timestamp on this line -- treat as continuation of previous segment
            segments[-1]["text"] += " " + line

    # Backfill "end" times using the next segment's start, so downstream matching
    # (frame -> transcript window) still works sensibly.
    for i in range(len(segments) - 1):
        segments[i]["end"] = segments[i + 1]["start"]
    if segments:
        segments[-1]["end"] = segments[-1]["start"] + 5.0

    return segments
