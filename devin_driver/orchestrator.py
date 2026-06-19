"""The orchestrator: initiate AND manage one Devin session per engine, and
close the loop back to the fork.

For each work item it files an issue (if a GitHub client is given), spawns a
Devin session, polls status_enum until the session reaches a terminal state,
captures the PR url, manages blocked/failed sessions, and comments status back
on the issue. Fire-and-poll — we never block on a single session.

Resilience (layer 1): every live call (the Devin poll, the nudge, every GitHub
call) is isolated so one transient HTTP error logs and continues rather than
aborting the whole in-flight batch. Local state is always mutated BEFORE any
side-effecting GitHub comment, so an isolated comment failure can never cause a
session to be re-processed (no double-comment / double-Result).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import prompts
from .devin_client import (
    BLOCKED,
    FINISHED,
    TERMINAL_FAILURE,
    DevinClient,
    Session,
)
from .github_client import GitHubClient, Issue
from .scanner import WorkItem

# Result statuses.
VERIFIED = "verified"  # Devin finished; PR captured.
FAILED = "failed"  # Devin itself reached a terminal-failure status.
NEEDS_ATTENTION = "needs_attention"  # factory-side: timed out / lost contact / stuck blocked.


@dataclass
class Result:
    engine: str
    grains: list[str]
    status: str  # VERIFIED | FAILED | NEEDS_ATTENTION
    session_url: str
    pull_request_url: str | None = None
    issue_url: str | None = None
    note: str = ""  # why a FAILED/NEEDS_ATTENTION result ended that way
    # Per-grain outcome: grain -> "verified" | "discrepancy" | "unknown".
    grain_results: dict[str, str] = field(default_factory=dict)
    # Redundancy findings: pairs of grain expressions that produced identical
    # buckets, e.g. [{"grain_a", "grain_b", "note"}]. (Devin only; empty in fake.)
    redundancies: list[dict] = field(default_factory=list)

    @property
    def discrepancies(self) -> list[str]:
        return [g for g, s in self.grain_results.items() if s == "discrepancy"]


def _grain_results(work_item: WorkItem, session: Session) -> dict[str, str]:
    """Derive per-grain results from the session's structured output.

    Live sessions return per-grain verdicts; the fake path returns none, so
    every grain is reported as "unknown" (plumbing proven, no verdict claimed).
    Devin keys its grains uppercase ("WEEK"); our work items are lowercase, so
    normalize before lookup or every live verdict is silently dropped.
    """
    reported = (session.structured_output or {}).get("grains", {})
    reported = {k.upper(): v for k, v in reported.items()}
    results: dict[str, str] = {}
    for grain in work_item.grains:
        entry = reported.get(grain.upper())
        if entry is None:
            results[grain] = "unknown"  # never claim a verdict we didn't get
        else:
            results[grain] = entry.get("status", "unknown")
    return results


def _issue_title(engine: str) -> str:
    return f"Verify time-grain SQL: {engine}"


def _issue_body(engine: str, grains: list[str]) -> str:
    # A work-order only — task, not finding. Never hint at a discrepancy here
    # (Tombstone #3/#4): findings must come from Devin's live run, not the issue.
    return (
        f"Verify Apache Superset's time-grain SQL for the `{engine}` engine spec "
        f"against a live database.\n\n"
        f"Grains to check: {', '.join(grains)}.\n\n"
        f"Run each grain's SQL over a battery of real timestamps, compare every "
        f"bucket to an independent oracle, and report a correctness matrix. "
        f"Surface any divergence for review.\n"
    )


def _safe_comment(github: GitHubClient | None, issue: Issue | None, body: str) -> None:
    """Comment on the issue, isolating any failure (log and continue)."""
    if github is None or issue is None:
        return
    try:
        github.comment(issue.number, body)
    except Exception as exc:  # noqa: BLE001 - isolation is the point
        print(f"  WARN: failed to comment on issue #{issue.number}: {exc}")


def run(
    client: DevinClient,
    work_items: list[WorkItem],
    github: GitHubClient | None = None,
    run_id: str | None = None,
    poll_interval: float = 0.0,
    max_polls: int = 200,
    blocked_patience: int = 12,
    max_consecutive_errors: int = 4,
) -> list[Result]:
    """Drive every work item to a terminal state and return the results.

    blocked_patience is how many poll cycles to wait after nudging a blocked
    session before giving up. At the live 15s interval the default of 12 gives a
    session ~3 minutes to react to a nudge (it needs minutes, not one poll).

    max_consecutive_errors bounds how many back-to-back poll failures a session
    tolerates before we stop waiting on it and mark it NEEDS_ATTENTION (rather
    than burning to max_polls and mislabeling it as a Devin FAILED).
    """
    sessions: dict[str, Session] = {}
    pending: dict[str, WorkItem] = {}
    issues: dict[str, Issue | None] = {}  # session_id -> filed issue (or None)

    # Spawn one session per engine, filing an issue first and announcing the run.
    for item in work_items:
        issue: Issue | None = None
        if github is not None:
            try:
                issue = github.ensure_issue(
                    f"devin-driver:{item.engine}",
                    _issue_title(item.engine),
                    _issue_body(item.engine, item.grains),
                )
            except Exception as exc:  # noqa: BLE001 - isolation
                print(f"  WARN: failed to file issue for {item.engine}: {exc}")

        # Idempotency OFF: Devin keys idempotency on the PROMPT, which is identical
        # every run, so idempotent=True would re-attach to a prior (even terminated)
        # session. We always want fresh sessions per run. The run_id rides along as
        # a tag purely as a label, so you can find a run's sessions in the Devin UI.
        tags = [f"engine:{item.engine}", "devin-driver"]
        if run_id:
            tags.append(f"run:{run_id}")
        session = client.create_session(
            prompt=prompts.build_prompt(item.engine, item.grains),
            tags=tags,
            idempotent=False,
            structured_output_schema=prompts.STRUCTURED_OUTPUT_SCHEMA,
        )
        sessions[session.session_id] = session
        pending[session.session_id] = item
        issues[session.session_id] = issue
        print(f"  launched {item.engine:12s} -> {session.url}")
        _safe_comment(github, issue, f"Devin session started: {session.url}")

    results: list[Result] = []
    nudged_at: dict[str, int] = {}  # session_id -> poll when nudged
    errors: dict[str, int] = {}  # session_id -> consecutive poll errors

    def finalize(session_id: str, result: Result, comment: str) -> None:
        # Mutate local state FIRST, then attempt the (isolated) side effect, so a
        # comment failure can never re-enter this branch and double-record.
        result.issue_url = issues[session_id].url if issues[session_id] else None
        results.append(result)
        del pending[session_id]
        _safe_comment(github, issues[session_id], comment)

    # Poll until everything is terminal (or we hit the safety cap).
    polls = 0
    while pending and polls < max_polls:
        if poll_interval:
            time.sleep(poll_interval)
        polls += 1

        for session_id in list(pending):
            item = pending[session_id]

            try:
                session = client.get_session(session_id)
            except Exception as exc:  # noqa: BLE001 - isolation
                errors[session_id] = errors.get(session_id, 0) + 1
                print(
                    f"  WARN: poll error for {item.engine} "
                    f"({errors[session_id]}/{max_consecutive_errors}): {exc}"
                )
                if errors[session_id] >= max_consecutive_errors:
                    finalize(
                        session_id,
                        Result(
                            engine=item.engine,
                            grains=item.grains,
                            status=NEEDS_ATTENTION,
                            session_url=sessions[session_id].url,
                            note=f"lost contact after {errors[session_id]} poll errors",
                            grain_results={g: "unknown" for g in item.grains},
                        ),
                        "Factory lost contact with this session (repeated poll "
                        "errors) — needs attention.",
                    )
                continue  # transient: try again next poll
            errors[session_id] = 0  # poll succeeded; reset the error streak

            status = session.status_enum

            # A real session opens its PR and writes verdicts, then parks in
            # `blocked` ("awaiting instructions") before it ever reaches
            # `finished`. Capture that delivered work regardless of status —
            # but require grains (real verdicts), so a still-working draft PR
            # with no verdicts is not finalized early and no matrix is faked.
            delivered = bool(session.pull_request_url) and bool(
                (session.structured_output or {}).get("grains")
            )

            if status == FINISHED or delivered:
                result = Result(
                    engine=item.engine,
                    grains=item.grains,
                    status=VERIFIED,
                    session_url=session.url,
                    pull_request_url=session.pull_request_url,
                    grain_results=_grain_results(item, session),
                    redundancies=list((session.structured_output or {}).get("redundancies") or []),
                )
                pr = session.pull_request_url or "(no PR)"
                finalize(session_id, result, f"Verified — PR: {pr}")
            elif status in TERMINAL_FAILURE:
                finalize(
                    session_id,
                    _failed_result(item, session, note=f"Devin status: {status}"),
                    f"Devin reported failure (status: {status}) — needs attention.",
                )
            elif status == BLOCKED:
                if session_id not in nudged_at:
                    # Start the grace clock when we DECIDE to nudge — before the
                    # call — so a flaky messages endpoint can't defeat the timer.
                    nudged_at[session_id] = polls
                    print(f"  nudging blocked session for {item.engine}")
                    try:
                        client.send_message(
                            session_id,
                            "You appear blocked. Please proceed using SQLite if an "
                            "engine cannot be installed, and report what you have.",
                        )
                    except Exception as exc:  # noqa: BLE001 - isolation
                        print(f"  WARN: nudge failed for {item.engine}: {exc}")
                elif polls - nudged_at[session_id] >= blocked_patience:
                    print(f"  giving up on still-blocked {item.engine}")
                    finalize(
                        session_id,
                        Result(
                            engine=item.engine,
                            grains=item.grains,
                            status=NEEDS_ATTENTION,
                            session_url=session.url,
                            pull_request_url=session.pull_request_url,
                            note="still blocked after nudge + grace window",
                            grain_results={g: "unknown" for g in item.grains},
                        ),
                        "Session still blocked after a nudge and grace window — "
                        "needs attention.",
                    )
                # else: still within the grace window — keep polling.

    # Anything still pending hit the poll cap — it timed out, which is NOT the
    # same as Devin failing verification.
    for session_id in list(pending):
        item = pending[session_id]
        finalize(
            session_id,
            Result(
                engine=item.engine,
                grains=item.grains,
                status=NEEDS_ATTENTION,
                session_url=sessions[session_id].url,
                note=f"did not reach a terminal state within {max_polls} polls",
                grain_results={g: "unknown" for g in item.grains},
            ),
            f"Session did not finish within {max_polls} polls — needs attention.",
        )

    return results


def _failed_result(item: WorkItem, session: Session, note: str = "") -> Result:
    return Result(
        engine=item.engine,
        grains=item.grains,
        status=FAILED,
        session_url=session.url,
        pull_request_url=None,
        note=note,
        grain_results={grain: "unknown" for grain in item.grains},
    )
