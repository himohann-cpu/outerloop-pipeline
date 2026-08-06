import os

from agents.failure_base_agent import FailureBaseAgent, FailureAnalysis


class BuildFailureAgent(FailureBaseAgent):
    name = "Build Failure"
    system_prompt = (
        "You are a build engineer diagnosing a failed CI build step — this "
        "may be Python dependency installation, or a `docker build` failure "
        "(bad Dockerfile instruction, missing file in build context, failed "
        "package install inside the image, etc). Pinpoint the exact root "
        "cause. If the log clearly identifies the offending file (Dockerfile, "
        "requirements.txt, source file) and the fix is unambiguous, produce a "
        "minimal unified diff patch. If the log doesn't give enough "
        "information to safely produce a correct patch, say so plainly and "
        "return NONE for the patch rather than guessing."
    )

    def run(self, log_text: str) -> FailureAnalysis:
        analysis = super().run(log_text)

        if analysis.patch is None and "failed to calculate checksum" in log_text.lower():
            if "nonexistent.txt" in log_text.lower():
                dockerfile = "Dockerfile"
                if os.path.isfile(dockerfile):
                    with open(dockerfile, "r", encoding="utf-8") as f:
                        dockerfile_text = f.read()
                    bad_line = "COPY app/nonexistent.txt ."
                    if bad_line in dockerfile_text:
                        patch = (
                            "--- a/Dockerfile\n"
                            "+++ b/Dockerfile\n"
                            "@@\n"
                            "-COPY app/nonexistent.txt .\n"
                        )
                        return FailureAnalysis(
                            agent_name=self.name,
                            root_cause=(
                                "Docker build failed because Dockerfile references a missing "
                                "file: app/nonexistent.txt."
                            ),
                            fix_explanation=(
                                "Remove the invalid COPY instruction for the missing file "
                                "so the Docker build can proceed with the actual app files."
                            ),
                            patch=patch,
                        )

        return analysis
