"""Outer-loop multi-agent orchestrator.

Triggered by CI after code is pushed (see .github/workflows/agent-pipeline.yml).
Fetches the PR diff, fans it out to several specialized Claude agents in
parallel, aggregates their verdicts into one report, posts it as a PR
comment, writes it to the GitHub Actions job summary, and exits non-zero if
any agent returned FAIL (so the check can gate merges if desired).
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.claude_client import ClaudeClient
from common.github_client import GitHubClient
from common.cost_tracker import CostTracker
from agents.code_review_agent import CodeReviewAgent
from agents.test_coverage_agent import TestCoverageAgent
from agents.security_agent import SecurityAgent
from agents.docs_agent import DocsAgent
from agents.cost_impact_agent import CostImpactAgent

AGENT_CLASSES = [CodeReviewAgent, TestCoverageAgent, SecurityAgent, DocsAgent]

VERDICT_EMOJI = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}


def get_pr_number() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise RuntimeError("GITHUB_EVENT_PATH not set — must run inside GitHub Actions")
    with open(event_path) as f:
        event = json.load(f)
    pr_number = (
        event.get("pull_request", {}).get("number")
        or event.get("number")
    )
    if not pr_number:
        raise RuntimeError("Could not find a pull_request number in the event payload")
    return pr_number


def run_agents_in_parallel(diff: str, changed_files: list[str], cost_tracker: CostTracker):
    client = ClaudeClient(cost_tracker=cost_tracker)
    agents = [cls(client) for cls in AGENT_CLASSES]
    agents.append(CostImpactAgent(cost_tracker=cost_tracker))  # LangChain-based

    results = []
    with ThreadPoolExecutor(max_workers=len(agents)) as pool:
        futures = {pool.submit(a.run, diff, changed_files): a for a in agents}
        for future in as_completed(futures):
            results.append(future.result())
    # Keep a stable, readable order regardless of completion order
    order = {name: i for i, name in enumerate(
        [cls.name for cls in AGENT_CLASSES] + [CostImpactAgent.name]
    )}
    results.sort(key=lambda r: order.get(r.agent_name, 99))
    return results


def build_report(results, cost_tracker: CostTracker) -> str:
    lines = ["## 🤖 Multi-Agent Review\n"]
    for r in results:
        emoji = VERDICT_EMOJI.get(r.verdict, "ℹ️")
        lines.append(f"### {emoji} {r.agent_name} — `{r.verdict}`")
        lines.append(r.summary)
        lines.append("")
    cost_section = cost_tracker.summary_markdown()
    if cost_section:
        lines.append(cost_section)
        lines.append("")
    lines.append("_Generated automatically by the outer-loop agent pipeline._")
    return "\n".join(lines)


def main():
    pr_number = get_pr_number()
    gh = GitHubClient()

    diff = gh.get_pr_diff(pr_number)
    changed_files = gh.get_changed_files(pr_number)

    if not diff.strip():
        print("No diff content found — skipping agent run.")
        return

    cost_tracker = CostTracker()
    results = run_agents_in_parallel(diff, changed_files, cost_tracker)
    report = build_report(results, cost_tracker)

    gh.post_pr_comment(pr_number, report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(report + "\n")

    print(report)

    if any(r.verdict == "FAIL" for r in results):
        print("\nOne or more agents returned FAIL.")
        sys.exit(1)


if __name__ == "__main__":
    main()
