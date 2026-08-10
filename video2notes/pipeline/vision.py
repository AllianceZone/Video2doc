"""
Vision-specific pipeline stages: scoring and deduplicating extracted frames.
Drops blurry/blank/near-duplicate frames using perceptual hashing + a
Laplacian-variance sharpness score; when two frames are near-duplicates, keeps
the sharper one rather than just the earlier one.

(A natural place to add real computer-vision features later -- OCR on slides,
VLM-based captioning, etc. -- without touching the rest of the pipeline.)
"""

import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("video2notes.pipeline.vision")


def sharpness_score(image_path: Path) -> float:
    import cv2
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def _is_blurry(image_path: Path, threshold: float) -> bool:
    return sharpness_score(image_path) < threshold


def _is_blank(image_path: Path, std_threshold: float) -> bool:
    import cv2
    import numpy as np
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return True
    return float(np.std(img)) < std_threshold


def deduplicate_frames(frame_paths: List[Path], dedup_dir: Path, enabled: bool = True,
                        hamming_threshold: int = 5, blur_check: bool = True, blur_threshold: float = 60.0,
                        blank_check: bool = True, blank_std_threshold: float = 8.0) -> List[Path]:
    """Filters frame_paths (assumed sorted by timestamp), copies survivors
    into dedup_dir, returns their sorted paths."""
    from PIL import Image
    import imagehash

    dedup_dir.mkdir(parents=True, exist_ok=True)

    if not enabled:
        kept = []
        for p in frame_paths:
            dest = dedup_dir / p.name
            dest.write_bytes(p.read_bytes())
            kept.append(dest)
        return kept

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
        sharpness = sharpness_score(p)

        if kept_records and (h - kept_records[-1]["hash"]) <= hamming_threshold:
            if sharpness > kept_records[-1]["sharpness"]:
                kept_records[-1] = {"path": p, "hash": h, "sharpness": sharpness}
            dropped_dup += 1
            continue

        kept_records.append({"path": p, "hash": h, "sharpness": sharpness})

    kept = []
    for rec in kept_records:
        dest = dedup_dir / rec["path"].name
        dest.write_bytes(rec["path"].read_bytes())
        kept.append(dest)

    logger.info(f"Dedup: kept {len(kept)}/{len(frame_paths)} "
                f"(dropped {dropped_blur} blurry, {dropped_blank} blank, {dropped_dup} duplicate)")
    return kept
