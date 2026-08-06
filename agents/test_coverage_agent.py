from agents.base_agent import BaseAgent


class TestCoverageAgent(BaseAgent):
    name = "Test Coverage"
    system_prompt = (
        "You are a test engineer reviewing a pull request diff. Identify new "
        "or changed logic that appears to lack corresponding test coverage in "
        "the diff. Suggest concrete test cases (happy path + edge cases) for "
        "any untested logic you find. FAIL if significant new logic has no "
        "tests at all, WARN if coverage looks partial, PASS if changes are "
        "adequately tested or are non-logic changes (docs, config, etc.)."
    )
