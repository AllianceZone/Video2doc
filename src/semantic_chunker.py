"""
Groups consecutive kept frames into semantically coherent "chunks" instead of
treating every frame as its own report section. Two frames get merged into the
same chunk when the transcript spoken around them is topically similar *and*
they're close enough in time -- e.g. five frames of someone talking through the
same slide/point become one chunk, not five near-identical report entries.

Each chunk keeps its full list of original frames (for the audio/time window)
but only surfaces up to two representative images (first + last) when built
into a report, per the "club frames together" idea -- enough to show visual
change across the chunk without the noise of every intermediate frame.
"""

import logging
from pathlib import Path
from typing import Dict, List

from .docx_builder import compute_frame_text_map

logger = logging.getLogger("video2doc.semantic_chunker")


def _frame_start(frame_path: Path) -> float:
    from .utils import hhmmss_to_seconds
    return hhmmss_to_seconds(frame_path.stem.replace("frame_", ""))


def _segments_in_window(segments: List[Dict], start: float, end: float) -> List[Dict]:
    """Transcript segments belonging to the time window [start, end).

    A segment is "owned" by the window its *start* falls in, so each sentence is
    attributed to exactly one chunk in the normal case. But a single transcript
    segment can be long enough to span several chunks (Whisper/merged sentences
    routinely run 30-60s while frames -- and therefore chunk boundaries -- change
    every few seconds). Windows that no segment *starts* in would otherwise come
    out empty and render as "(no speech detected in this section)" even though
    someone is clearly still talking over them, so those windows fall back to any
    segment that merely overlaps them.
    """
    owned = [s for s in segments if start <= s["start"] < end]
    if owned:
        return owned
    return [s for s in segments
            if s["start"] < end and s.get("end", s["start"]) > start]


def compute_chunks(frame_paths: List[Path], segments: List[Dict],
                    similarity_threshold: float = 0.15, max_chunk_seconds: float = 60.0,
                    max_frames_per_chunk: int = 4) -> List[Dict]:
    """
    Returns a list of chunk dicts, each:
      {"start": float, "end": float, "frames": [Path, ...], "segments": [Dict, ...]}
    `frames` holds every original frame in the chunk (chronological);
    `segments` holds every transcript segment whose start falls in [start, end).
    """
    frame_paths = sorted(frame_paths, key=_frame_start)
    if not frame_paths:
        return []

    frame_text_map = compute_frame_text_map(frame_paths, segments)
    frame_texts = [frame_text_map.get(f.name, "") for f in frame_paths]

    similarity = _pairwise_similarity(frame_texts)

    chunks: List[List[Path]] = [[frame_paths[0]]]
    for i in range(1, len(frame_paths)):
        cur_chunk = chunks[-1]
        chunk_start = _frame_start(cur_chunk[0])
        candidate_start = _frame_start(frame_paths[i])
        time_ok = (candidate_start - chunk_start) <= max_chunk_seconds
        size_ok = len(cur_chunk) < max_frames_per_chunk
        sim_ok = similarity[i - 1][i] >= similarity_threshold if similarity is not None else False

        if time_ok and size_ok and sim_ok:
            cur_chunk.append(frame_paths[i])
        else:
            chunks.append([frame_paths[i]])

    result = []
    for i, chunk_frames in enumerate(chunks):
        start = _frame_start(chunk_frames[0])
        end = (
            _frame_start(chunks[i + 1][0]) if i + 1 < len(chunks) else start + 3600
        )
        chunk_segments = _segments_in_window(segments, start, end)
        result.append({"start": start, "end": end, "frames": chunk_frames, "segments": chunk_segments})

    logger.info(f"Grouped {len(frame_paths)} frames into {len(result)} semantic chunks "
                f"(similarity_threshold={similarity_threshold}, max_chunk_seconds={max_chunk_seconds})")
    return result


def group_chunks_for_summary(chunks: List[Dict], group_size: int = 6,
                              max_group_seconds: float = 180.0) -> List[Dict]:
    """
    Groups consecutive semantic chunks (see compute_chunks) into larger "summary
    groups" so summaries are generated from enough transcript to actually
    compress something. Summarizing one tiny 2-4-sentence chunk on its own has
    nothing to condense -- it just restates the chunk. Combining ~4-10 chunks
    worth of transcript gives the summarizer real material to work with.

    The fine-grained chunks (with their own images/audio/transcript) are kept
    nested inside each group unchanged -- grouping only affects where the
    *summary* is generated and shown, not the frame/audio/transcript display.

    Returns a list of: {"start": float, "end": float, "chunks": [chunk, ...],
    "segments": [Dict, ...]} (segments = every transcript segment across all
    chunks in the group, for summarization).
    """
    if not chunks:
        return []

    groups: List[List[Dict]] = [[chunks[0]]]
    for c in chunks[1:]:
        current = groups[-1]
        span = c["end"] - current[0]["start"]
        if len(current) >= group_size or span > max_group_seconds:
            groups.append([c])
        else:
            current.append(c)

    result = []
    for g in groups:
        start = g[0]["start"]
        end = g[-1]["end"]
        # A segment that spans a chunk boundary is carried by both chunks (see
        # _segments_in_window); dedupe so summarization doesn't see it twice.
        seen = set()
        segments = []
        for chunk in g:
            for seg in chunk["segments"]:
                key = (seg["start"], seg["end"], seg["text"])
                if key not in seen:
                    seen.add(key)
                    segments.append(seg)
        result.append({"start": start, "end": end, "chunks": g, "segments": segments})

    logger.info(f"Grouped {len(chunks)} chunks into {len(result)} summary groups "
                f"(group_size={group_size}, max_group_seconds={max_group_seconds})")
    return result


def representative_images(chunk: Dict) -> List[Path]:
    """First + last frame of the chunk (deduped) -- the 'club two frames' rule."""
    frames = chunk["frames"]
    if len(frames) == 1:
        return [frames[0]]
    if frames[0] == frames[-1]:
        return [frames[0]]
    return [frames[0], frames[-1]]


def _pairwise_similarity(texts: List[str]):
    """Cosine similarity between each text and the next one (index i vs i+1). None on failure."""
    if len(texts) < 2:
        return None
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        logger.warning("scikit-learn not available; semantic chunking will fall back to time-only grouping.")
        return None

    non_empty = [t if t.strip() else "" for t in texts]
    if all(not t.strip() for t in non_empty):
        return None

    try:
        vectorizer = TfidfVectorizer(stop_words="english")
        tfidf = vectorizer.fit_transform([t if t.strip() else " " for t in non_empty])
        sim_matrix = cosine_similarity(tfidf)
        return sim_matrix
    except ValueError:
        # Can happen if the vocabulary ends up empty (e.g. all-stopword transcript)
        return None
