"""The GitHub fake/live seam.

Mirrors devin_client: one interface, two implementations, switched by mode. The
orchestrator files one issue per engine on the fork and comments session status
back, closing the remediation loop. The fake path is deterministic and needs no
credentials or network.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class Issue:
    number: int
    url: str
    title: str


class GitHubClient(abc.ABC):
    """Interface for filing issues and commenting on the fork."""

    @abc.abstractmethod
    def ensure_issue(self, key: str, title: str, body: str) -> Issue:
        """Find-or-create an open issue identified by the unique label `key`.

        Idempotent while the issue is open: a second call with the same key
        reuses the existing open issue rather than filing a duplicate. A closed
        (human-triaged) issue is intentionally not reused.
        """

    @abc.abstractmethod
    def comment(self, issue_number: int, body: str) -> None:
        ...


class FakeGitHubClient(GitHubClient):
    """In-memory simulation. Deterministic, no creds, no network."""

    def __init__(self) -> None:
        self._counter = 0
        self._by_key: dict[str, Issue] = {}
        # issue_number -> list of comment bodies (handy for tests).
        self.comments: dict[int, list[str]] = {}

    def ensure_issue(self, key: str, title: str, body: str) -> Issue:
        if key in self._by_key:
            return self._by_key[key]
        self._counter += 1
        issue = Issue(
            number=self._counter,
            url=f"https://github.com/grace-wj/superset/issues/{self._counter}",
            title=title,
        )
        self._by_key[key] = issue
        self.comments[issue.number] = []
        return issue

    def comment(self, issue_number: int, body: str) -> None:
        self.comments.setdefault(issue_number, []).append(body)


class LiveGitHubClient(GitHubClient):
    """Real GitHub REST client. Wired but unfired — Grace runs the live path."""

    API_BASE = "https://api.github.com"

    def __init__(self, token: str, repo: str) -> None:
        if not token or not repo:
            raise ValueError("GITHUB_TOKEN and GITHUB_REPO are required for live mode")
        self._repo = repo  # "owner/name"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    def ensure_issue(self, key: str, title: str, body: str) -> Issue:
        import requests  # lazy import so the fake path needs no network stack

        # Reuse an existing OPEN issue with this label, if any.
        resp = requests.get(
            f"{self.API_BASE}/repos/{self._repo}/issues",
            params={"labels": key, "state": "open"},
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        existing = resp.json()
        if isinstance(existing, list) and existing:
            found = existing[0]
            return Issue(
                number=found["number"],
                url=found.get("html_url", ""),
                title=found.get("title", title),
            )

        # None open: file a fresh issue. GitHub auto-creates the label.
        resp = requests.post(
            f"{self.API_BASE}/repos/{self._repo}/issues",
            json={"title": title, "body": body, "labels": [key]},
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        created = resp.json()
        return Issue(
            number=created["number"],
            url=created.get("html_url", ""),
            title=created.get("title", title),
        )

    def comment(self, issue_number: int, body: str) -> None:
        import requests

        resp = requests.post(
            f"{self.API_BASE}/repos/{self._repo}/issues/{issue_number}/comments",
            json={"body": body},
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()


def make_github_client(config) -> GitHubClient:
    """Pick the implementation based on mode.

    In live mode, missing GitHub credentials are a hard error — we must never
    silently file fake in-memory issues while spawning real, billed Devin
    sessions (the remediation trail would go nowhere).
    """
    if config.is_live:
        return LiveGitHubClient(config.github_token, config.github_repo)
    return FakeGitHubClient()
