"""
Builds a PowerPoint deck from kept frames + transcript + summary:
  1. Title slide
  2. Summary slide
  3. Key points slide
  4. One slide per kept frame: image + the transcript spoken in that window

Uses python-pptx directly (not pptxgenjs) since this runs standalone on the
user's machine as part of the generated project, not inside this sandbox.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

from .utils import hhmmss_to_seconds, seconds_to_hhmmss
from .text_utils import split_sentences

logger = logging.getLogger("video2doc.pptx_builder")

# ---- Palette (avoid AI-slide-deck cliches: no accent stripes, no default blue) ----
COLOR_BG = RGBColor(0xFF, 0xFF, 0xFF)
COLOR_TITLE = RGBColor(0x1B, 0x2A, 0x41)      # deep navy
COLOR_BODY = RGBColor(0x33, 0x38, 0x40)       # near-black gray
COLOR_ACCENT = RGBColor(0x2E, 0x6B, 0x5E)     # muted teal-green
COLOR_CARD_BG = RGBColor(0xF4, 0xF6, 0xF5)    # soft neutral card fill
COLOR_MUTED = RGBColor(0x76, 0x7E, 0x87)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def _frame_timestamp_seconds(frame_path: Path) -> float:
    stamp = frame_path.stem.replace("frame_", "")
    return hhmmss_to_seconds(stamp)


def _segments_between(segments: List[Dict], start: float, end: float) -> List[str]:
    return [seg["text"] for seg in segments if start <= seg["start"] < end]


def _new_presentation() -> Presentation:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    return prs


def _blank_slide(prs: Presentation):
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = COLOR_BG
    return slide


def _add_textbox(slide, left, top, width, height, text, size=18, bold=False,
                  color=COLOR_BODY, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
                  font_name="Calibri"):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font_name
    return box


def _add_bullets(slide, left, top, width, height, items: List[str], size=16,
                  color=COLOR_BODY, font_name="Calibri", space_after=10):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(space_after)
        run = p.add_run()
        run.text = f"•  {item}"
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.font.name = font_name
    return box


def _add_card(slide, left, top, width, height, fill=COLOR_CARD_BG):
    """Soft-filled rounded rectangle, no border, no accent stripe -- just a gentle card."""
    from pptx.enum.shapes import MSO_SHAPE
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _add_title_slide(prs: Presentation, video_title: str, num_frames: int):
    slide = _blank_slide(prs)
    _add_textbox(slide, Inches(1), Inches(2.7), Inches(11.3), Inches(1.4),
                 video_title.replace("_", " "), size=40, bold=True, color=COLOR_TITLE)
    _add_textbox(slide, Inches(1), Inches(3.9), Inches(11.3), Inches(0.6),
                 f"Video walkthrough  •  {num_frames} key frames  •  auto-generated report",
                 size=18, color=COLOR_MUTED)


def _add_summary_slide(prs: Presentation, summary: str):
    if not summary:
        return
    if len(summary) > 1400:
        summary = summary[:1380].rsplit(" ", 1)[0] + "…"
        font_size = 17
    elif len(summary) > 900:
        font_size = 18
    else:
        font_size = 20
    slide = _blank_slide(prs)
    _add_textbox(slide, Inches(0.7), Inches(0.6), Inches(11.9), Inches(0.8),
                 "Summary", size=32, bold=True, color=COLOR_TITLE)
    _add_card(slide, Inches(0.7), Inches(1.6), Inches(11.9), Inches(5.2))
    _add_textbox(slide, Inches(1.1), Inches(2.0), Inches(11.1), Inches(4.4),
                 summary, size=font_size, color=COLOR_BODY, anchor=MSO_ANCHOR.TOP)


def _add_key_points_slide(prs: Presentation, key_points: List[str]):
    if not key_points:
        return
    fitted = _fit_texts_to_slide(key_points, max_chars=1100, max_items=8)
    font_size = 17 if sum(len(t) for t in fitted) <= 700 else (15 if sum(len(t) for t in fitted) <= 1000 else 13)
    slide = _blank_slide(prs)
    _add_textbox(slide, Inches(0.7), Inches(0.6), Inches(11.9), Inches(0.8),
                 "Key Points", size=32, bold=True, color=COLOR_TITLE)
    _add_card(slide, Inches(0.7), Inches(1.6), Inches(11.9), Inches(5.2))
    _add_bullets(slide, Inches(1.1), Inches(2.0), Inches(11.1), Inches(4.4),
                 fitted, size=font_size, space_after=10)


def _truncate_text(text: str, max_chars: int) -> str:
    """Cuts a single block of text at a word boundary with an ellipsis, unlike
    _fit_texts_to_slide (which is for lists of separate bullet items and would
    just replace an over-budget single item with a placeholder note)."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(",.;: ") + "…"


