"""
Resolves the configured input source into a local video file path.

Supported source_type values:
  - "local"   : path already on disk, used as-is
  - "youtube" : downloaded via yt-dlp
  - "gdrive"  : downloaded via gdown
  - "zoho"    : best-effort direct HTTP download of a Zoho WorkDrive share link
"""

import logging
import shutil
from pathlib import Path

import requests

logger = logging.getLogger("video2doc.downloader")


def resolve_video(source_type: str, source: str, video_quality: str, work_dir: Path) -> Path:
    source_type = source_type.lower().strip()

    if source_type == "local":
        return _handle_local(source)
    elif source_type == "youtube":
        return _handle_youtube(source, video_quality, work_dir)
    elif source_type == "gdrive":
        return _handle_gdrive(source, work_dir)
    elif source_type == "zoho":
        return _handle_zoho(source, work_dir)
    else:
        raise ValueError(
            f"Unknown input.source_type '{source_type}'. "
            "Expected one of: local, youtube, gdrive, zoho"
        )


def _handle_local(source: str) -> Path:
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Local video not found: {path}")
    logger.info(f"Using local video file: {path}")
    return path


def _handle_youtube(url: str, video_quality: str, work_dir: Path) -> Path:
    try:
        import yt_dlp
    except ImportError as e:
        raise ImportError(
            "yt-dlp is required for YouTube downloads. Install with: pip install yt-dlp"
        ) from e

    work_dir.mkdir(parents=True, exist_ok=True)

    # Map a friendly quality string to a yt-dlp format selector
    height = "".join(ch for ch in video_quality if ch.isdigit()) or "1080"
    fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"

    outtmpl = str(work_dir / "%(title)s.%(ext)s")
    ydl_opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
    }

    logger.info(f"Downloading YouTube video: {url} (max {height}p)")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        # If ffmpeg remuxed/merged into mp4, the extension may differ from prepare_filename
        p = Path(filepath)
        if not p.exists():
            p = p.with_suffix(".mp4")
        if not p.exists():
            # Fall back: pick the newest file in work_dir
            candidates = sorted(work_dir.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
            if not candidates:
                raise FileNotFoundError("yt-dlp reported success but no output file was found.")
            p = candidates[0]

    logger.info(f"Downloaded to: {p}")
    return p


def _handle_gdrive(url: str, work_dir: Path) -> Path:
    try:
        import gdown
    except ImportError as e:
        raise ImportError(
            "gdown is required for Google Drive downloads. Install with: pip install gdown"
        ) from e

    work_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading Google Drive file: {url}")

    output_path = gdown.download(url=url, output=str(work_dir) + "/", fuzzy=True, quiet=False)
    if not output_path:
        raise RuntimeError(
            "gdown failed to download the file. Ensure the Drive link sharing is set to "
            "'Anyone with the link' and that it points directly to the video file."
        )
    return Path(output_path)


def _handle_zoho(url: str, work_dir: Path) -> Path:
    """
    Best-effort downloader for Zoho WorkDrive share links.

    Zoho does not offer a simple public download API like Google Drive, so this
    attempts a direct HTTP GET, following redirects, and inspects the
    Content-Disposition header for a filename. This works for many "public share"
    links but may fail for links requiring login/session cookies -- in that case,
    download the file manually through your browser and use source_type: local instead.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Attempting direct download from Zoho link: {url}")
    logger.warning(
        "Zoho WorkDrive downloads are best-effort. If this fails, download the file "
        "manually and re-run with input.source_type: local."
    )

    with requests.get(url, stream=True, allow_redirects=True, timeout=60) as r:
        r.raise_for_status()

        filename = "zoho_video.mp4"
        cd = r.headers.get("content-disposition", "")
        if "filename=" in cd:
            filename = cd.split("filename=")[-1].strip('"; ')

        out_path = work_dir / filename
        with open(out_path, "wb") as f:
            shutil.copyfileobj(r.raw, f)

    logger.info(f"Downloaded to: {out_path}")
    return out_path
