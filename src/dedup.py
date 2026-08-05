"""
Deduplicates and filters extracted frames.

Approach (lightweight alternative to fastdup, no GPU/heavy deps required):
  1. Blur filter: drop frames that are motion-blurred (variance of Laplacian too low).
  2. Blank filter: drop frames that are near-solid-color (transition/fade frames).
  3. Perceptual-hash dedup: walk frames in timestamp order, compare each candidate's
     pHash to the last *kept* frame; skip it if the Hamming distance is below the
     configured threshold (i.e. visually near-identical to what we already kept).

This keeps one representative frame per "visually distinct moment" instead of many
near-identical frames from a static slide/talking-head shot.
"""

import logging
from pathlib import Path
from typing import List

import cv2
import imagehash
import numpy as np
from PIL import Image

from .utils import ensure_dir

logger = logging.getLogger("video2doc.dedup")


def _is_blurry(image_path: Path, threshold: float) -> bool:
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return True
    variance = cv2.Laplacian(img, cv2.CV_64F).var()
    return variance < threshold


def _is_blank(image_path: Path, std_threshold: float) -> bool:
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return True
    return float(np.std(img)) < std_threshold


def _sharpness(image_path: Path) -> float:
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def deduplicate_frames(frame_paths: List[Path], dedup_dir: Path, enabled: bool,
                        hamming_threshold: int, blur_check: bool, blur_threshold: float,
                        blank_check: bool, blank_std_threshold: float) -> List[Path]:
    """
    Filters `frame_paths` (assumed sorted by timestamp) and copies the kept frames
    into `dedup_dir`, preserving original filenames (which encode timestamps).
    Returns the sorted list of kept frame paths (in dedup_dir).

    When a frame is a near-duplicate of the most recently kept frame, this keeps
    whichever of the two is sharper (higher Laplacian variance) rather than
    always keeping the earlier one -- so a brief motion-blurred moment doesn't
    "win" over a crisper frame one or two seconds later of the same scene.
    """
    ensure_dir(dedup_dir)

    if not enabled:
        logger.info("Dedup disabled; copying all frames through unchanged.")
        kept = []
        for p in frame_paths:
            dest = dedup_dir / p.name
            dest.write_bytes(p.read_bytes())
            kept.append(dest)
        return kept

    # kept_records holds (source_path, phash, sharpness) for each frame kept so far
    kept_records: List[dict] = []
    dropped_blur = dropped_blank = dropped_dup = 0

    for p in frame_paths:
        if blur_check and _is_blurry(p, blur_threshold):
            dropped_blur += 1
            continue
        if blank_check and _is_blank(p, blank_std_threshold):
            dropped_blank += 1
            continue

        img = Image.open(p)
        h = imagehash.phash(img)
        sharpness = _sharpness(p)

        if kept_records and (h - kept_records[-1]["hash"]) <= hamming_threshold:
            # Near-duplicate of the last kept frame -- keep whichever is sharper
            if sharpness > kept_records[-1]["sharpness"]:
                kept_records[-1] = {"path": p, "hash": h, "sharpness": sharpness}
            dropped_dup += 1
            continue

        kept_records.append({"path": p, "hash": h, "sharpness": sharpness})

    kept: List[Path] = []
    for rec in kept_records:
        dest = dedup_dir / rec["path"].name
        dest.write_bytes(rec["path"].read_bytes())
        kept.append(dest)

    logger.info(
        f"Dedup complete: kept {len(kept)}/{len(frame_paths)} frames "
        f"(dropped: {dropped_blur} blurry, {dropped_blank} blank, {dropped_dup} near-duplicate)"
    )
    return kept