def _fit_texts_to_slide(texts: List[str], max_chars: int = 850, max_items: int = 7) -> List[str]:
    """Caps bullet content so it can't overflow the card; points to the Word doc for the rest."""
    fitted = []
    total_chars = 0
    for t in texts:
        if len(fitted) >= max_items or total_chars + len(t) > max_chars:
            remaining = len(texts) - len(fitted)
            if remaining > 0:
                fitted.append(f"(+{remaining} more lines — see the Word doc for the full transcript)")
            break
        fitted.append(t)
        total_chars += len(t)
    return fitted


def _bullet_font_size(texts: List[str]) -> int:
    total_chars = sum(len(t) for t in texts)
    if total_chars > 650:
        return 12
    if total_chars > 400:
        return 13
    return 14


def _add_frame_slide(slide, frame_path: Path, timestamp: float, texts: List[str]):
    _add_textbox(slide, Inches(0.6), Inches(0.4), Inches(11.5), Inches(0.6),
                 f"[{seconds_to_hhmmss(timestamp)}]", size=20, bold=True, color=COLOR_ACCENT)

    # Image on the left, sized to fit within a fixed box while preserving aspect ratio
    img_box_left, img_box_top = Inches(0.6), Inches(1.15)
    img_box_w, img_box_h = Inches(6.6), Inches(5.9)
    try:
        with Image.open(frame_path) as im:
            iw, ih = im.size
        box_ratio = img_box_w / img_box_h
        img_ratio = iw / ih
        if img_ratio > box_ratio:
            draw_w = img_box_w
            draw_h = Inches(img_box_w / img_ratio / Inches(1))
        else:
            draw_h = img_box_h
            draw_w = Inches(img_box_h * img_ratio / Inches(1))
        left = img_box_left + (img_box_w - draw_w) / 2
        top = img_box_top + (img_box_h - draw_h) / 2
        slide.shapes.add_picture(str(frame_path), left, top, width=draw_w, height=draw_h)
    except Exception as e:
        logger.warning(f"Could not insert image {frame_path}: {e}")

    # Transcript text on the right, on a soft card
    text_left = Inches(7.5)
    _add_card(slide, text_left, Inches(1.15), Inches(5.25), Inches(5.9))
    if texts:
        fitted = _fit_texts_to_slide(texts)
        font_size = _bullet_font_size(fitted)
        _add_bullets(slide, text_left + Inches(0.35), Inches(1.5), Inches(4.55), Inches(5.2),
                     fitted, size=font_size, space_after=8)
    else:
        _add_textbox(slide, text_left + Inches(0.35), Inches(1.5), Inches(4.55), Inches(1.0),
                     "(no speech detected in this interval)", size=14, color=COLOR_MUTED)


def _paginate_items(items: List[str], max_chars: int = 750, max_items: int = 6) -> List[List[str]]:
    """Splits a list of strings into slide-sized chunks (by char budget and item count)."""
    pages: List[List[str]] = []
    current: List[str] = []
    current_chars = 0
    for item in items:
        if current and (len(current) >= max_items or current_chars + len(item) > max_chars):
            pages.append(current)
            current, current_chars = [], 0
        current.append(item)
        current_chars += len(item)
    if current:
        pages.append(current)
    return pages or [[]]


def build_audio_pptx(segments: List[Dict], video_title: str, output_path: Path,
                      summary: str = "", key_points: Optional[List[str]] = None,
                      transcript_text_override: Optional[str] = None) -> Path:
    """For audio-only input: title, summary, key points, then paginated transcript slides."""
    prs = _new_presentation()

    _add_title_slide(prs, video_title, 0)
    _add_summary_slide(prs, summary)
    _add_key_points_slide(prs, key_points or [])

    if transcript_text_override is not None:
        items = [l.strip() for l in transcript_text_override.splitlines() if l.strip()]
    else:
        items = [f"[{seconds_to_hhmmss(seg['start'])}]  {seg['text'].strip()}"
                  for seg in segments if seg["text"].strip()]

    pages = _paginate_items(items, max_chars=1500, max_items=10)
    for i, page_items in enumerate(pages, 1):
        slide = _blank_slide(prs)
        _add_textbox(slide, Inches(0.7), Inches(0.5), Inches(11.9), Inches(0.7),
                     f"Transcript ({i}/{len(pages)})", size=26, bold=True, color=COLOR_TITLE)
        _add_card(slide, Inches(0.7), Inches(1.35), Inches(11.9), Inches(5.7))
        total_chars = sum(len(t) for t in page_items)
        font_size = 15 if total_chars <= 500 else (13 if total_chars <= 950 else 11)
        _add_bullets(slide, Inches(1.1), Inches(1.7), Inches(11.1), Inches(5.1),
                     page_items, size=font_size, space_after=8)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    logger.info(f"Saved audio transcript PowerPoint deck: {output_path}")
    return output_path


