"""Cost-impact review agent.

Analyzes a PR diff for cloud/infra cost implications — new or upsized
resources, heavy new dependencies, unbounded loops/fetches that scale with
traffic, etc. Runs on GCP Gemini Apps through the shared Gemini client.
"""

from agents.base_agent import BaseAgent
from common.gemini_client import GeminiClient

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


class CostImpactAgent(BaseAgent):
    name = "Cost Impact"
    system_prompt = SYSTEM_PROMPT

    def __init__(self, client=None, cost_tracker=None, model=None):
        if client is None:
            client = GeminiClient(cost_tracker=cost_tracker, model=model)
        super().__init__(client)
