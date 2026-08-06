"""Outer-loop failure-analysis pipeline.

Triggered when the build, test, or deploy job in ci-pipeline.yml fails.
For each failed stage's log (downloaded as an artifact into ./logs), runs
the matching diagnosis agent. If the agent produces a patch that applies
cleanly, opens an auto-fix PR. Either way, posts a diagnostic report as a
commit comment so the failure is never silent.
"""
import os
import subprocess

from common.gemini_client import GeminiClient
from common.github_client import GitHubClient
from common.git_ops import try_apply_fix
from common.cost_tracker import CostTracker
from agents.build_failure_agent import BuildFailureAgent
from agents.test_failure_agent import TestFailureAgent
from agents.deployment_failure_agent import DeploymentFailureAgent

LOGS_DIR = "logs"

# filename produced by the CI job -> (stage name, agent class)
LOG_AGENT_MAP = {
    "build.log": ("build", BuildFailureAgent),
    "test.log": ("test", TestFailureAgent),
    "deploy.log": ("deploy", DeploymentFailureAgent),
}


def find_logs() -> dict:
    found = {}
    for filename, (stage, agent_cls) in LOG_AGENT_MAP.items():
        path = os.path.join(LOGS_DIR, filename)
        if os.path.isfile(path):
            found[filename] = (stage, agent_cls, path)
    return found


def current_sha() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def target_branch(gh: GitHubClient) -> str:
    # PR context: fix branch should target the PR's head branch.
    # Push-to-main context: fall back to the branch that was pushed.
    return (
        os.environ.get("GITHUB_HEAD_REF")
        or os.environ.get("GITHUB_REF_NAME")
        or gh.get_default_branch()
    )


def build_report_section(stage: str, analysis, fix_pr_url: str | None) -> str:
    lines = [f"### ❌ {analysis.agent_name}", f"**Root cause:** {analysis.root_cause}"]
    if analysis.fix_explanation:
        lines.append(f"**Proposed fix:** {analysis.fix_explanation}")
    if fix_pr_url:
        lines.append(f"🤖 **Auto-fix PR opened:** {fix_pr_url}")
    elif analysis.patch:
        lines.append(
            "_A patch was suggested but did not apply cleanly to the current "
            "branch, so no PR was opened automatically:_"
        )
        lines.append(f"```diff\n{analysis.patch[:3000]}\n```")
    else:
        lines.append("_No automated fix available — this needs manual investigation._")
    return "\n\n".join(lines)


def main():
    logs = find_logs()
    if not logs:
        print("No failure logs found in ./logs — nothing to analyze.")
        return

    gh = GitHubClient()
    sha = current_sha()
    short_sha = sha[:7]
    base_branch = target_branch(gh)
    cost_tracker = CostTracker()
    client = GeminiClient(cost_tracker=cost_tracker)

    report_sections = ["## 🤖 Failure Analysis & Auto-Fix\n"]

    for filename, (stage, agent_cls, path) in logs.items():
        with open(path, "r", errors="replace") as f:
            log_text = f.read()

        agent = agent_cls(client)
        analysis = agent.run(log_text)

        fix_pr_url = None
        if analysis.patch:
            branch_name = f"agent-fix/{stage}-{short_sha}"
            try:
                applied = try_apply_fix(
                    analysis.patch,
                    branch_name,
                    commit_message=f"Auto-fix: {stage} failure ({short_sha})",
                )
            except Exception as e:
                print(f"git ops failed for {stage}: {e}")
                applied = False

            if applied:
                try:
                    pr = gh.create_pull_request(
                        head=branch_name,
                        base=base_branch,
                        title=f"🤖 Auto-fix: {stage} failure on {short_sha}",
                        body=(
                            f"Automated fix proposed by the **{analysis.agent_name}** "
                            f"agent after a `{stage}` failure on `{short_sha}`.\n\n"
                            f"**Root cause:** {analysis.root_cause}\n\n"
                            f"**Fix:** {analysis.fix_explanation}\n\n"
                            "⚠️ This patch was generated automatically — review "
                            "and run CI on it before merging."
                        ),
                    )
                    fix_pr_url = pr.get("html_url")
                except Exception as e:
                    print(f"Could not open fix PR for {stage}: {e}")

        report_sections.append(build_report_section(stage, analysis, fix_pr_url))

    cost_section = cost_tracker.summary_markdown()
    if cost_section:
        report_sections.append(cost_section)

    report = "\n\n".join(report_sections)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(report + "\n")

    try:
        gh.post_commit_comment(sha, report)
    except Exception as e:
        print(f"Could not post commit comment: {e}")


if __name__ == "__main__":
    main()