def _add_audio_icon(slide, audio_path: Path, left, top, size=Inches(0.55)):
    """
    Embeds a playable audio clip on the slide (click-to-play icon), using
    python-pptx's add_movie() -- despite the name, PowerPoint's media model
    treats short audio the same way, so passing an audio mime type here embeds
    a working audio object rather than a video. Falls back to a text label
    with the filename if embedding fails for any reason (e.g. an unusual codec).
    """
    try:
        slide.shapes.add_movie(
            str(audio_path), left, top, size, size,
            poster_frame_image=None, mime_type="audio/mpeg",
        )
        return True
    except Exception as e:
        logger.warning(f"Could not embed audio {audio_path}, falling back to text label: {e}")
        _add_textbox(slide, left, top, Inches(3.0), Inches(0.4),
                     f"🔊 {Path(audio_path).name}", size=12, color=COLOR_ACCENT)
        return False


def _add_chunk_slide(slide, images: List[Path], audio_path, start: float, end: float,
                      transcript_text: str):
    _add_textbox(slide, Inches(0.6), Inches(0.35), Inches(11.5), Inches(0.55),
                 f"[{seconds_to_hhmmss(start)} – {seconds_to_hhmmss(end)}]",
                 size=19, bold=True, color=COLOR_ACCENT)

    # 1. Image(s) -- up to two, stacked or side-by-side depending on count
    img_left, img_top = Inches(0.6), Inches(1.0)
    img_area_w, img_area_h = Inches(6.4), Inches(6.05)
    if images:
        n = len(images)
        slot_h = img_area_h / n if n > 1 else img_area_h
        for idx, img in enumerate(images):
            try:
                with Image.open(img) as im:
                    iw, ih = im.size
                gap = Inches(0.15) if n > 1 else 0
                box_w, box_h = img_area_w, int(slot_h) - gap
                box_ratio = box_w / box_h
                img_ratio = iw / ih
                if img_ratio > box_ratio:
                    draw_w = box_w
                    draw_h = Inches(box_w / img_ratio / Inches(1))
                else:
                    draw_h = box_h
                    draw_w = Inches(box_h * img_ratio / Inches(1))
                left = img_left + (box_w - draw_w) / 2
                top = img_top + int(idx * slot_h) + (box_h - draw_h) / 2
                slide.shapes.add_picture(str(img), left, top, width=draw_w, height=draw_h)
            except Exception as e:
                logger.warning(f"Could not insert image {img}: {e}")

    # 2. Audio note icon, top-right of the text column
    text_left = Inches(7.3)
    if audio_path:
        _add_audio_icon(slide, audio_path, text_left, Inches(1.0))
        _add_textbox(slide, text_left + Inches(0.75), Inches(1.08), Inches(3.5), Inches(0.4),
                     "Audio note", size=12, color=COLOR_MUTED)

    # 3. Transcript, on a card (no per-chunk summary anymore -- see the group
    # takeaways slide instead, which has real material to work with)
    card_top = Inches(1.75)
    _add_card(slide, text_left, card_top, Inches(5.45), Inches(5.3))

    content = split_sentences(transcript_text) if transcript_text else []
    if content:
        fitted = _fit_texts_to_slide(content, max_chars=1150, max_items=11)
        font_size = 14 if sum(len(t) for t in fitted) <= 700 else (13 if sum(len(t) for t in fitted) <= 950 else 11)
        _add_bullets(slide, text_left + Inches(0.3), card_top + Inches(0.3), Inches(4.85), Inches(4.7),
                     fitted, size=font_size, space_after=8)
    else:
        _add_textbox(slide, text_left + Inches(0.3), card_top + Inches(0.3), Inches(4.85), Inches(1.0),
                     "(no speech detected in this section)", size=13, color=COLOR_MUTED)


