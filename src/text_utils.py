"""
Text-quality helpers applied on top of raw transcript segments.

Whisper (and some external transcripts) often emit short, choppy fragments that
don't align with sentence boundaries. For anything that will be *read* --
the Word doc, the PPTX, the summary -- it looks much better grouped into full
sentences. The raw, unmerged segments are still what gets saved to the
.json/.srt/.txt transcript files, so no timing precision is lost there.
"""

import re
from typing import Dict, List

_SENTENCE_END_RE = re.compile(r'[.!?]["\')\]]?\s*$')


def merge_into_sentences(segments: List[Dict]) -> List[Dict]:
    """
    Groups consecutive segments into sentence-level segments.
    A group ends when its accumulated text ends in ./!/? (optionally followed by
    a closing quote/bracket), or when we run out of segments.
    """
    if not segments:
        return []

    merged: List[Dict] = []
    buffer_text = []
    buffer_start = None
    buffer_end = None

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if buffer_start is None:
            buffer_start = seg["start"]
        buffer_end = seg["end"]
        buffer_text.append(text)

        if _SENTENCE_END_RE.search(text):
            merged.append({
                "start": buffer_start,
                "end": buffer_end,
                "text": " ".join(buffer_text).strip(),
            })
            buffer_text = []
            buffer_start = None
            buffer_end = None

    # Flush any trailing fragment that never hit sentence-ending punctuation
    if buffer_text:
        merged.append({
            "start": buffer_start,
            "end": buffer_end,
            "text": " ".join(buffer_text).strip(),
        })

    return merged


def full_text(segments: List[Dict]) -> str:
    """Concatenates all segment text into one block, space-separated."""
    return " ".join(seg["text"].strip() for seg in segments if seg["text"].strip())


def split_sentences(text: str) -> List[str]:
    """Lightweight regex sentence splitter (no NLTK/corpus download required)."""
    text = text.strip()
    if not text:
        return []
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])', text)
    return [s.strip() for s in sentences if s.strip()]
