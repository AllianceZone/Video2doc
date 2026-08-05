"""
Generates an overall summary paragraph + a bullet list of key points from the
full transcript.

Two methods:
  - "openai" : sends the transcript to an OpenAI chat model, asks for JSON
               {"summary": "...", "key_points": ["...", ...]}. Best quality,
               needs an API key + internet.
  - "local"  : TextRank-style extractive summarization (TF-IDF + cosine
               similarity + PageRank over sentences). No internet, no API key,
               no corpus download -- just scikit-learn + networkx. Picks the
               most "central" sentences rather than writing new ones.

"auto" tries openai (if a key is available) and falls back to local on any
failure, so the pipeline never hard-fails on this step.
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional

from .text_utils import full_text, split_sentences

logger = logging.getLogger("video2doc.summarizer")


def generate_summary(segments: List[Dict], method: str = "auto",
                      api_key_env: str = "OPENAI_API_KEY", openai_model: str = "gpt-4o-mini",
                      max_summary_sentences: int = 5, max_key_points: int = 8) -> Dict:
    text = full_text(segments)
    if not text.strip():
        return {"summary": "", "key_points": []}

    if method == "openai":
        return _summarize_openai(text, api_key_env, openai_model, max_key_points)

    if method == "local":
        return _summarize_local(text, max_summary_sentences, max_key_points)

    # auto
    api_key = os.environ.get(api_key_env)
    if api_key:
        try:
            return _summarize_openai(text, api_key_env, openai_model, max_key_points)
        except Exception as e:
            logger.warning(f"OpenAI summarization failed ({e}); falling back to local summarizer.")
    return _summarize_local(text, max_summary_sentences, max_key_points)


def _summarize_openai(text: str, api_key_env: str, model: str, max_key_points: int) -> Dict:
    from openai import OpenAI

    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise EnvironmentError(f"Environment variable '{api_key_env}' is not set.")

    client = OpenAI(api_key=api_key)
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
    if len(sentences) <= max(max_summary_sentences, 3):
        summary = " ".join(sentences)
        return {"summary": summary, "key_points": sentences[:max_key_points]}

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

    top_summary_idx = sorted(ranked[:max_summary_sentences])
    summary = " ".join(sentences[i] for i in top_summary_idx)

    top_keypoint_idx = sorted(ranked[:max_key_points])
    key_points = [sentences[i] for i in top_keypoint_idx]

    return {"summary": summary, "key_points": key_points}
