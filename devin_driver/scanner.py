"""The scanner: a dumb work-list generator.

It enumerates which (engine, grain) pairs to verify and emits one work item per
engine. It deliberately performs NO discrepancy detection — findings must come
from Devin executing real SQL against a live DB, never from static analysis
here. Pre-detecting a finding and then "verifying" it live would be theater.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The time grains Superset defines per engine spec (`_time_grain_expressions`).
GRAINS: tuple[str, ...] = (
    "second",
    "minute",
    "hour",
    "day",
    "week",
    "month",
    "quarter",
    "year",
)

# Engines to verify. SQLite is the zero-infra, guaranteed-green substrate;
# Postgres adds the cross-dialect dimension (e.g. WEEK starts Monday vs Sunday).
ENGINES: tuple[str, ...] = ("sqlite", "postgresql")


@dataclass
class WorkItem:
    """One unit of work — an engine and the grains to verify for it."""

    engine: str
    grains: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"engine": self.engine, "grains": self.grains}


def scan(
    engines: tuple[str, ...] = ENGINES,
    grains: tuple[str, ...] = GRAINS,
) -> list[WorkItem]:
    """Enumerate the verification targets. One work item per engine."""
    return [WorkItem(engine=engine, grains=list(grains)) for engine in engines]
