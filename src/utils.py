"""Shared helpers: logging setup, timestamp formatting, filesystem helpers."""

import logging
import re
from pathlib import Path


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("video2doc")


def seconds_to_hhmmss(seconds: float) -> str:
    """452.3 -> '00:07:32'"""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def seconds_to_filename_stamp(seconds: float) -> str:
    """452.3 -> '00-07-32' (safe for filenames across OSes)"""
    return seconds_to_hhmmss(seconds).replace(":", "-")


def hhmmss_to_seconds(stamp: str) -> float:
    """'00-07-32' or '00:07:32' -> 452.0"""
    parts = re.split(r"[:\-]", stamp)
    parts = [int(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return h * 3600 + m * 60 + s


def slugify(name: str) -> str:
    """Make a string safe to use as a folder/file name."""
    name = re.sub(r"[^\w\-. ]", "_", name)
    return name.strip().replace(" ", "_")[:120] or "video"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
