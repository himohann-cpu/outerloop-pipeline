"""Cost-impact review agent.

Analyzes a PR diff for cloud/infra cost implications — new or upsized
resources, heavy new dependencies, unbounded loops/fetches that scale with
traffic, etc. Deliberately built with LangChain (`ChatAnthropic`) instead of
the raw Anthropic SDK used by the other agents, to demonstrate a second
orchestration path in this pipeline. Reports its own token usage into the
same shared CostTracker as everything else, via LangChain's
`usage_metadata` on the returned message.
"""
import os

from langchain_anthropic import ChatAnthropic

from agents.base_agent import AgentResult, parse_verdict_summary

SYSTEM_PROMPT = (
    "You are a cloud cost engineer reviewing a pull request diff for cost "
    "impact. Look for: new or upsized cloud resources (databases, queues, "
    "compute instances, storage buckets), newly added heavy dependencies "
    "(large base images, ML/data libraries), N+1 queries or per-request "
    "external API/LLM calls that scale with traffic, missing pagination or "
    "limits on unbounded loops/fetches, new high-frequency scheduled jobs, "
    "and logging/storage that could grow unbounded. Judge whether the "
    "change is cost-neutral, a minor increase, or a meaningful cost "
    "increase worth flagging before merge. Use FAIL only for a clearly "
    "significant, avoidable cost increase; WARN for a moderate or unclear "
    "impact worth a second look; PASS if there's no material cost impact."
)


class CostImpactAgent:
    name = "Cost Impact"
    system_prompt = SYSTEM_PROMPT  # class attribute, for interface parity with the other agents

    def __init__(self, model: str | None = None, cost_tracker=None):
        self.model_name = model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
        self.llm = ChatAnthropic(model=self.model_name, max_tokens=2000)
        self.cost_tracker = cost_tracker

    def _build_prompt(self, diff: str, changed_files: list[str]) -> str:
        files_list = "\n".join(f"- {f}" for f in changed_files) or "(none listed)"
        return (
            f"Changed files:\n{files_list}\n\n"
            f"Unified diff of the pull request:\n```diff\n{diff[:12000]}\n```\n\n"
            "Respond in this exact format:\n"
            "VERDICT: <PASS|WARN|FAIL>\n"
            "SUMMARY:\n<your findings as short bullet points>"
        )

    def run(self, diff: str, changed_files: list[str]) -> AgentResult:
        messages = [
            ("system", self.system_prompt),
            ("human", self._build_prompt(diff, changed_files)),
        ]
        response = self.llm.invoke(messages)

        if self.cost_tracker is not None:
            usage = getattr(response, "usage_metadata", None) or {}
            self.cost_tracker.record(
                self.name,
                self.model_name,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )

        raw = response.content if isinstance(response.content, str) else str(response.content)
        verdict, summary = parse_verdict_summary(raw)
        return AgentResult(agent_name=self.name, verdict=verdict, summary=summary)
