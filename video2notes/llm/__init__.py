"""
Provider factory: get_llm_provider() returns whichever provider is configured
(config.llm_provider), falling back to the local provider if OpenAI is
selected but no API key is set -- so the service degrades gracefully instead
of hard-failing every summarization request.
"""

import logging

from config import get_settings
from llm.base import LLMProvider  # noqa: F401
from llm.local import LocalTextRankProvider
from llm.openai import OpenAIProvider

logger = logging.getLogger("video2notes.llm")


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "openai":
        if settings.openai_api_key:
            return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)
        logger.warning("llm_provider=openai but no OPENAI_API_KEY set; falling back to local provider.")
    return LocalTextRankProvider()
