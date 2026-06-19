"""Tests for the orchestrator: terminal paths, the blocked grace window, and the
error-isolation contracts (which the plain fake can't exercise because it never
throws)."""

from devin_driver import orchestrator
from devin_driver.devin_client import FakeDevinClient, Session
from devin_driver.github_client import FakeGitHubClient
from devin_driver.orchestrator import FAILED, NEEDS_ATTENTION, VERIFIED
from devin_driver.scanner import WorkItem


def _items(*engines):
    return [WorkItem(engine=e, grains=["day", "week"]) for e in engines]


def test_verified_path_captures_pr_and_files_and_comments():
    client = FakeDevinClient()
    gh = FakeGitHubClient()
    results = orchestrator.run(client, _items("sqlite"), github=gh, poll_interval=0.0)

    assert len(results) == 1
    r = results[0]
    assert r.status == VERIFIED
    assert r.pull_request_url is not None
    assert r.issue_url is not None
    # Issue was filed and commented on (started + verified).
    assert gh.comments  # at least one issue commented
    bodies = next(iter(gh.comments.values()))
    assert any("started" in b for b in bodies)
    assert any("Verified" in b for b in bodies)


def test_blocked_then_recover_is_managed_with_a_single_nudge():
    client = FakeDevinClient(scenarios={"sqlite": "blocked_then_recover"})
    results = orchestrator.run(
        client, _items("sqlite"), poll_interval=0.0, blocked_patience=12
    )
    r = results[0]
    assert r.status == VERIFIED  # recovered within the grace window
    # Exactly one nudge was sent.
    session_id = next(iter(client._sessions))
    assert len(client._sessions[session_id].messages) == 1


def test_blocked_forever_gives_up_only_after_the_grace_window():
    """Must NOT kill on the next poll after the nudge — only after blocked_patience."""
    client = FakeDevinClient(scenarios={"sqlite": "blocked"})
    results = orchestrator.run(
        client, _items("sqlite"), poll_interval=0.0, blocked_patience=3
    )
    r = results[0]
    assert r.status == NEEDS_ATTENTION
    assert "blocked" in r.note
    # Nudged at poll 1; gave up at poll 1 + 3 = 4. So get_session ran 4 times,
    # i.e. it did NOT give up on poll 2 (the next-poll bug we fixed).
    session_id = next(iter(client._sessions))
    assert client._polls[session_id] == 4


def test_failed_path_is_devin_failure_not_needs_attention():
    client = FakeDevinClient(scenarios={"sqlite": "failed"})
    results = orchestrator.run(client, _items("sqlite"), poll_interval=0.0)
    r = results[0]
    assert r.status == FAILED
    assert "failed" in r.note


# --- Error-isolation contracts (clients that throw) ---


class _NudgeRaisesClient:
    """Wraps a blocked-forever fake; its send_message raises every time."""

    def __init__(self):
        self._inner = FakeDevinClient(scenarios={"sqlite": "blocked"})
        self.send_message_calls = 0

    def create_session(self, *a, **k):
        return self._inner.create_session(*a, **k)

    def get_session(self, session_id):
        return self._inner.get_session(session_id)

    def send_message(self, session_id, message):
        self.send_message_calls += 1
        raise RuntimeError("messages endpoint 500")


def test_nudge_failure_does_not_defeat_the_grace_clock():
    """If the nudge HTTP call raises, the grace clock still starts (set before the
    call), so we nudge exactly once and still time out — not nudge every poll."""
    client = _NudgeRaisesClient()
    results = orchestrator.run(
        client, _items("sqlite"), poll_interval=0.0, blocked_patience=3
    )
    r = results[0]
    assert r.status == NEEDS_ATTENTION
    assert client.send_message_calls == 1  # nudged once despite raising, not every poll


class _PollAlwaysRaisesClient:
    """create_session works; every get_session raises (transient-forever)."""

    def __init__(self):
        self._inner = FakeDevinClient()
        self.get_calls = 0

    def create_session(self, *a, **k):
        return self._inner.create_session(*a, **k)

    def get_session(self, session_id):
        self.get_calls += 1
        raise RuntimeError("poll 503")

    def send_message(self, session_id, message):
        pass


def test_persistent_poll_errors_become_needs_attention_not_failed():
    client = _PollAlwaysRaisesClient()
    results = orchestrator.run(
        client, _items("sqlite"), poll_interval=0.0, max_consecutive_errors=4, max_polls=200
    )
    r = results[0]
    assert r.status == NEEDS_ATTENTION  # NOT FAILED — it's a factory-side loss of contact
    assert "lost contact" in r.note
    # Gave up at the error cap (4 polls), not after riding to max_polls (200).
    assert client.get_calls == 4


def test_comment_failure_does_not_double_record_results():
    """An isolated comment failure must not re-enter the terminal branch."""

    class _CommentRaisesGitHub(FakeGitHubClient):
        def comment(self, issue_number, body):
            raise RuntimeError("comment 429")

    client = FakeDevinClient()
    gh = _CommentRaisesGitHub()
    results = orchestrator.run(client, _items("sqlite", "postgresql"), github=gh, poll_interval=0.0)
    # Exactly one result per engine despite every comment raising.
    assert len(results) == 2
    assert all(r.status == VERIFIED for r in results)
