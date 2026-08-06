"""Tracks estimated USD cost of every Gemini API call made during a pipeline
run, across both the raw SDK agents and the LangChain-based agent.

Rates are currently hardcoded from an early Gemini pricing model and should
be updated if you switch to a different model or pricing plan.
"""
from dataclasses import dataclass, field

# USD per 1,000,000 tokens: (input, output)
PRICING = {
    "gemini-1.5-pro": (2.00, 10.00),
}
DEFAULT_PRICING = (3.00, 15.00)  # fallback for an unlisted/future model


@dataclass
class UsageEntry:
    agent_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class CostTracker:
    """Shared across every agent in one orchestrator run — pass the same
    instance to GeminiClient(cost_tracker=...) and to CostImpactAgent."""

    entries: list = field(default_factory=list)

    def record(self, agent_name: str, model: str, input_tokens: int, output_tokens: int) -> float:
        in_price, out_price = PRICING.get(model, DEFAULT_PRICING)
        cost = (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price
        self.entries.append(UsageEntry(agent_name, model, input_tokens, output_tokens, cost))
        return cost

    @property
    def total_cost(self) -> float:
        return sum(e.cost_usd for e in self.entries)

    @property
    def total_tokens(self) -> int:
        return sum(e.input_tokens + e.output_tokens for e in self.entries)

    def summary_markdown(self) -> str:
        if not self.entries:
            return ""
        lines = [
            "<details>",
            f"<summary>\U0001F4B0 LLM cost for this run: ${self.total_cost:.4f} "
            f"({self.total_tokens:,} tokens across {len(self.entries)} calls)</summary>",
            "",
            "| Agent | Model | Input tokens | Output tokens | Cost |",
            "|---|---|---|---|---|",
        ]
        for e in self.entries:
            lines.append(
                f"| {e.agent_name} | {e.model} | {e.input_tokens:,} | "
                f"{e.output_tokens:,} | ${e.cost_usd:.4f} |"
            )
        lines.append("")
        lines.append("</details>")
        return "\n".join(lines)
