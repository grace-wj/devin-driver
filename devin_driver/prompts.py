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
        "grains": {
            "type": "object",
            "description": "Per-grain result: 'verified' or 'discrepancy'.",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["verified", "discrepancy"]},
                    "note": {"type": "string"},
                },
                "required": ["status"],
            },
        },
    },
    "required": ["engine", "grains"],
}


def build_prompt(engine: str, grains: list[str]) -> str:
    """Draft prompt for one engine. UNVALIDATED — tune against live sessions."""
    grain_list = ", ".join(grains)
    return (
        f"You are verifying Apache Superset's time-grain SQL for the `{engine}` "
        f"engine spec against a real, running database.\n\n"
        f"Steps:\n"
        f"1. Stand up a live {engine} database in your VM.\n"
        f"2. For each grain ({grain_list}), take Superset's "
        f"`_time_grain_expressions` SQL for {engine} and run it over a battery "
        f"of real timestamps.\n"
        f"3. Independently compute the expected bucket for each timestamp using "
        f"Python's `datetime` as an oracle.\n"
        f"4. Compare every bucket to the oracle. Where they diverge, that grain "
        f"is a discrepancy to surface for review (NOT a bug to silently 'fix').\n"
        f"5. For any discrepancy, open a PR that pins the empirically-observed "
        f"behavior with a regression test and flags it for a maintainer.\n"
        f"6. Report results as structured output per the provided schema.\n"
    )
