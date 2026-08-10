"""
Pluggable LLM provider interface. Every provider (local extractive, OpenAI,
future Claude/local-model backends) implements the same contract, so the
pipeline never cares which one is active -- only config.llm_provider does.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, TypedDict


class SummaryResult(TypedDict):
    summary: str
    key_points: List[str]


class LLMProvider(ABC):
    """A provider only needs to implement summarize(). Anything else
    (translation, Q&A over a transcript, etc.) can be added the same way
    later without touching callers that only need summarize()."""

    @abstractmethod
    def summarize(self, text: str, style: str = "narrative",
                   max_summary_sentences: int = 5, max_key_points: int = 8) -> SummaryResult:
        """
        style: "narrative" (descriptive paragraph + general key points) or
               "action_items" (biased towards decisions/action items/takeaways).
        """
        raise NotImplementedError
