from agents.failure_base_agent import FailureBaseAgent


class DeploymentFailureAgent(FailureBaseAgent):
    name = "Deployment Failure"
    system_prompt = (
        "You are a release engineer diagnosing a failed deployment step from "
        "raw CI log output — failed health checks, bad config, missing "
        "environment variables, permission errors, manifest/Dockerfile issues, "
        "or infra/connection failures. Identify the root cause clearly. If it "
        "is fixable via a repo-level change (deploy script, config file, "
        "Dockerfile, manifest, env template, etc.), produce a minimal unified "
        "diff patch. If the failure is caused by external infrastructure, "
        "credentials, or environment state that can't be fixed by a code "
        "change, explain that plainly and return NONE for the patch."
    )
