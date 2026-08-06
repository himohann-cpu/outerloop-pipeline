"""Thin wrapper around the Anthropic Messages API used by every agent.

Kept in one place so retries, model selection, and error handling are
consistent across agents.
"""
import os
import time
from anthropic import Anthropic, APIError

DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "2000"))


class ClaudeClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, cost_tracker=None):
        self.client = Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self.cost_tracker = cost_tracker

    def run(self, system_prompt: str, user_content: str, agent_name: str = "unknown", retries: int = 3) -> str:
        """Send a single-turn request and return the text response."""
        last_err = None
        for attempt in range(retries):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_content}],
                )
                if self.cost_tracker is not None and getattr(resp, "usage", None):
                    self.cost_tracker.record(
                        agent_name, self.model,
                        resp.usage.input_tokens, resp.usage.output_tokens,
                    )
                return "".join(
                    block.text for block in resp.content if block.type == "text"
                ).strip()
            except APIError as e:
                last_err = e
                time.sleep(2 ** attempt)
        return f"_Agent call failed after {retries} attempts: {last_err}_"
