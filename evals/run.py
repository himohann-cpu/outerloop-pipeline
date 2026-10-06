"""CLI for the failure-agent eval harness.

    python -m evals.run --client oracle             # free: proves the scenarios are sound
    python -m evals.run --client live --record v1   # real model; saves responses
    python -m evals.run --client replay --recordings v1   # free re-score of a saved run
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent))  # so `agents` and `common` import from any cwd

from evals import harness, report  # noqa: E402
from evals.clients import client_factory  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", choices=["live", "oracle", "null", "replay"], default="oracle")
    parser.add_argument("--record", metavar="NAME", help="with --client live: save responses under evals/recordings/NAME")
    parser.add_argument("--recordings", metavar="NAME", help="with --client replay: which saved run to replay")
    parser.add_argument("--scenario", action="append", metavar="ID", help="run only this scenario (repeatable)")
    parser.add_argument("--stage", choices=["build", "test", "deploy"])
    parser.add_argument("--trials", type=int, default=1, help="runs per scenario, to see run-to-run variance")
    parser.add_argument("--thresholds", default=str(EVALS_DIR / "thresholds.json"))
    parser.add_argument("--no-gate", action="store_true", help="report only; never exit non-zero on thresholds")
    parser.add_argument("--baseline", metavar="RESULTS_JSON", help="earlier results.json to diff outcomes against")
    parser.add_argument("--out", default=str(EVALS_DIR / "results" / "latest"))
    args = parser.parse_args(argv)

    scenarios = harness.load_scenarios(args.scenario, args.stage)
    if not scenarios:
        raise SystemExit("No scenarios matched.")
    make_client = client_factory(args.client, record=args.record, recordings=args.recordings)

    def progress(r):
        print(f"  {r.id:<36} {r.outcome:<16} diagnosis={'ok' if r.diagnosis_correct else 'no':<3} {r.detail[:70]}")

    print(f"Running {len(scenarios)} scenario(s) x {args.trials} trial(s) with client '{args.client}'")
    results = [r.to_dict() for r in harness.run_suite(scenarios, make_client, args.trials, progress)]
    metrics = report.compute_metrics(results)

    gated = not args.no_gate
    gate_failures = []
    if gated:
        thresholds = json.loads(Path(args.thresholds).read_text(encoding="utf-8"))
        gate_failures = report.check_thresholds(metrics, thresholds)

    run = {
        "client": args.client,
        "model": os.environ.get("GEMINI_MODEL", "gemini-1.5-pro") if args.client == "live" else (args.recordings or args.client),
        "trials": args.trials,
        "started_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "metrics": metrics,
        "gated": gated,
        "gate_failures": gate_failures,
        "results": results,
    }
    if args.baseline:
        run["baseline_changes"] = report.compare_to_baseline(
            results, json.loads(Path(args.baseline).read_text(encoding="utf-8")))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    markdown = report.render_markdown(run)
    (out / "results.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    (out / "report.md").write_text(markdown, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(markdown)

    print()
    for name in report.METRIC_HELP:
        print(f"  {name:<28} {report._fmt(name, metrics[name])}")
    print(f"\nReport: {out / 'report.md'}")
    for failure in gate_failures:
        print(f"GATE FAILED: {failure}")
    return 1 if gate_failures else 0


if __name__ == "__main__":
    sys.exit(main())
