"""
Builds a Word document that walks through the video chronologically:
a summary + key points section up front, then for each kept (deduplicated)
frame, the frame image followed by the transcript text spoken in that window.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml.ns import qn
from docx.oxml.shared import OxmlElement
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .utils import hhmmss_to_seconds, seconds_to_hhmmss

logger = logging.getLogger("video2doc.docx_builder")


def _add_hyperlink(paragraph, text: str, target_path: str):
    """
    python-docx has no built-in hyperlink API -- this adds a relative-path
    hyperlink run (relative to the .docx file's own location) so clicking
    "🔊 Play audio note" in Word opens the clip alongside it, as long as the
    audio_notes/ folder travels with the document.
    """
    part = paragraph.part
    r_id = part.relate_to(target_path, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "2E6B5E")
    rPr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rPr.append(underline)
    run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _frame_timestamp_seconds(frame_path: Path) -> float:
    # filename format: frame_HH-MM-SS.jpg
    stamp = frame_path.stem.replace("frame_", "")
    return hhmmss_to_seconds(stamp)


def _segments_between(segments: List[Dict], start: float, end: float) -> str:
    texts = [seg["text"] for seg in segments if start <= seg["start"] < end]
    return " ".join(texts).strip()


def compute_frame_text_map(frame_paths: List[Path], segments: List[Dict]) -> Dict[str, str]:
    """
    Returns {frame_filename: transcript_text} for the transcript spoken between
    each frame and the next one. Exposed publicly so a UI can show/edit this
    mapping before final export.
    """
    frame_paths = sorted(frame_paths, key=_frame_timestamp_seconds)
    mapping = {}
    for i, frame_path in enumerate(frame_paths):
        start_ts = _frame_timestamp_seconds(frame_path)
        end_ts = (
            _frame_timestamp_seconds(frame_paths[i + 1])
            if i + 1 < len(frame_paths)
            else start_ts + 3600
        )
        mapping[frame_path.name] = _segments_between(segments, start_ts, end_ts)
    return mapping


def build_audio_document(segments: List[Dict], video_title: str, output_path: Path,
                          summary: str = "", key_points: Optional[List[str]] = None,
                          transcript_text_override: Optional[str] = None) -> Path:
    """
    For audio-only input (no video/frames): title, summary, key points, then the
    full transcript as timestamped paragraphs.
    """
    doc = Document()

    title = doc.add_heading(video_title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = doc.add_paragraph("Auto-generated audio transcript report")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if summary:
        doc.add_heading("Summary", level=1)
        doc.add_paragraph(summary)

    if key_points:
        doc.add_heading("Key Points", level=1)
        for kp in key_points:
            doc.add_paragraph(kp, style="List Bullet")

    doc.add_page_break()
    doc.add_heading("Full Transcript", level=1)

    if transcript_text_override is not None:
        for line in transcript_text_override.splitlines():
            line = line.strip()
            if line:
                p = doc.add_paragraph(line)
                p.paragraph_format.space_after = Pt(8)
    else:
        for seg in segments:
            text = seg["text"].strip()
            if not text:
                continue
            p = doc.add_paragraph()
            run_ts = p.add_run(f"[{seconds_to_hhmmss(seg['start'])}]  ")
            run_ts.bold = True
            p.add_run(text)
            p.paragraph_format.space_after = Pt(8)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    logger.info(f"Saved audio transcript Word document: {output_path}")
    return output_path


def build_document(frame_paths: List[Path], segments: List[Dict], video_title: str,
                    output_path: Path, image_width_inches: float = 6.0,
                    text_overrides: Optional[Dict[str, str]] = None,
                    summary: str = "", key_points: Optional[List[str]] = None) -> Path:
    frame_paths = sorted(frame_paths, key=_frame_timestamp_seconds)
    doc = Document()

    # Title page
    title = doc.add_heading(video_title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = doc.add_paragraph(f"Auto-generated frame + transcript report "
                                  f"({len(frame_paths)} frames)")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if summary:
        doc.add_heading("Summary", level=1)
        doc.add_paragraph(summary)

    if key_points:
        doc.add_heading("Key Points", level=1)
        for kp in key_points:
            doc.add_paragraph(kp, style="List Bullet")

    doc.add_page_break()

    for i, frame_path in enumerate(frame_paths):
        start_ts = _frame_timestamp_seconds(frame_path)
        end_ts = (
            _frame_timestamp_seconds(frame_paths[i + 1])
            if i + 1 < len(frame_paths)
            else start_ts + 3600  # last frame: grab everything remaining
        )

        heading = doc.add_heading(f"[{seconds_to_hhmmss(start_ts)}]", level=2)

        try:
            doc.add_picture(str(frame_path), width=Inches(image_width_inches))
        except Exception as e:
            logger.warning(f"Could not insert image {frame_path}: {e}")
            doc.add_paragraph(f"(Image could not be inserted: {frame_path.name})")

        if text_overrides is not None and frame_path.name in text_overrides:
            text = text_overrides[frame_path.name]
        else:
            text = _segments_between(segments, start_ts, end_ts)

        if text:
            for line in text.splitlines():
                line = line.strip()
                if line:
                    p = doc.add_paragraph(line)
                    p.paragraph_format.space_after = Pt(6)
        else:
            p = doc.add_paragraph("(no speech detected in this interval)")
            p.paragraph_format.space_after = Pt(18)

        if i < len(frame_paths) - 1:
            doc.add_paragraph("").paragraph_format.space_after = Pt(6)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    logger.info(f"Saved Word document: {output_path}")
    return output_path


def build_chunked_document(chunks: List[Dict], video_title: str, output_path: Path,
                            image_width_inches: float = 5.5, overall_summary: str = "",
                            overall_key_points: Optional[List[str]] = None) -> Path:
    """
    Builds the "4-part" report: for each semantic chunk --
      1. representative image(s)
      2. a clickable link to that chunk's audio note clip (if one was generated)
      3. the chunk's transcript
      4. the chunk's own mini-summary + key point(s)

    Each item in `chunks` (see semantic_chunker.compute_chunks) is expected to
    also carry, once processed: "images" (List[Path]), "audio_clip" (Path or
    None, relative to output_path's folder), "summary" (str), "key_points" (List[str]).
    """
    from .utils import seconds_to_hhmmss as _ts

    doc = Document()
    title = doc.add_heading(video_title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = doc.add_paragraph(f"Auto-generated report  •  {len(chunks)} sections")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if overall_summary:
        doc.add_heading("Overall Summary", level=1)
        doc.add_paragraph(overall_summary)
    if overall_key_points:
        doc.add_heading("Overall Key Points", level=1)
        for kp in overall_key_points:
            doc.add_paragraph(kp, style="List Bullet")

    doc.add_page_break()

    for i, chunk in enumerate(chunks, 1):
        doc.add_heading(f"Section {i}  ·  [{_ts(chunk['start'])} – {_ts(chunk['end'])}]", level=2)

        # 1. Image(s)
        for img in chunk.get("images", []):
            try:
                doc.add_picture(str(img), width=Inches(image_width_inches))
            except Exception as e:
                logger.warning(f"Could not insert image {img}: {e}")

        # 2. Audio note link
        audio_clip = chunk.get("audio_clip")
        if audio_clip:
            p = doc.add_paragraph()
            rel_path = f"audio_notes/{Path(audio_clip).name}"
            _add_hyperlink(p, f"🔊 Play audio note ({Path(audio_clip).name})", rel_path)
            p.paragraph_format.space_after = Pt(6)

        # 3. Transcript
        transcript_text = chunk.get("transcript_text", "")
        if transcript_text:
            p = doc.add_paragraph(transcript_text)
            p.paragraph_format.space_after = Pt(8)
        else:
            doc.add_paragraph("(no speech detected in this section)").paragraph_format.space_after = Pt(8)

        # 4. Mini-summary + key point(s)
        if chunk.get("summary"):
            p = doc.add_paragraph()
            run = p.add_run("Summary: ")
            run.bold = True
            run.font.color.rgb = RGBColor(0x2E, 0x6B, 0x5E)
            p.add_run(chunk["summary"])
            p.paragraph_format.space_after = Pt(4)
        for kp in chunk.get("key_points", []):
            doc.add_paragraph(kp, style="List Bullet")

        if i < len(chunks):
            doc.add_paragraph("").paragraph_format.space_after = Pt(4)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    logger.info(f"Saved chunked Word document: {output_path}")
    return output_path
