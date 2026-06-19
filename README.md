# Devin Driver

A factory that drives [Devin](https://devin.ai) sessions to **empirically
verify** Apache Superset's per-engine time-grain SQL against real databases.

## What

Superset abstracts 40+ databases through `superset/db_engine_specs/*.py` —
hand-written SQL for how each dialect truncates a timestamp to a grain (second,
minute, … week, month, quarter, year). That SQL is hand-maintained and almost
entirely **unverified against a running database**, so a `WEEK` bucket can be
silently wrong, or differ by backend (Postgres starts the week Monday;
MySQL/SQLite start it Sunday).

A script can *enumerate* the specs, but it cannot boot a database, run each
grain's SQL, diff the result against an independent oracle, and report what
diverged. Only an autonomous agent operating a live environment can. Devin
Driver is the orchestrator that fans that work out — **one session per engine**
— and reports a correctness matrix. The harness is the product, not any single
finding.

## Architecture

```
scanner          github_client (seam)     orchestrator               dashboard
--------         --------------------     ------------               ---------
enumerate   -->  file 1 issue/engine -->  spawn 1 session/engine  -->  funnel +
engines ×        (Fake | Live)            poll status_enum             engine×grain
grains                  ^                  capture pull_request.url     matrix +
(work-list)             |                  manage blocked/failed        issue/PR trail
                        +-- comment status + PR back on the issue <-----+
                 devin_client (seam): Fake | Live, identical orchestrator path
```

- **`scanner`** — a dumb work-list generator. It lists which `(engine, grain)`
  pairs to verify and emits one work item per engine. It does **no discrepancy
  detection**; findings come only from Devin's live execution.
- **`devin_client`** — the Devin fake/live seam. `FakeDevinClient` and
  `LiveDevinClient` implement one interface, so the orchestrator code path is
  identical in both modes. `DEVIN_MODE` selects which is wired in.
- **`github_client`** — the GitHub fake/live seam (same pattern). Files one
  issue per engine on the fork and comments status + PR url back, closing the
  remediation loop. In live mode, missing GitHub credentials are a hard error
  (we never spawn real billed sessions while filing fake in-memory issues).
- **`orchestrator`** — initiates *and manages*: files the issue, spawns a session
  per engine, polls `status_enum`, captures the PR url, nudges `blocked` sessions
  (with a grace window before giving up), and comments status back. Every live
  call is isolated, so one transient HTTP error logs and continues rather than
  aborting the whole batch.
- **`dashboard`** — renders the funnel, the engine×grain matrix, and the
  per-engine issue/PR trail to the CLI. Results are `verified`, `failed` (Devin's
  own failure), or `needs_attention` (factory-side: timed out / lost contact /
  stuck blocked) — never conflated.

## Run

Requires Python 3.11+.

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your values for live mode
python main.py                # defaults to fake mode
```

With Docker:

```bash
docker build -t devin-driver .
docker run --rm --env-file .env devin-driver
```

## Simulate

The default `DEVIN_MODE=fake` runs the entire pipeline deterministically with no
API key and no network — sessions resolve in seconds. This validates the
**plumbing only** (it proves the factory works), not prompt quality, which is
live-only.

```bash
DEVIN_MODE=fake python main.py    # green end-to-end, free
DEVIN_MODE=live python main.py    # real sessions (10–40 min each)
```

Live mode requires `DEVIN_API_KEY`, and — because it files real issues — also
`GITHUB_TOKEN` and `GITHUB_REPO`; it fails loudly if they're missing.

## Test

```bash
pip install -r requirements-dev.txt
pytest -q
```

The suite covers the orchestrator's management paths — including the blocked
grace window and the error-isolation contracts (a nudge that raises, a poll that
raises, a comment that raises) that the plain fake can't exercise — plus GitHub
issue idempotency and end-to-end wiring.

The live path is wired but intentionally unfired here; real sessions, fork
credentials, and PR judgment are owned by a human.

## Observability

Each run prints:

- the **scan** (the JSON work-list of engines × grains),
- a **funnel**: enumerated → issues filed → launched → verified / failed /
  needs-attention, plus PRs opened and discrepancies surfaced,
- the **engine × grain matrix** (`OK` verified / `!!` discrepancy / `??`
  unknown) — the headline visual,
- the **per-engine trail**: each engine's status, issue url, PR url, and a note
  for anything that needs attention.

In fake mode the matrix is all-`OK` with zero discrepancies by design: findings
are produced by live runs, never simulated.

## Idempotency

- **Issues** are find-or-create by label (`devin-driver:{engine}`), so a re-run
  reuses the existing **open** issue instead of filing a duplicate (a closed,
  human-triaged issue is intentionally not reused).
- **Sessions** are created with `idempotent=True`; cross-run dedup relies on the
  Devin API (keyed on the deterministic per-engine prompt) and is an unvalidated
  live assumption — within a single run, exactly one session per engine.

## Status

This is a proof-of-concept. Built: scanner, Devin + GitHub fake/live seams, the
orchestrator (files issue → spawns session → polls → comments status + PR back,
with blocked/failed management and per-call error isolation), a CLI dashboard,
and a pytest suite. Runs green and creds-free in fake mode; the live path is
wired but unfired. Deferred: HTTP retry/backoff, a webhook trigger, and a richer
(e.g. Streamlit) dashboard. The Devin prompt in `devin_driver/prompts.py` is an
**unvalidated draft** pending live tuning. PRs Devin opens won't auto-reference
the filed issues (the prompt is human-owned and doesn't receive the issue
number) — the loop is closed on the dashboard, not via fork cross-references.