def _add_group_takeaways_slide(prs: Presentation, start: float, end: float,
                                summary: str, key_points: List[str]):
    """One slide per summary group (spanning several chunks): what this
    stretch covers + concrete takeaways. Skipped entirely if there wasn't
    enough material to distill anything (see summarizer.MIN_SENTENCES_FOR_SUMMARY).

    Summary and key points get separate layout regions (rather than being
    thrown into one bullet-fitting call) so a long summary paragraph can't eat
    the entire char budget and leave zero room for the key points themselves.
    """
    if not summary and not key_points:
        return
    slide = _blank_slide(prs)
    _add_textbox(slide, Inches(0.7), Inches(0.5), Inches(11.9), Inches(0.7),
                 f"[{seconds_to_hhmmss(start)} – {seconds_to_hhmmss(end)}]",
                 size=26, bold=True, color=COLOR_TITLE)

    if summary and key_points:
        # Summary on top (own card), key points below (own card) -- each gets
        # its own space so neither can starve the other.
        summary_h = Inches(2.15)
        _add_card(slide, Inches(0.7), Inches(1.35), Inches(11.9), summary_h)
        fitted_summary = _truncate_text(summary, 650)
        font_size = 15 if len(fitted_summary) <= 350 else 13
        _add_textbox(slide, Inches(1.1), Inches(1.6), Inches(11.1), summary_h - Inches(0.5),
                     fitted_summary, size=font_size, color=COLOR_BODY)

        kp_top = Inches(1.35) + summary_h + Inches(0.2)
        kp_h = Inches(7.5) - kp_top - Inches(0.4)
        _add_card(slide, Inches(0.7), kp_top, Inches(11.9), kp_h)
        fitted_kp = _fit_texts_to_slide(key_points, max_chars=650, max_items=6)
        kp_font = 14 if sum(len(t) for t in fitted_kp) <= 400 else 12
        _add_bullets(slide, Inches(1.1), kp_top + Inches(0.25), Inches(11.1), kp_h - Inches(0.4),
                     fitted_kp, size=kp_font, space_after=8)
    else:
        _add_card(slide, Inches(0.7), Inches(1.35), Inches(11.9), Inches(5.7))
        if summary:
            fitted = _truncate_text(summary, 1200)
            _add_textbox(slide, Inches(1.1), Inches(1.7), Inches(11.1), Inches(5.1),
                         fitted, size=16, color=COLOR_BODY)
        else:
            fitted = _fit_texts_to_slide(key_points, max_chars=1300, max_items=10)
            font_size = 16 if sum(len(t) for t in fitted) <= 600 else 14
            _add_bullets(slide, Inches(1.1), Inches(1.7), Inches(11.1), Inches(5.1),
                         fitted, size=font_size, space_after=10)


def build_chunked_pptx(groups: List[Dict], video_title: str, output_path: Path,
                        overall_summary: str = "", overall_key_points: Optional[List[str]] = None) -> Path:
    """
    Structured in two levels, matching docx_builder.build_chunked_document:
      - one "takeaways" slide per summary group (skipped if nothing to distill)
      - one slide per chunk nested in that group: image(s) + audio note + transcript
    See semantic_chunker.group_chunks_for_summary for the expected input shape.
    """
    total_chunks = sum(len(g["chunks"]) for g in groups)
    prs = _new_presentation()
    _add_title_slide(prs, video_title, total_chunks)
    _add_summary_slide(prs, overall_summary)
    _add_key_points_slide(prs, overall_key_points or [])

    for group in groups:
        _add_group_takeaways_slide(
            prs, group["start"], group["end"], group.get("summary", ""), group.get("key_points", []),
        )
        for chunk in group["chunks"]:
            slide = _blank_slide(prs)
            _add_chunk_slide(
                slide, images=chunk.get("images", []), audio_path=chunk.get("audio_clip"),
                start=chunk["start"], end=chunk["end"], transcript_text=chunk.get("transcript_text", ""),
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    logger.info(f"Saved chunked PowerPoint deck: {output_path}")
    return output_path


def build_pptx(frame_paths: List[Path], segments: List[Dict], video_title: str,
               output_path: Path, summary: str = "", key_points: Optional[List[str]] = None,
               text_overrides: Optional[Dict[str, str]] = None) -> Path:
    frame_paths = sorted(frame_paths, key=_frame_timestamp_seconds)
    prs = _new_presentation()

    _add_title_slide(prs, video_title, len(frame_paths))
    _add_summary_slide(prs, summary)
    _add_key_points_slide(prs, key_points or [])

    for i, frame_path in enumerate(frame_paths):
        start_ts = _frame_timestamp_seconds(frame_path)
        end_ts = (
            _frame_timestamp_seconds(frame_paths[i + 1])
            if i + 1 < len(frame_paths)
            else start_ts + 3600
        )
        if text_overrides is not None and frame_path.name in text_overrides:
            override = text_overrides[frame_path.name]
            texts = [line.strip() for line in override.splitlines() if line.strip()]
        else:
            texts = _segments_between(segments, start_ts, end_ts)
        slide = _blank_slide(prs)
        _add_frame_slide(slide, frame_path, start_ts, texts)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    logger.info(f"Saved PowerPoint deck: {output_path}")
    return output_path
