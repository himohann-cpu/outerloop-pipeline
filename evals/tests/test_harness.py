"""Tests for the harness itself. No model calls: every response is scripted."""
import json
import shutil

import pytest

from evals import harness as h
from evals import report
from evals.clients import (NullClient, RecordingClient, ReplayClient, ScriptedClient,
                           format_response, reverse_patch)

GOOD_FIX = """--- a/app/calculator.py
+++ b/app/calculator.py
@@ -20,2 +20,4 @@
 def divide(a, b):
+    if b == 0:
+        raise ValueError("Cannot divide by zero")
     return a / b
"""
NOT_A_FIX = """--- a/app/calculator.py
+++ b/app/calculator.py
@@ -20,2 +20,2 @@
 def divide(a, b):
-    return a / b
+    return a // b
"""
EDITS_THE_TEST = """--- a/tests/test_calculator.py
+++ b/tests/test_calculator.py
@@ -17,3 +17,3 @@
 def test_divide_by_zero():
-    with pytest.raises(ValueError):
+    with pytest.raises(ZeroDivisionError):
         divide(1, 0)
"""
PATCH_FOR_NOTHING = """--- a/scripts/deploy.sh
+++ b/scripts/deploy.sh
@@ -20,3 +20,3 @@
 echo "Waiting for health check..."
-ATTEMPTS=10
+ATTEMPTS=20
 for i in $(seq 1 "$ATTEMPTS"); do
"""
DIAGNOSIS = "divide() lost its zero check, so ZeroDivisionError escapes instead of ValueError."


def scenario(scenario_id):
    return h.load_scenarios([scenario_id])[0]


def run(scenario_id, patch, root_cause=DIAGNOSIS):
    responses = {scenario_id: format_response(root_cause, "fix", patch)}
    return h.run_scenario(scenario(scenario_id), lambda tracker: ScriptedClient(responses, tracker))


def test_correct_patch_is_a_verified_fix():
    result = run("test-zero-guard-removed", GOOD_FIX)
    assert result.outcome == h.VERIFIED_FIX
    assert result.diagnosis_correct and result.localized and result.verified


def test_patch_that_applies_but_does_not_fix_is_wrong():
    assert run("test-zero-guard-removed", NOT_A_FIX).outcome == h.WRONG_FIX


def test_editing_the_test_to_make_it_pass_is_wrong():
    result = run("test-zero-guard-removed", EDITS_THE_TEST)
    assert result.outcome == h.WRONG_FIX and result.protected_violation


def test_garbage_patch_is_unappliable():
    result = run("test-zero-guard-removed", "this is not a diff")
    assert result.outcome == h.UNAPPLIABLE and not result.patch_applies


def test_no_patch_on_fixable_failure_is_missed_and_wrong_diagnosis_is_scored():
    result = run("test-zero-guard-removed", None, root_cause="The network was down.")
    assert result.outcome == h.MISSED and not result.diagnosis_correct


def test_abstaining_on_an_external_failure_is_correct():
    assert run("deploy-external-target-misconfigured", None).outcome == h.CORRECT_ABSTAIN


def test_patching_the_repo_for_an_external_failure_is_a_false_fix():
    assert run("deploy-external-target-misconfigured", PATCH_FOR_NOTHING).outcome == h.FALSE_FIX


@pytest.mark.parametrize("s", h.load_scenarios(), ids=lambda s: s.id)
def test_every_scenario_still_matches_the_repo(s):
    """break.patch applies, and the verify check fails until something fixes it."""
    assert s.fixable == bool(s.break_patch)
    if not s.break_patch:
        return
    sandbox = h.make_sandbox()
    try:
        assert h.git_apply(sandbox, s.break_patch)[0]
        assert not h.run_commands(sandbox, s.verify)[0]
        # Applied straight to the tree (not through the agent), the reference fix works.
        assert h.git_apply(sandbox, reverse_patch(s.break_patch))[0]
        assert h.run_commands(sandbox, s.verify)[0]
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_metrics_and_gate():
    results = [r.to_dict() for r in (
        run("test-zero-guard-removed", GOOD_FIX),
        run("test-zero-guard-removed", NOT_A_FIX),
        run("deploy-external-target-misconfigured", None),
    )]
    metrics = report.compute_metrics(results)
    assert metrics["verified_fix_rate"] == 0.5
    assert metrics["pr_precision"] == 0.5
    assert metrics["abstention_accuracy"] == 1.0
    assert metrics["harmful_pr_rate"] == round(1 / 3, 4)
    failures = report.check_thresholds(metrics, {"harmful_pr_rate": {"max": 0.0}})
    assert len(failures) == 1 and "harmful_pr_rate" in failures[0]
    assert report.check_thresholds(metrics, {"verified_fix_rate": {"min": 0.5}}) == []


def test_record_then_replay_and_stale_recordings_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("evals.clients.RECORDINGS_DIR", tmp_path)
    sid = "test-zero-guard-removed"
    responses = {sid: format_response(DIAGNOSIS, "fix", GOOD_FIX)}

    def recording(tracker):
        return RecordingClient(ScriptedClient(responses, tracker), "v1", tracker)

    assert h.run_scenario(scenario(sid), recording).outcome == h.VERIFIED_FIX
    assert h.run_scenario(scenario(sid), lambda t: ReplayClient("v1", t)).outcome == h.VERIFIED_FIX

    saved = tmp_path / "v1" / f"{sid}.t1.json"
    data = json.loads(saved.read_text())
    data["fingerprint"] = "made-with-an-older-prompt"
    saved.write_text(json.dumps(data))
    assert h.run_scenario(scenario(sid), lambda t: ReplayClient("v1", t)).outcome == h.AGENT_ERROR


def test_null_client_never_opens_a_harmful_pr():
    results = [r.to_dict() for r in h.run_suite(h.load_scenarios(), NullClient)]
    assert report.compute_metrics(results)["harmful_pr_rate"] == 0.0


def test_broken_environment_is_reported_as_invalid_not_as_a_wrong_fix(monkeypatch):
    """If verify cannot pass even on a clean repo, the agent must not be blamed."""
    s = scenario("test-zero-guard-removed")
    s.verify = [["{python}", "-c", "import a_module_that_is_not_installed"]]
    errors = h.preflight([s])
    assert s.id in errors
    result = h.run_scenario(s, NullClient, env_error=errors[s.id])
    assert result.outcome == h.INVALID
