"""Core of the failure-agent eval harness.

For each scenario: copy the repo into a sandbox, seed a known failure, hand
the failure log to the same agent class the pipeline uses, then score what
comes back with deterministic checks only -- did the diagnosis name the real
cause, does the patch apply, and does the broken stage pass afterwards.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from evals.clients import AGENT_ERROR_PREFIX, load_agent_classes, prompt_fingerprint

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "logs", "results", "recordings"}
SKIP_SUFFIXES = {".pdf", ".pptx", ".pyc", ".tar"}
COMMAND_TIMEOUT_S = 180

# Outcomes, from best to worst for a pipeline that opens PRs automatically.
VERIFIED_FIX = "verified_fix"        # patch applied and the stage passes again
CORRECT_ABSTAIN = "correct_abstain"  # nothing to fix in the repo, and no patch offered
MISSED = "missed"                    # fixable, but the agent offered no patch
UNAPPLIABLE = "unappliable"          # patch offered but `git apply` rejects it (no PR opened)
WRONG_FIX = "wrong_fix"              # patch applies but the stage still fails, or it games the check
FALSE_FIX = "false_fix"              # patch applies for a failure no repo change can fix
AGENT_ERROR = "agent_error"          # the model call itself failed
INVALID = "invalid_scenario"         # the scenario no longer matches the repo
HARMFUL = {WRONG_FIX, FALSE_FIX}     # these would have opened a bad auto-fix PR


def _read_text(path: Path) -> str:
    """Read as LF text so Windows checkouts (CRLF) behave like CI."""
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


@dataclass
class Scenario:
    id: str
    stage: str                      # build | test | deploy
    title: str
    fixable: bool
    log: str
    reference_diagnosis: str
    diagnosis_keywords: list        # list of groups; every group needs one hit
    break_patch: str = ""
    expected_files: list = field(default_factory=list)
    protected_paths: list = field(default_factory=list)
    verify: list = field(default_factory=list)   # commands that must exit 0 once fixed
    log_source: str = ""

    @classmethod
    def load(cls, directory: Path) -> "Scenario":
        meta = json.loads(_read_text(directory / "scenario.json"))
        patch_file = directory / "break.patch"
        return cls(
            id=directory.name,
            log=_read_text(directory / "failure_log.txt"),
            break_patch=_read_text(patch_file) if patch_file.exists() else "",
            **meta,
        )


def load_scenarios(ids=None, stage=None) -> list:
    scenarios = [Scenario.load(d) for d in sorted(SCENARIO_DIR.iterdir()) if (d / "scenario.json").exists()]
    if ids:
        unknown = set(ids) - {s.id for s in scenarios}
        if unknown:
            raise SystemExit(f"Unknown scenario id(s): {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.id in ids]
    if stage:
        scenarios = [s for s in scenarios if s.stage == stage]
    return scenarios


@dataclass
class ScenarioResult:
    id: str
    stage: str
    fixable: bool
    trial: int = 1
    outcome: str = ""
    diagnosis_correct: bool = False
    patch_proposed: bool = False
    patch_applies: bool = False
    localized: bool = False
    protected_violation: bool = False
    verified: bool = False
    touched_files: list = field(default_factory=list)
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    root_cause: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------- sandbox

def make_sandbox(repo_root: Path = REPO_ROOT) -> Path:
    """Copy the working tree to a temp dir, normalising line endings."""
    sandbox = Path(tempfile.mkdtemp(prefix="agent-eval-"))
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            src = Path(root) / name
            if src.suffix.lower() in SKIP_SUFFIXES:
                continue
            dst = sandbox / src.relative_to(repo_root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = src.read_bytes()
            if b"\0" not in data:
                data = data.replace(b"\r\n", b"\n")
            dst.write_bytes(data)
    return sandbox


def git_apply(sandbox: Path, patch_text: str) -> tuple:
    """Same check the pipeline's git_ops.try_apply_fix uses: --check, then apply."""
    fd, patch_path = tempfile.mkstemp(suffix=".patch")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")
        for cmd in (["git", "apply", "--check", patch_path], ["git", "apply", patch_path]):
            proc = subprocess.run(cmd, cwd=sandbox, capture_output=True, text=True)
            if proc.returncode != 0:
                return False, proc.stderr.strip()[:400]
        return True, ""
    finally:
        os.unlink(patch_path)


