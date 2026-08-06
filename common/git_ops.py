"""Helper for the failure-analysis orchestrator: attempt to turn an agent's
suggested unified diff into a real branch + commit + push, without ever
touching the branch CI is currently running on if the patch is bad.
"""
import os
import subprocess
import tempfile


class GitOpsError(Exception):
    pass


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitOpsError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def try_apply_fix(patch_text: str, branch_name: str, commit_message: str) -> bool:
    """Create `branch_name`, apply `patch_text`, commit, and push it.

    Returns True on success. Returns False (and leaves no trace — branch is
    cleaned up) if the patch doesn't apply cleanly, so a bad AI-generated
    diff never gets force-pushed or half-applied.
    """
    if not patch_text or patch_text.strip().upper() == "NONE":
        return False

    _run(["git", "checkout", "-b", branch_name])

    fd, patch_path = tempfile.mkstemp(suffix=".patch")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")

        try:
            _run(["git", "apply", "--check", patch_path])
            _run(["git", "apply", patch_path])
        except GitOpsError:
            _run(["git", "checkout", "-"])
            _run(["git", "branch", "-D", branch_name])
            return False
    finally:
        os.unlink(patch_path)

    _run(["git", "add", "-A"])
    _run(["git", "commit", "-m", commit_message])
    _run(["git", "push", "-u", "origin", branch_name, "--force"])
    return True
