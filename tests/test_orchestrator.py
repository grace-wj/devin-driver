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


class _StructuredOutputClient:
    """Finishes immediately, returning a canned structured_output (mimics a live
    Devin verdict) so we can prove the harness surfaces discrepancies + redundancies."""

    def __init__(self, structured_output):
        self._so = structured_output

    def create_session(self, *a, **k):
        return Session(session_id="s1", url="u")

    def get_session(self, session_id):
        from devin_driver.devin_client import FINISHED

        return Session(
            session_id=session_id,
            url="u",
            status_enum=FINISHED,
            pull_request_url="pr",
            structured_output=self._so,
        )

    def send_message(self, session_id, message):
        pass


def test_structured_output_surfaces_discrepancy_and_redundancy():
    so = {
        "engine": "sqlite",
        "reference": "ISO-8601: week starts Monday",
        # Devin keys grains UPPERCASE — mirror the real payload so this test
        # actually guards the case-normalization in _grain_results.
        "grains": {
            "DAY": {"status": "verified"},
            "WEEK": {"status": "discrepancy", "note": "engine WEEK starts Sunday"},
        },
        "redundancies": [
            {"grain_a": "WEEK", "grain_b": "WEEK_STARTING_SUNDAY", "note": "identical buckets"}
        ],
    }
    results = orchestrator.run(_StructuredOutputClient(so), _items("sqlite"), poll_interval=0.0)
    r = results[0]
    assert r.status == VERIFIED
    assert r.grain_results["week"] == "discrepancy"  # divergence from ISO reference surfaces
    assert "week" in r.discrepancies
    assert len(r.redundancies) == 1  # redundancy captured structurally, not just in prose


class _ParkedBlockedClient:
    """Mimics the real lifecycle: a session that opens its PR and writes verdicts
    but parks in `blocked` ("awaiting instructions") instead of going `finished`.
    Optionally has no structured_output (a draft PR with no verdicts yet)."""

    def __init__(self, structured_output):
        self._so = structured_output
        self.send_message_calls = 0

    def create_session(self, *a, **k):
        return Session(session_id="s1", url="u")

    def get_session(self, session_id):
        from devin_driver.devin_client import BLOCKED

        return Session(
            session_id=session_id,
            url="u",
            status_enum=BLOCKED,  # never reaches `finished` during the window
            pull_request_url="pr",
            structured_output=self._so,
        )

    def send_message(self, session_id, message):
        self.send_message_calls += 1


def test_parked_blocked_with_pr_and_verdicts_is_verified():
    """The bug that bit the live run: a delivered session parked in `blocked`
    must be captured as VERIFIED with its real verdicts, and never nudged."""
    so = {
        "engine": "sqlite",
        "grains": {
            "DAY": {"status": "verified"},
            "WEEK": {"status": "discrepancy", "note": "Sunday-start"},
        },
    }
    client = _ParkedBlockedClient(so)
    results = orchestrator.run(
        client, _items("sqlite"), poll_interval=0.0, blocked_patience=12
    )
    r = results[0]
    assert r.status == VERIFIED
    assert r.pull_request_url == "pr"
    assert r.grain_results["week"] == "discrepancy"  # the red cell survives
    assert client.send_message_calls == 0  # delivered work is never nudged


def test_blocked_with_pr_but_no_verdicts_is_not_fabricated():
    """A draft PR with no verdicts must NOT be finalized as verified (no faked
    green matrix); it degrades to needs_attention but still surfaces the PR."""
    client = _ParkedBlockedClient(structured_output=None)
    results = orchestrator.run(
        client, _items("sqlite"), poll_interval=0.0, blocked_patience=3
    )
    r = results[0]
    assert r.status == NEEDS_ATTENTION
    assert r.status != VERIFIED
    assert r.pull_request_url == "pr"  # PR surfaced, not silently lost


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
