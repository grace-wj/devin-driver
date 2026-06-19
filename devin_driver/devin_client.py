"""The fake/live seam.

Both clients implement the same DevinClient interface, so the orchestrator code
path is byte-for-byte identical regardless of mode. The fake path simulates the
session lifecycle deterministically (seconds, free) and validates plumbing only;
it says nothing about prompt quality, which is live-only.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

# status_enum values we care about. Anything in TERMINAL_FAILURE ends a session
# unsuccessfully; FINISHED ends it successfully; BLOCKED needs a nudge.
WORKING = "working"
BLOCKED = "blocked"
FINISHED = "finished"
TERMINAL_FAILURE = frozenset({"expired", "stopped", "failed"})


@dataclass
class Session:
    session_id: str
    url: str
    status_enum: str = WORKING
    pull_request_url: str | None = None
    structured_output: dict | None = None
    messages: list[str] = field(default_factory=list)


class DevinClient(abc.ABC):
    """Interface for driving Devin sessions."""

    @abc.abstractmethod
    def create_session(
        self,
        prompt: str,
        tags: list[str] | None = None,
        idempotent: bool = True,
        structured_output_schema: dict | None = None,
    ) -> Session:
        ...

    @abc.abstractmethod
    def get_session(self, session_id: str) -> Session:
        ...

    @abc.abstractmethod
    def send_message(self, session_id: str, message: str) -> None:
        ...


class FakeDevinClient(DevinClient):
    """Deterministic simulation of the session lifecycle. No network, no key.

    By default each session reports `working` for a couple of polls, then
    `finished` with a canned PR url — exercising the full spawn → poll → capture
    path without fabricating any verification finding (the matrix stays
    all-verified, M=0).

    Pass `scenarios={engine: behavior}` (engine read from the `engine:` tag) to
    simulate the management paths for tests:
      - "finish"               (default) — works briefly, then finishes with a PR.
      - "blocked_then_recover" — stays `blocked` until BLOCK_RECOVER_POLLS polls
                                 after a nudge (send_message), then finishes.
      - "blocked"              — stays `blocked` forever (never recovers).
      - "failed"               — reports a terminal-failure status.
    Scenario branches fully override the default lifecycle.
    """

    POLLS_TO_FINISH = 2
    BLOCK_RECOVER_POLLS = 2  # polls after a nudge before a blocked session recovers

    def __init__(self, scenarios: dict[str, str] | None = None) -> None:
        self._counter = 0
        self._polls: dict[str, int] = {}
        self._sessions: dict[str, Session] = {}
        self._scenario: dict[str, str] = {}  # session_id -> behavior
        self._nudged_poll: dict[str, int] = {}  # session_id -> poll when nudged
        self._scenarios_by_engine = scenarios or {}

    @staticmethod
    def _engine_from_tags(tags: list[str] | None) -> str | None:
        for tag in tags or []:
            if tag.startswith("engine:"):
                return tag.split(":", 1)[1]
        return None

    def create_session(
        self,
        prompt: str,
        tags: list[str] | None = None,
        idempotent: bool = True,
        structured_output_schema: dict | None = None,
    ) -> Session:
        self._counter += 1
        session_id = f"fake-{self._counter}"
        session = Session(
            session_id=session_id,
            url=f"https://app.devin.ai/sessions/{session_id}",
        )
        self._sessions[session_id] = session
        self._polls[session_id] = 0
        engine = self._engine_from_tags(tags)
        self._scenario[session_id] = self._scenarios_by_engine.get(engine, "finish")
        return session

    def _finish(self, session: Session, session_id: str) -> Session:
        number = session_id.rsplit("-", 1)[-1]
        session.status_enum = FINISHED
        session.pull_request_url = (
            f"https://github.com/grace-wj/superset/pull/{number}"
        )
        return session

    def get_session(self, session_id: str) -> Session:
        session = self._sessions[session_id]
        self._polls[session_id] += 1
        behavior = self._scenario[session_id]

        if behavior == "failed":
            session.status_enum = "failed"
            return session

        if behavior == "blocked":
            session.status_enum = BLOCKED
            return session

        if behavior == "blocked_then_recover":
            nudged = self._nudged_poll.get(session_id)
            if nudged is not None and self._polls[session_id] - nudged >= self.BLOCK_RECOVER_POLLS:
                return self._finish(session, session_id)
            session.status_enum = BLOCKED
            return session

        # Default "finish" behavior.
        if self._polls[session_id] >= self.POLLS_TO_FINISH:
            return self._finish(session, session_id)
        return session

    def send_message(self, session_id: str, message: str) -> None:
        self._sessions[session_id].messages.append(message)
        # A nudge starts the recovery clock for blocked_then_recover sessions.
        self._nudged_poll.setdefault(session_id, self._polls[session_id])


class LiveDevinClient(DevinClient):
    """Real Devin API client. Wired but unfired — Grace runs the live path."""

    def __init__(self, api_key: str, api_base: str) -> None:
        if not api_key:
            raise ValueError("DEVIN_API_KEY is required for live mode")
        self._api_base = api_base.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict:
        import requests  # imported lazily so the fake path needs no dependency

        resp = requests.post(
            f"{self._api_base}{path}", json=payload, headers=self._headers, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> dict:
        import requests

        resp = requests.get(
            f"{self._api_base}{path}", headers=self._headers, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def create_session(
        self,
        prompt: str,
        tags: list[str] | None = None,
        idempotent: bool = True,
        structured_output_schema: dict | None = None,
    ) -> Session:
        payload: dict = {"prompt": prompt, "idempotent": idempotent}
        if tags:
            payload["tags"] = tags
        if structured_output_schema:
            payload["structured_output_schema"] = structured_output_schema
        data = self._post("/sessions", payload)
        if data.get("is_new_session") is False:
            print(
                f"  WARN: Devin reused an EXISTING session {data.get('session_id')} "
                f"(is_new_session=false) — idempotency matched a prior request; "
                f"this run may be polling stale work."
            )
        return Session(session_id=data["session_id"], url=data.get("url", ""))

    def get_session(self, session_id: str) -> Session:
        data = self._get(f"/sessions/{session_id}")
        pull_request = data.get("pull_request") or {}
        return Session(
            session_id=session_id,
            url=data.get("url", ""),
            status_enum=data.get("status_enum", WORKING),
            pull_request_url=pull_request.get("url"),
            structured_output=data.get("structured_output"),
            messages=data.get("messages", []),
        )

    def send_message(self, session_id: str, message: str) -> None:
        self._post(f"/sessions/{session_id}/messages", {"message": message})


def make_client(config) -> DevinClient:
    """Pick the implementation based on DEVIN_MODE. Same interface either way."""
    if config.is_live:
        return LiveDevinClient(config.devin_api_key, config.devin_api_base)
    return FakeDevinClient()
