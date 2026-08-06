"""Minimal GitHub REST API helper — fetch a PR diff, post a comment.

Uses only `requests` + the GITHUB_TOKEN GitHub Actions provides automatically,
so no extra secrets are needed for this part.
"""
import os
import requests

API_ROOT = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str | None = None, repo: str | None = None):
        self.token = token or os.environ["GITHUB_TOKEN"]
        self.repo = repo or os.environ["GITHUB_REPOSITORY"]  # "owner/name"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def get_pr_diff(self, pr_number: int) -> str:
        url = f"{API_ROOT}/repos/{self.repo}/pulls/{pr_number}"
        r = self.session.get(url, headers={"Accept": "application/vnd.github.v3.diff"})
        r.raise_for_status()
        return r.text

    def get_changed_files(self, pr_number: int) -> list[str]:
        url = f"{API_ROOT}/repos/{self.repo}/pulls/{pr_number}/files"
        r = self.session.get(url)
        r.raise_for_status()
        return [f["filename"] for f in r.json()]

    def post_pr_comment(self, pr_number: int, body: str) -> None:
        url = f"{API_ROOT}/repos/{self.repo}/issues/{pr_number}/comments"
        r = self.session.post(url, json={"body": body})
        r.raise_for_status()

    def get_default_branch(self) -> str:
        url = f"{API_ROOT}/repos/{self.repo}"
        r = self.session.get(url)
        r.raise_for_status()
        return r.json()["default_branch"]

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> dict:
        url = f"{API_ROOT}/repos/{self.repo}/pulls"
        r = self.session.post(
            url, json={"head": head, "base": base, "title": title, "body": body}
        )
        r.raise_for_status()
        return r.json()

    def post_commit_comment(self, sha: str, body: str) -> None:
        url = f"{API_ROOT}/repos/{self.repo}/commits/{sha}/comments"
        r = self.session.post(url, json={"body": body})
        r.raise_for_status()
