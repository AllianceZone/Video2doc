"""
OpenAI-backed LLM provider. Genuinely synthesizes/paraphrases (unlike the
local extractive provider) -- better summaries and real action items, at the
cost of an API key + internet + per-call cost.
"""

import json
import re

from llm.base import LLMProvider, SummaryResult


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        if not api_key:
            raise ValueError("OpenAI API key is required for OpenAIProvider.")
        self.api_key = api_key
        self.model = model

    def summarize(self, text: str, style: str = "narrative",
                   max_summary_sentences: int = 5, max_key_points: int = 8) -> SummaryResult:
        from openai import OpenAI

        if not text.strip():
            return {"summary": "", "key_points": []}

        client = OpenAI(api_key=self.api_key)

        if style == "action_items":
            prompt = (
                "You are extracting the substance from a slice of a meeting/video transcript -- "
                "not just restating what was said, but identifying what actually matters. "
                "Respond with ONLY a JSON object (no markdown fences, no preamble) in this exact "
                f"shape: {{\"summary\": \"1-2 sentence plain-language summary of what this part covers\", "
                f"\"key_points\": [\"...\", ... up to {max_key_points} items]}}\n\n"
                "For key_points: prioritize concrete action items, decisions made, important facts/numbers, "
                "problems raised, or learnings -- phrased as short standalone statements a reader could act "
                "on. Skip filler, small talk, and restatements. It's fine to return fewer key_points, even "
                "zero, if there genuinely aren't any.\n\n"
                f"Transcript slice:\n{text[:60000]}"
            )
        else:
            prompt = (
                "You are summarizing a transcript for a written report. Respond with ONLY a JSON object "
                "(no markdown fences, no preamble) in this exact shape: "
                f"{{\"summary\": \"a concise {max_summary_sentences}-sentence paragraph\", "
                f"\"key_points\": [\"point 1\", ... up to {max_key_points} points]}}\n\n"
                f"Transcript:\n{text[:60000]}"
            )

        response = client.chat.completions.create(
            model=self.model,
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
