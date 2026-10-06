"""Turn scenario results into metrics, a Markdown report and a pass/fail gate."""
from __future__ import annotations

import statistics

from evals import harness as h

METRIC_HELP = {
    "diagnosis_accuracy": "root cause named correctly / scenarios scored",
    "verified_fix_rate": "fixable failures where the patch applied and the stage passed again",
    "patch_apply_rate": "proposed patches that `git apply` accepts",
    "pr_precision": "auto-fix PRs that would have been correct / PRs that would have been opened",
    "abstention_accuracy": "unfixable failures where the agent correctly offered no patch",
    "harmful_pr_rate": "scenarios that would have opened a wrong auto-fix PR",
    "cost_per_scenario_usd": "mean LLM spend per failure analysed",
    "cost_per_verified_fix_usd": "total LLM spend / verified fixes",
    "latency_p50_s": "median agent latency",
}
ICON = {
    h.VERIFIED_FIX: "✅", h.CORRECT_ABSTAIN: "✅", h.MISSED: "➖", h.UNAPPLIABLE: "⚠️",
    h.WRONG_FIX: "❌", h.FALSE_FIX: "❌", h.AGENT_ERROR: "💥", h.INVALID: "🚧",
}


def _ratio(numerator: int, denominator: int):
    return round(numerator / denominator, 4) if denominator else None


def compute_metrics(results: list) -> dict:
    """`results` is a list of dicts (ScenarioResult.to_dict())."""
    scored = [r for r in results if r["outcome"] not in (h.AGENT_ERROR, h.INVALID)]
    fixable = [r for r in scored if r["fixable"]]
    unfixable = [r for r in scored if not r["fixable"]]
    proposed = [r for r in scored if r["patch_proposed"]]
    applied = [r for r in scored if r["patch_applies"]]
    verified = [r for r in scored if r["outcome"] == h.VERIFIED_FIX]
    harmful = [r for r in scored if r["outcome"] in h.HARMFUL]
    total_cost = sum(r["cost_usd"] for r in results)
    return {
        "scenarios_run": len(results),
        "scenarios_scored": len(scored),
        "agent_errors": sum(r["outcome"] == h.AGENT_ERROR for r in results),
        "invalid_scenarios": sum(r["outcome"] == h.INVALID for r in results),
        "diagnosis_accuracy": _ratio(sum(r["diagnosis_correct"] for r in scored), len(scored)),
        "verified_fix_rate": _ratio(len(verified), len(fixable)),
        "patch_apply_rate": _ratio(len(applied), len(proposed)),
        "pr_precision": _ratio(len(verified), len(applied)),
        "abstention_accuracy": _ratio(
            sum(r["outcome"] == h.CORRECT_ABSTAIN for r in unfixable), len(unfixable)),
        "harmful_pr_rate": _ratio(len(harmful), len(scored)),
        "total_cost_usd": round(total_cost, 4),
        "cost_per_scenario_usd": round(total_cost / len(results), 4) if results else None,
        "cost_per_verified_fix_usd": round(total_cost / len(verified), 4) if verified else None,
        "latency_p50_s": round(statistics.median(r["latency_s"] for r in scored), 2) if scored else None,
    }


def check_thresholds(metrics: dict, thresholds: dict) -> list:
    """Return a list of human-readable gate failures (empty = pass)."""
    failures = []
    if metrics["invalid_scenarios"]:
        failures.append(f"{metrics['invalid_scenarios']} scenario(s) could not be scored (see Notes)")
    if metrics["agent_errors"]:
        failures.append(f"{metrics['agent_errors']} model call(s) failed, so the run is incomplete")
    for name, bound in thresholds.items():
        value = metrics.get(name)
        if value is None:
            continue
        if "min" in bound and value < bound["min"]:
            failures.append(f"{name} = {value} is below the minimum {bound['min']}")
        if "max" in bound and value > bound["max"]:
            failures.append(f"{name} = {value} is above the maximum {bound['max']}")
    return failures


def compare_to_baseline(results: list, baseline: dict) -> list:
    """List scenarios whose outcome changed versus an earlier results.json."""
    previous = {(r["id"], r["trial"]): r["outcome"] for r in baseline["results"]}
    changes = []
    for r in results:
        before = previous.get((r["id"], r["trial"]))
        if before and before != r["outcome"]:
            changes.append(f"`{r['id']}` (trial {r['trial']}): {before} → {r['outcome']}")
    return changes


def _fmt(name: str, value) -> str:
    if value is None:
        return "n/a"
    if name.endswith("_usd"):
        return f"${value:.4f}"
    if name.endswith("_s"):
        return f"{value}s"
    return f"{value:.0%}"


def render_markdown(run: dict) -> str:
    metrics, results = run["metrics"], run["results"]
    lines = [
        "## Failure-agent eval report",
        "",
        f"Client: `{run['client']}` · model: `{run['model']}` · "
        f"{metrics['scenarios_run']} runs ({run['trials']} trial(s) per scenario) · {run['started_at']}",
        "",
        "| Metric | Value | Meaning |",
        "|---|---|---|",
    ]
    for name, meaning in METRIC_HELP.items():
        lines.append(f"| {name} | {_fmt(name, metrics[name])} | {meaning} |")
    lines += ["", f"Total LLM spend: ${metrics['total_cost_usd']:.4f}", ""]

    if run["gate_failures"]:
        lines += ["### ❌ Gate failed", ""] + [f"- {f}" for f in run["gate_failures"]] + [""]
    elif run["gated"]:
        lines += ["### ✅ Gate passed", ""]
    if run.get("baseline_changes"):
        lines += ["### Changes since baseline", ""] + [f"- {c}" for c in run["baseline_changes"]] + [""]

    lines += [
        "### Per scenario",
        "",
        "| Scenario | Stage | Fixable | Outcome | Diagnosis | Patch applies | Cost | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        applies = "–" if not r["patch_proposed"] else ("yes" if r["patch_applies"] else "no")
        note = (r["detail"] or "").replace("|", "\\|").replace("\n", " ")[:140]
        scenario = r["id"] if run["trials"] == 1 else f"{r['id']} #{r['trial']}"
        lines.append(
            f"| {scenario} | {r['stage']} | {'yes' if r['fixable'] else 'no'} | "
            f"{ICON.get(r['outcome'], '')} {r['outcome']} | "
            f"{'correct' if r['diagnosis_correct'] else 'wrong'} | {applies} | "
            f"${r['cost_usd']:.4f} | {note} |"
        )
    return "\n".join(lines) + "\n"
