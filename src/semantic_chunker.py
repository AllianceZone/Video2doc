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
        chunk_segments = [seg for seg in segments if start <= seg["start"] < end]
        result.append({"start": start, "end": end, "frames": chunk_frames, "segments": chunk_segments})

    logger.info(f"Grouped {len(frame_paths)} frames into {len(result)} semantic chunks "
                f"(similarity_threshold={similarity_threshold}, max_chunk_seconds={max_chunk_seconds})")
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
