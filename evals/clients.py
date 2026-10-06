"""Model clients the harness can plug into the agents.

Every client exposes the same `run(system_prompt, user_content, agent_name)`
method as common.gemini_client.GeminiClient, so the agents run unmodified.

  live     the real GeminiClient (needs GEMINI_API_KEY)
  oracle   answers with each scenario's known fix -- the ceiling; proves the
           scenarios and the scoring are sound (must score 100%)
  null     never proposes a patch -- the floor
  replay   re-serves responses saved by an earlier `--record` run, for free
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import types
from pathlib import Path

AGENT_ERROR_PREFIX = "_Agent call failed"
RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"


def load_agent_classes() -> dict:
    """Import the pipeline's agents, even where the Gemini SDK isn't installed
    (oracle/null/replay runs never call it)."""
    try:
        import google.generativeai  # noqa: F401
    except ImportError:
        google = sys.modules.setdefault("google", types.ModuleType("google"))
        stub = types.ModuleType("google.generativeai")
        google.generativeai = stub
        sys.modules["google.generativeai"] = stub
    from agents.build_failure_agent import BuildFailureAgent
    from agents.deployment_failure_agent import DeploymentFailureAgent
    from agents.test_failure_agent import TestFailureAgent

    return {"build": BuildFailureAgent, "test": TestFailureAgent, "deploy": DeploymentFailureAgent}


def prompt_fingerprint(agent) -> str:
    """Changes whenever the agent's system prompt or prompt template changes,
    so a recording made with an old prompt is never silently reused."""
    material = agent.system_prompt + "\0" + agent.build_user_prompt("<LOG>")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def format_response(root_cause: str, fix_explanation: str, patch: str | None) -> str:
    return f"ROOT_CAUSE:\n{root_cause}\nFIX_EXPLANATION:\n{fix_explanation}\nPATCH:\n{patch or 'NONE'}"


def reverse_patch(patch_text: str) -> str:
    """Turn a scenario's break.patch into the patch that undoes it."""
    out = []
    for line in patch_text.splitlines():
        if line.startswith(("diff --git", "index ")):
            continue
        header = re.match(r"^@@ -(\S+) \+(\S+) @@", line)
        if header:
            out.append(f"@@ -{header.group(2)} +{header.group(1)} @@")
        elif line.startswith(("--- ", "+++ ")):
            out.append(line)
        elif line.startswith("+"):
            out.append("-" + line[1:])
        elif line.startswith("-"):
            out.append("+" + line[1:])
        else:
            out.append(line)
    return "\n".join(out) + "\n"


class _BaseClient:
    model_name = "none"

    def __init__(self, cost_tracker=None):
        self.cost_tracker = cost_tracker
        self.context = {}


class OracleClient(_BaseClient):
    model_name = "oracle"

    def run(self, system_prompt, user_content, agent_name="unknown", retries=3):
        scenario = self.context["scenario"]
        patch = reverse_patch(scenario.break_patch) if scenario.fixable else None
        return format_response(scenario.reference_diagnosis, "Reference fix for this scenario.", patch)


class NullClient(_BaseClient):
    model_name = "null"

    def run(self, system_prompt, user_content, agent_name="unknown", retries=3):
        return format_response("Unable to determine the cause from this log.", "", None)


class ScriptedClient(_BaseClient):
    """Fixed response per scenario id. Used by the harness's own unit tests."""
    model_name = "scripted"

    def __init__(self, responses: dict, cost_tracker=None):
        super().__init__(cost_tracker)
        self.responses = responses

    def run(self, system_prompt, user_content, agent_name="unknown", retries=3):
        return self.responses[self.context["scenario"].id]


def _recording_path(name: str, context: dict) -> Path:
    return RECORDINGS_DIR / name / f"{context['scenario'].id}.t{context['trial']}.json"


class RecordingClient(_BaseClient):
    """Wraps a live client and saves every response plus its token usage."""

    def __init__(self, inner, name: str, cost_tracker=None):
        super().__init__(cost_tracker)
        self.inner, self.name = inner, name
        self.model_name = getattr(inner, "model_name", "unknown")

    def run(self, system_prompt, user_content, agent_name="unknown", retries=3):
        self.inner.context = self.context
        before = len(self.cost_tracker.entries) if self.cost_tracker else 0
        text = self.inner.run(system_prompt, user_content, agent_name=agent_name, retries=retries)
        if text.startswith(AGENT_ERROR_PREFIX):
            return text  # never save a failed call as if it were an answer
        new = self.cost_tracker.entries[before:] if self.cost_tracker else []
        path = _recording_path(self.name, self.context)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "fingerprint": self.context["fingerprint"],
            "model": self.model_name,
            "input_tokens": sum(e.input_tokens for e in new),
            "output_tokens": sum(e.output_tokens for e in new),
            "response": text,
        }, indent=2), encoding="utf-8")
        return text


class ReplayClient(_BaseClient):
    def __init__(self, name: str, cost_tracker=None):
        super().__init__(cost_tracker)
        self.name = name

    def run(self, system_prompt, user_content, agent_name="unknown", retries=3):
        path = _recording_path(self.name, self.context)
        if not path.exists():
            return f"{AGENT_ERROR_PREFIX}: no recording at {path.name}; re-run with --record {self.name}_"
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["fingerprint"] != self.context["fingerprint"]:
            return (f"{AGENT_ERROR_PREFIX}: recording {path.name} was made with a different prompt; "
                    f"re-run with --record {self.name}_")
        self.model_name = saved["model"]
        if self.cost_tracker is not None:
            self.cost_tracker.record(agent_name, saved["model"], saved["input_tokens"], saved["output_tokens"])
        return saved["response"]


def client_factory(kind: str, record: str | None = None, recordings: str | None = None):
    """Return `make_client(cost_tracker)` for the chosen client kind."""
    if kind == "oracle":
        return OracleClient
    if kind == "null":
        return NullClient
    if kind == "replay":
        if not recordings:
            raise SystemExit("--client replay needs --recordings NAME")
        return lambda tracker: ReplayClient(recordings, tracker)
    if kind == "live":
        def make(tracker):
            from common.gemini_client import GeminiClient
            live = GeminiClient(cost_tracker=tracker)
            return RecordingClient(live, record, tracker) if record else live
        return make
    raise SystemExit(f"Unknown client: {kind}")
