"""
Local, dependency-light LLM provider: TextRank-style extractive summarization
(TF-IDF + cosine similarity + PageRank over sentences). No internet, no API
key -- but it can only *select* existing sentences, not write new ones, so it
can't truly synthesize action items the way a real LLM can (see openai.py).
"""

import re
from typing import List

from llm.base import LLMProvider, SummaryResult

# Below this many sentences there usually isn't enough material to compress
# into something meaningfully shorter -- return empty rather than restate it.
MIN_SENTENCES_FOR_SUMMARY = 4


def split_sentences(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])', text)
    return [s.strip() for s in sentences if s.strip()]


class LocalTextRankProvider(LLMProvider):
    def summarize(self, text: str, style: str = "narrative",
                   max_summary_sentences: int = 5, max_key_points: int = 8) -> SummaryResult:
        sentences = split_sentences(text)
        if len(sentences) < MIN_SENTENCES_FOR_SUMMARY:
            return {"summary": "", "key_points": []}

        try:
            import networkx as nx
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError as e:
            raise ImportError(
                "scikit-learn and networkx are required for the local LLM provider. "
                "Install with: pip install scikit-learn networkx"
            ) from e

        vectorizer = TfidfVectorizer(stop_words="english")
        tfidf = vectorizer.fit_transform(sentences)
        sim_matrix = cosine_similarity(tfidf)
        for i in range(len(sentences)):
            sim_matrix[i, i] = 0.0

        graph = nx.from_numpy_array(sim_matrix)
        try:
            scores = nx.pagerank(graph, max_iter=200)
        except Exception:
            scores = {i: float(sim_matrix[i].sum()) for i in range(len(sentences))}

        ranked = sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)

        n = len(sentences)
        summary_n = min(max_summary_sentences, max(1, n // 2))
        keypoint_n = min(max_key_points, max(1, n // 2))

        summary = " ".join(sentences[i] for i in sorted(ranked[:summary_n]))
        key_points = [sentences[i] for i in sorted(ranked[:keypoint_n])]

        return {"summary": summary, "key_points": key_points}
