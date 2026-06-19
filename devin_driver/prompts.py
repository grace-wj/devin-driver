"""Devin session prompt.

⚠️ UNVALIDATED — requires live tuning by Grace.

The prompt is the actual product and can only be validated against live Devin
sessions (fake = canned responses, proves nothing). This is a starting draft so
the factory has something to send; treat the wording as a placeholder, not final.
"""

from __future__ import annotations

# The schema we ask Devin to return, so the orchestrator can build the matrix
# from structured output rather than parsing prose.
STRUCTURED_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "engine": {"type": "string"},
        "reference": {
            "type": "string",
            "description": (
                "The single fixed oracle convention used for every engine, "
                "e.g. 'ISO-8601: week starts Monday'."
            ),
        },
        "grains": {
            "type": "object",
            "description": "Per-grain result vs the reference: 'verified' or 'discrepancy'.",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["verified", "discrepancy"]},
                    "note": {"type": "string"},
                },
                "required": ["status"],
            },
        },
        "redundancies": {
            "type": "array",
            "description": (
                "Pairs of time-grain expressions in this spec that produced "
                "identical buckets across the whole battery (a redundancy)."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "grain_a": {"type": "string"},
                    "grain_b": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["grain_a", "grain_b"],
            },
        },
    },
    "required": ["engine", "grains"],
}


def build_prompt(engine: str, grains: list[str]) -> str:
    """Hardened draft for one engine. Still UNVALIDATED — tune against live sessions."""
    grain_list = ", ".join(grains)
    return (
        f"You are empirically verifying Apache Superset's time-grain SQL for the "
        f"`{engine}` engine spec by EXECUTING it against a real, running database — "
        f"not by reading or reasoning about the SQL. Use the actual SQL from the "
        f"`_time_grain_expressions` mapping in `superset/db_engine_specs/` for the "
        f"`{engine}` spec.\n\n"

        f"METHOD (you MUST actually run this; do NOT assert on SQL strings):\n"
        f"1. Stand up a real {engine} database in your VM and connect to it.\n"
        f"2. Build a timestamp battery that stresses bucket boundaries — at minimum a "
        f"Sunday and the adjacent Monday, the last day of a month/quarter/year and the "
        f"first of the next, Feb 29 of a leap year, plus a few ordinary datetimes — "
        f"spanning at least two different years.\n"
        f"3. For each grain ({grain_list}): run Superset's actual SQL expression over "
        f"every timestamp and record the bucket (the truncated period-start timestamp) "
        f"the database returns.\n"
        f"4. Independently compute the expected bucket in Python with `datetime` against "
        f"a SINGLE FIXED reference convention: ISO-8601, where the week starts on "
        f"MONDAY. Use this same reference for EVERY engine — do NOT pick a convention "
        f"that happens to match this engine. The goal is to surface where dialects "
        f"diverge from one shared reference, so the reference must not move per engine.\n"
        f"5. Compare the database's bucket to the reference for every (grain, timestamp).\n\n"

        f"INTERPRETATION:\n"
        f"- `verified`: the database's buckets match the ISO-8601 reference across the "
        f"whole battery.\n"
        f"- `discrepancy`: it diverges from the reference. This is often a legitimate "
        f"dialect CONVENTION difference (e.g. WEEK starting Sunday instead of Monday), "
        f"not a defect — SURFACE it for review, state plainly which convention the "
        f"engine uses, and do NOT call the engine 'wrong' or modify its SQL.\n"
        f"- `redundancy`: also run and compare ALL time-grain expressions in this spec "
        f"(including grains beyond the list above) and flag any two that produce "
        f"identical buckets across the whole battery.\n\n"

        f"DELIVERABLE — always open exactly ONE pull request for the `{engine}` spec, "
        f"against this fork:\n"
        f"- Add a focused, self-contained regression test (do not wire into Superset's "
        f"full test harness) pinning the EMPIRICALLY-OBSERVED behavior for every grain "
        f"(valuable even when nothing diverges — it locks in behavior previously "
        f"unverified against a live DB).\n"
        f"- EVERY test MUST execute the engine-spec SQL against a live database at test "
        f"runtime and assert on the executed result. Do NOT assert that two precomputed "
        f"or hard-coded value lists are equal — that is a tautological test that proves "
        f"nothing. In particular, a redundancy test MUST run BOTH expressions live "
        f"against the database and compare their executed outputs to each other, never "
        f"two stored constants.\n"
        f"- Write the PR for a human maintainer skimming it in 30 seconds, NOT as a "
        f"data dump. Lead with the conclusion, keep prose plain, and push the raw "
        f"evidence to the bottom. Use this exact structure:\n"
        f"  TITLE: `Verify {engine} time-grain SQL against a live DB` (append "
        f"`— N discrepanc{{y/ies}} surfaced` only if any were found).\n"
        f"  ## Summary — 1-2 plain sentences a maintainer can read in isolation: what "
        f"you did and the headline verdict (e.g. 'Ran all 8 time grains against a live "
        f"{engine} DB; 7 match ISO-8601, WEEK starts Sunday by dialect convention').\n"
        f"  ## Why this matters — ONE sentence: these expressions were previously "
        f"unverified against a running DB, so a wrong or convention-divergent bucket "
        f"could silently skew dashboards.\n"
        f"  ## Findings — plain-English bullets, one per discrepancy/redundancy, each "
        f"stating the convention in human terms (e.g. '`WEEK` buckets to Sunday, not "
        f"Monday — a SQLite convention, not a defect'). If everything matched, say so "
        f"in one line.\n"
        f"  ## What this PR changes — one line: adds a regression test pinning the "
        f"observed behavior; does NOT modify any engine SQL.\n"
        f"  ## Evidence (executed) — LAST: the reference convention, the timestamp "
        f"battery, and the full per-grain `grain | sql bucket | iso-8601 bucket | "
        f"match?` table. Wrap this section in a collapsible `<details>` block so it "
        f"does not bury the summary.\n"
        f"- Flag any discrepancy or redundancy for a maintainer in the Findings "
        f"section. Do NOT modify the engine spec's SQL.\n\n"

        f"REPORT: return structured output per the provided schema — set `reference` to "
        f"the convention you used, mark each grain `verified` or `discrepancy` against "
        f"it with a short `note` (e.g. the week-start day this engine uses), and list "
        f"any `redundancies` as {{grain_a, grain_b, note}} pairs.\n"
    )
