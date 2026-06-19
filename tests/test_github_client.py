"""Tests for the GitHub fake seam: issue idempotency and comments."""

from devin_driver.github_client import FakeGitHubClient


def test_ensure_issue_is_idempotent_per_key():
    gh = FakeGitHubClient()
    first = gh.ensure_issue("devin-driver:sqlite", "Verify sqlite", "body")
    again = gh.ensure_issue("devin-driver:sqlite", "Verify sqlite", "body")

    # Same key -> same issue, no duplicate filed.
    assert again.number == first.number
    assert again is first or again.number == first.number


def test_ensure_issue_distinct_keys_get_distinct_issues():
    gh = FakeGitHubClient()
    a = gh.ensure_issue("devin-driver:sqlite", "t", "b")
    b = gh.ensure_issue("devin-driver:postgresql", "t", "b")
    assert a.number != b.number


def test_comment_is_recorded():
    gh = FakeGitHubClient()
    issue = gh.ensure_issue("devin-driver:sqlite", "t", "b")
    gh.comment(issue.number, "hello")
    gh.comment(issue.number, "world")
    assert gh.comments[issue.number] == ["hello", "world"]