def run_commands(sandbox: Path, commands: list) -> tuple:
    """Run every verify command in the sandbox; True only if all exit 0."""
    env = dict(os.environ, PYTHONPATH=str(sandbox), PYTHONDONTWRITEBYTECODE="1")
    for command in commands:
        argv = [sys.executable if part == "{python}" else part for part in command]
        try:
            proc = subprocess.run(argv, cwd=sandbox, env=env, capture_output=True, text=True,
                                  timeout=COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return False, f"timed out: {' '.join(command)}"
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
            return False, f"{' '.join(command)} -> exit {proc.returncode}: {' | '.join(tail)}"[:400]
    return True, ""


def touched_files(patch_text: str) -> list:
    files = []
    for line in patch_text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            path = line[4:].split("\t")[0].strip()
            if path == "/dev/null":
                continue
            if path.startswith(("a/", "b/")):
                path = path[2:]
            if path not in files:
                files.append(path)
    return files


def diagnosis_matches(scenario: Scenario, text: str) -> bool:
    text = text.lower()
    return all(any(k.lower() in text for k in group) for group in scenario.diagnosis_keywords)


@contextlib.contextmanager
def _cwd(path: Path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


# ---------------------------------------------------------------- runner

def preflight(scenarios: list) -> dict:
    """Run every distinct verify command on the untouched repo. If it fails
    there (a missing dependency, say), a failed verify later would say nothing
    about the agent's patch, so those scenarios must not be scored.
    Returns {scenario_id: error}."""
    sandbox = make_sandbox()
    try:
        errors, cache = {}, {}
        for scenario in scenarios:
            key = json.dumps(scenario.verify)
            if key not in cache:
                cache[key] = run_commands(sandbox, scenario.verify)
            passes, error = cache[key]
            if not passes:
                errors[scenario.id] = f"verify fails on the unmodified repo (missing dependency?): {error}"
        return errors
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def run_scenario(scenario: Scenario, make_client, trial: int = 1, env_error: str = "") -> ScenarioResult:
    """`make_client(cost_tracker)` returns an object with the GeminiClient.run signature."""
    from common.cost_tracker import CostTracker

    result = ScenarioResult(id=scenario.id, stage=scenario.stage, fixable=scenario.fixable, trial=trial)
    if env_error:
        result.outcome, result.detail = INVALID, env_error
        return result
    sandbox = make_sandbox()
    try:
        # 1. Seed the failure, and prove the scenario is still a real failure.
        if scenario.break_patch:
            ok, err = git_apply(sandbox, scenario.break_patch)
            if not ok:
                result.outcome, result.detail = INVALID, f"break.patch no longer applies: {err}"
                return result
        if scenario.fixable:
            passes, _ = run_commands(sandbox, scenario.verify)
            if passes:
                result.outcome, result.detail = INVALID, "verify passes on the broken tree"
                return result

        # 2. Run the real agent class against the failure log.
        tracker = CostTracker()
        client = make_client(tracker)
        agent = load_agent_classes()[scenario.stage](client)
        client.context = {
            "scenario": scenario,
            "trial": trial,
            "fingerprint": prompt_fingerprint(agent),
        }
        started = time.perf_counter()
        with _cwd(sandbox):  # BuildFailureAgent reads ./Dockerfile
            analysis = agent.run(scenario.log)
        result.latency_s = round(time.perf_counter() - started, 3)
        result.cost_usd = round(tracker.total_cost, 6)
        result.input_tokens = sum(e.input_tokens for e in tracker.entries)
        result.output_tokens = sum(e.output_tokens for e in tracker.entries)
        result.root_cause = analysis.root_cause[:500]

        if str(getattr(agent, "raw_response", "")).startswith(AGENT_ERROR_PREFIX):
            result.outcome, result.detail = AGENT_ERROR, agent.raw_response[:300]
            return result

        # 3. Score it.
        result.diagnosis_correct = diagnosis_matches(
            scenario, f"{analysis.root_cause} {analysis.fix_explanation}")
        result.patch_proposed = bool(analysis.patch)
        if not analysis.patch:
            result.outcome = MISSED if scenario.fixable else CORRECT_ABSTAIN
            return result

        result.touched_files = touched_files(analysis.patch)
        result.localized = any(f in scenario.expected_files for f in result.touched_files)
        result.protected_violation = any(
            f.startswith(tuple(scenario.protected_paths)) for f in result.touched_files
        ) if scenario.protected_paths else False

        result.patch_applies, apply_error = git_apply(sandbox, analysis.patch)
        if not result.patch_applies:
            result.outcome, result.detail = UNAPPLIABLE, apply_error
            return result
        if not scenario.fixable:
            result.outcome = FALSE_FIX
            result.detail = "patched the repo for a failure that is not caused by the repo"
            return result
        if result.protected_violation:
            result.outcome = WRONG_FIX
            result.detail = "patch edits protected paths (e.g. changed the test instead of the bug)"
            return result

        result.verified, verify_error = run_commands(sandbox, scenario.verify)
        result.outcome = VERIFIED_FIX if result.verified else WRONG_FIX
        result.detail = verify_error
        return result
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def run_suite(scenarios: list, make_client, trials: int = 1, on_result=None) -> list:
    results = []
    env_errors = preflight(scenarios)
    for scenario in scenarios:
        for trial in range(1, trials + 1):
            result = run_scenario(scenario, make_client, trial, env_errors.get(scenario.id, ""))
            results.append(result)
            if on_result:
                on_result(result)
    return results
