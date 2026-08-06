"""Thin wrapper around GCP Gemini Apps for every agent.

Uses the Google Generative AI Python SDK and supports both API key and
Application Default Credentials-based auth, so the pipeline can run on GCP
and in GitHub Actions.
"""
import os
import time
from typing import Any

import google.generativeai as genai

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-pro")
MAX_TOKENS = int(os.environ.get("GEMINI_MAX_TOKENS", "2000"))


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    if hasattr(usage, "to_dict"):
        usage = usage.to_dict()
    if not isinstance(usage, dict):
        try:
            usage = dict(usage)
        except Exception:
            return {}
    normalized = {}
    for key, value in usage.items():
        normalized[key.lower()] = _int_value(value)
    return normalized


def _extract_tokens(usage: dict[str, int]) -> tuple[int, int]:
    input_tokens = (
        usage.get("prompt_tokens")
        or usage.get("prompttokens")
        or usage.get("input_tokens")
        or usage.get("inputtokens")
        or usage.get("prompt")
        or 0
    )
    output_tokens = (
        usage.get("completion_tokens")
        or usage.get("completiontokens")
        or usage.get("output_tokens")
        or usage.get("outputtokens")
        or usage.get("output")
        or 0
    )
    return input_tokens, output_tokens


def _extract_text(response: Any) -> str:
    if response is None:
        return ""
    if hasattr(response, "last"):
        last = getattr(response, "last")
        if isinstance(last, str):
            return last.strip()
        return str(last).strip()

    output = getattr(response, "output", None)
    if output:
        if isinstance(output, str):
            return output.strip()
        if isinstance(output, list) and output:
            first = output[0]
            if hasattr(first, "content"):
                content = getattr(first, "content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    return " ".join(str(item.get("text", item)).strip() for item in content if item).strip()
            return str(first).strip()

    if hasattr(response, "candidates"):
        candidates = getattr(response, "candidates")
        if candidates and isinstance(candidates, list):
            candidate = candidates[0]
            if hasattr(candidate, "content"):
                return str(getattr(candidate, "content")).strip()
            return str(candidate).strip()

    return str(response).strip()


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, cost_tracker=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
        self.client = genai
        self.model_name = model
        self.model = genai.GenerativeModel(model_name=self.model_name)
        self.cost_tracker = cost_tracker

    def _extract_usage(self, response: Any) -> Any:
        usage = getattr(response, "usage", None)
        if usage is None and hasattr(response, "to_dict"):
            usage = response.to_dict().get("usage")
        return usage

    def run(self, system_prompt: str, user_content: str, agent_name: str = "unknown", retries: int = 3) -> str:
        """Send a single-turn request and return the text response."""
        last_err = None
        prompt = [
            {"role": "system", "parts": [system_prompt]},
            {"role": "user", "parts": [user_content]},
        ]

        for attempt in range(retries):
            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": 0.0,
                        "max_output_tokens": MAX_TOKENS,
                    },
                )

                if self.cost_tracker is not None:
                    usage = self._extract_usage(response)
                    if usage:
                        usage = _normalize_usage(usage)
                        input_tokens, output_tokens = _extract_tokens(usage)
                        self.cost_tracker.record(agent_name, self.model_name, input_tokens, output_tokens)

                if hasattr(response, "text") and response.text:
                    return response.text.strip()
                return _extract_text(response)
            except Exception as e:
                last_err = e
                time.sleep(2 ** attempt)
        return f"_Agent call failed after {retries} attempts: {last_err}_"


