from dataclasses import dataclass
from common.gemini_client import GeminiClient


@dataclass
class FailureAnalysis:
    agent_name: str
    root_cause: str
    fix_explanation: str
    patch: str | None  # unified diff text, or None if no confident fix


class FailureBaseAgent:
    """Shared interface for agents that diagnose a failed CI log and try to
    produce a git-apply-compatible patch that fixes it."""

    name: str = "base-failure"
    system_prompt: str = ""

    def __init__(self, client: GeminiClient):
        self.client = client

    def build_user_prompt(self, log_text: str) -> str:
        # Keep the tail of the log — that's almost always where the actual
        # error is, and it keeps the prompt small.
        return (
            f"CI log output (tail, may be truncated):\n```\n{log_text[-12000:]}\n```\n\n"
            "Respond in exactly this format, with no extra commentary:\n"
            "ROOT_CAUSE:\n<1-3 sentence diagnosis>\n"
            "FIX_EXPLANATION:\n<what the fix does and why, in plain language>\n"
            "PATCH:\n"
            "<a minimal unified diff (must apply cleanly with `git apply`), "
            "with no markdown code fences — or the single word NONE if you "
            "cannot confidently produce a safe fix from this log alone>"
        )

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    def run(self, log_text: str) -> FailureAnalysis:
        raw = self.client.run(self.system_prompt, self.build_user_prompt(log_text), agent_name=self.name)

        root_cause, fix_explanation, patch_text = "", "", "NONE"
        if "PATCH:" in raw:
            head, patch_text = raw.split("PATCH:", 1)
        else:
            head, patch_text = raw, "NONE"

        if "FIX_EXPLANATION:" in head:
            root_cause_part, fix_explanation = head.split("FIX_EXPLANATION:", 1)
        else:
            root_cause_part, fix_explanation = head, ""

        if "ROOT_CAUSE:" in root_cause_part:
            root_cause = root_cause_part.split("ROOT_CAUSE:", 1)[1]

        root_cause = root_cause.strip()
        fix_explanation = fix_explanation.strip()
        patch_text = self._strip_fences(patch_text)

        patch = patch_text if patch_text and patch_text.upper() != "NONE" else None

        return FailureAnalysis(
            agent_name=self.name,
            root_cause=root_cause or "(model did not return a clear diagnosis)",
            fix_explanation=fix_explanation,
            patch=patch,
        )
