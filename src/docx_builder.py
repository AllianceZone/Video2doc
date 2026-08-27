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
    """Transcript spoken in [start, end).

    Segments are attributed to the interval their start falls in; an interval
    that no segment starts in falls back to any segment overlapping it, so a
    long sentence that merely spans the interval isn't dropped (which would
    render as "(no speech detected in this interval)").
    """
    owned = [seg["text"] for seg in segments if start <= seg["start"] < end]
    if not owned:
        owned = [seg["text"] for seg in segments
                 if seg["start"] < end and seg.get("end", seg["start"]) > start]
    return " ".join(owned).strip()


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


def build_chunked_document(groups: List[Dict], video_title: str, output_path: Path,
                            image_width_inches: float = 5.5, overall_summary: str = "",
                            overall_key_points: Optional[List[str]] = None) -> Path:
    """
    Builds the "4-part" report, structured in two levels:

      Summary GROUP (spans several chunks, ~4-10 by default):
        - a short summary of what this stretch covers
        - "Key Takeaways" -- action items / decisions / notable facts, only
          shown when there was enough material to actually distill (see
          summarizer.MIN_SENTENCES_FOR_SUMMARY) -- no group summary is shown
          otherwise, rather than restating the transcript back at the reader.
        Then, for each chunk nested inside the group:
          1. representative image(s)
          2. a clickable link to that chunk's audio note clip (if generated)
          3. the chunk's transcript

    Expected shape of each item in `groups` (see semantic_chunker.group_chunks_
    for_summary): {"start", "end", "chunks": [chunk, ...], "summary": str,
    "key_points": [str, ...]}. Each nested chunk (see semantic_chunker.
    compute_chunks) additionally carries "images" (List[Path]), "audio_clip"
    (Path or None), and "transcript_text" (str).
    """
    from .utils import seconds_to_hhmmss as _ts

    total_chunks = sum(len(g["chunks"]) for g in groups)
    doc = Document()
    title = doc.add_heading(video_title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = doc.add_paragraph(f"Auto-generated report  •  {total_chunks} sections")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if overall_summary:
        doc.add_heading("Overall Summary", level=1)
        doc.add_paragraph(overall_summary)
    if overall_key_points:
        doc.add_heading("Overall Key Points", level=1)
        for kp in overall_key_points:
            doc.add_paragraph(kp, style="List Bullet")

    doc.add_page_break()

    chunk_counter = 0
    for gi, group in enumerate(groups, 1):
        has_group_summary = bool(group.get("summary") or group.get("key_points"))
        if has_group_summary:
            doc.add_heading(f"[{_ts(group['start'])} – {_ts(group['end'])}]", level=1)
            if group.get("summary"):
                doc.add_paragraph(group["summary"]).paragraph_format.space_after = Pt(4)
            if group.get("key_points"):
                p = doc.add_paragraph()
                run = p.add_run("Key Takeaways")
                run.bold = True
                run.font.color.rgb = RGBColor(0x2E, 0x6B, 0x5E)
                for kp in group["key_points"]:
                    doc.add_paragraph(kp, style="List Bullet")
            doc.add_paragraph("").paragraph_format.space_after = Pt(2)

        for chunk in group["chunks"]:
            chunk_counter += 1
            doc.add_heading(f"Section {chunk_counter}  ·  [{_ts(chunk['start'])} – {_ts(chunk['end'])}]", level=2)

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

        if gi < len(groups):
            doc.add_paragraph("").paragraph_format.space_after = Pt(4)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    logger.info(f"Saved chunked Word document: {output_path}")
    return output_path
