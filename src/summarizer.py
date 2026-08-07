"""
Generates a summary paragraph + a bullet list of key points from a transcript.

Two methods:
  - "openai" : sends the transcript to an OpenAI chat model, asks for JSON
               {"summary": "...", "key_points": [...]}. Best quality, needs an
               API key + internet. Can genuinely synthesize/paraphrase.
  - "local"  : TextRank-style extractive summarization (TF-IDF + cosine
               similarity + PageRank over sentences). No internet, no API key.
               Picks the most "central" *existing* sentences rather than
               writing new ones -- it cannot invent action items that aren't
               already phrased as standalone sentences in the transcript.

"auto" tries openai (if a key is available) and falls back to local on any
failure, so the pipeline never hard-fails on this step.

Two styles:
  - "narrative"    : a descriptive summary paragraph + general key points.
  - "action_items" : biased towards decisions, action items, and concrete
                     takeaways -- meant for the per-section summaries in the
                     chunked report, where "what should someone DO with this"
                     matters more than a restatement of what was said.

IMPORTANT: summarizing a tiny amount of text (a handful of sentences) has
nothing to compress -- both methods will return an EMPTY summary/key_points
in that case (see MIN_SENTENCES_FOR_SUMMARY) rather than parroting the input
back as a fake "summary". Callers should group enough transcript together
before calling this for the result to be worth showing.
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional

from .text_utils import full_text, split_sentences

logger = logging.getLogger("video2doc.summarizer")

# Below this many sentences, there usually isn't enough material to compress
# into a meaningfully shorter summary -- return empty rather than restate it.
MIN_SENTENCES_FOR_SUMMARY = 4


def generate_summary(segments: List[Dict], method: str = "auto",
                      api_key_env: str = "OPENAI_API_KEY", openai_model: str = "gpt-4o-mini",
                      max_summary_sentences: int = 5, max_key_points: int = 8,
                      style: str = "narrative") -> Dict:
    text = full_text(segments)
    if not text.strip():
        return {"summary": "", "key_points": []}

    sentence_count = len(split_sentences(text))
    if sentence_count < MIN_SENTENCES_FOR_SUMMARY:
        # Not enough content to summarize meaningfully -- avoid the "summary"
        # just being the same 2-3 sentences restated.
        return {"summary": "", "key_points": []}

    if method == "openai":
        return _summarize_openai(text, api_key_env, openai_model, max_key_points, style)

    if method == "local":
        return _summarize_local(text, max_summary_sentences, max_key_points)

    # auto
    api_key = os.environ.get(api_key_env)
    if api_key:
        try:
            return _summarize_openai(text, api_key_env, openai_model, max_key_points, style)
        except Exception as e:
            logger.warning(f"OpenAI summarization failed ({e}); falling back to local summarizer.")
    return _summarize_local(text, max_summary_sentences, max_key_points)


def _summarize_openai(text: str, api_key_env: str, model: str, max_key_points: int,
                       style: str = "narrative") -> Dict:
    from openai import OpenAI

    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise EnvironmentError(f"Environment variable '{api_key_env}' is not set.")

    client = OpenAI(api_key=api_key)

    if style == "action_items":
        prompt = (
            "You are extracting the substance from a slice of a meeting/video transcript -- "
            "not just restating what was said, but identifying what actually matters. "
            "Respond with ONLY a JSON object (no markdown fences, no preamble) in this exact "
            f"shape: {{\"summary\": \"1-2 sentence plain-language summary of what this part covers\", "
            f"\"key_points\": [\"...\", ... up to {max_key_points} items]}}\n\n"
            "For key_points: prioritize concrete action items, decisions made, important facts/numbers, "
            "problems raised, or learnings -- phrased as short standalone statements a reader could act on. "
            "Skip filler, small talk, and restatements. If there are genuinely no action items or notable "
            "facts in this slice, it's fine to return fewer key_points, even zero.\n\n"
            f"Transcript slice:\n{text[:60000]}"
        )
    else:
        prompt = (
            "You are summarizing a transcript for a written report. Read the transcript below "
            "and respond with ONLY a JSON object (no markdown fences, no preamble) in this exact "
            f"shape: {{\"summary\": \"a concise 4-6 sentence paragraph\", "
            f"\"key_points\": [\"point 1\", \"point 2\", ... up to {max_key_points} points]}}\n\n"
            f"Transcript:\n{text[:60000]}"
        )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    data = json.loads(raw)
    return {
        "summary": data.get("summary", "").strip(),
        "key_points": [str(p).strip() for p in data.get("key_points", []) if str(p).strip()],
    }


def _summarize_local(text: str, max_summary_sentences: int, max_key_points: int) -> Dict:
    sentences = split_sentences(text)

    try:
        import networkx as nx
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as e:
        raise ImportError(
            "scikit-learn and networkx are required for local summarization. "
            "Install with: pip install scikit-learn networkx"
        ) from e

    vectorizer = TfidfVectorizer(stop_words="english")
    tfidf = vectorizer.fit_transform(sentences)
    sim_matrix = cosine_similarity(tfidf)
    for i in range(len(sentences)):
        sim_matrix[i, i] = 0.0  # remove self-loops before ranking

    graph = nx.from_numpy_array(sim_matrix)
    try:
        scores = nx.pagerank(graph, max_iter=200)
    except Exception:
        # PageRank can fail to converge on pathological inputs; fall back to
        # simple degree-based scoring (sum of similarities) instead.
        scores = {i: float(sim_matrix[i].sum()) for i in range(len(sentences))}

    ranked = sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)

    # Cap how much of the input we're allowed to select as "key" -- selecting
    # nearly every sentence isn't a summary, it's a copy.
    n = len(sentences)
    summary_n = min(max_summary_sentences, max(1, n // 2))
    keypoint_n = min(max_key_points, max(1, n // 2))

    top_summary_idx = sorted(ranked[:summary_n])
    summary = " ".join(sentences[i] for i in top_summary_idx)

    top_keypoint_idx = sorted(ranked[:keypoint_n])
    key_points = [sentences[i] for i in top_keypoint_idx]

    return {"summary": summary, "key_points": key_points}
