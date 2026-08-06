from dataclasses import dataclass
from common.gemini_client import GeminiClient


@dataclass
class AgentResult:
    agent_name: str
    verdict: str  # e.g. "PASS", "WARN", "FAIL", "INFO"
    summary: str


def parse_verdict_summary(raw: str) -> tuple[str, str]:
    """Shared by every review agent (raw-SDK and LangChain-based alike) so
    they all parse the same VERDICT/SUMMARY response format consistently."""
    verdict = "INFO"
    summary = raw
    for line in raw.splitlines():
        if line.upper().startswith("VERDICT:"):
            verdict = line.split(":", 1)[1].strip().upper()
            break
    if "SUMMARY:" in raw:
        summary = raw.split("SUMMARY:", 1)[1].strip()
    return verdict, summary


class BaseAgent:
    name: str = "base"
    system_prompt: str = ""

    def __init__(self, client: GeminiClient):
        self.client = client

    def build_user_prompt(self, diff: str, changed_files: list[str]) -> str:
        files_list = "\n".join(f"- {f}" for f in changed_files) or "(none listed)"
        return (
            f"Changed files:\n{files_list}\n\n"
            f"Unified diff of the pull request:\n```diff\n{diff[:12000]}\n```\n\n"
            "Respond in this exact format:\n"
            "VERDICT: <PASS|WARN|FAIL>\n"
            "SUMMARY:\n<your findings as short bullet points>"
        )

    def run(self, diff: str, changed_files: list[str]) -> AgentResult:
        raw = self.client.run(
            self.system_prompt, self.build_user_prompt(diff, changed_files), agent_name=self.name
        )
        verdict, summary = parse_verdict_summary(raw)
        return AgentResult(agent_name=self.name, verdict=verdict, summary=summary)
