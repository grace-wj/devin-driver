"""Observability: the funnel and the engine×grain matrix, rendered to the CLI.

This is the money visual in miniature — a green/red grid of which grains each
engine had verified, plus the headline funnel counts and the issue/PR trail.
"""

from __future__ import annotations

from .orchestrator import FAILED, NEEDS_ATTENTION, VERIFIED, Result
from .scanner import GRAINS

_CELL = {"verified": "OK", "discrepancy": "!!", "unknown": "??"}


def render(results: list[Result], grains: tuple[str, ...] = GRAINS) -> None:
    launched = len(results)
    verified = sum(1 for r in results if r.status == VERIFIED)
    failed = sum(1 for r in results if r.status == FAILED)
    needs_attention = sum(1 for r in results if r.status == NEEDS_ATTENTION)
    issues_filed = sum(1 for r in results if r.issue_url)
    with_pr = sum(1 for r in results if r.pull_request_url)
    discrepancies = sum(len(r.discrepancies) for r in results)
    redundancies = sum(len(r.redundancies) for r in results)

    print("\n=== Funnel ===")
    print(f"  engines enumerated : {launched}")
    print(f"  issues filed       : {issues_filed}")
    print(f"  sessions launched  : {launched}")
    print(f"  verified           : {verified}")
    print(f"  failed (Devin)     : {failed}")
    print(f"  needs attention    : {needs_attention}")
    print(f"  PRs opened         : {with_pr}")
    print(f"  discrepancies      : {discrepancies}")
    print(f"  redundancies       : {redundancies}")

    print("\n=== Engine x Grain matrix ===")
    header = "  " + f"{'engine':12s}" + "".join(f"{g[:5]:>7s}" for g in grains)
    print(header)
    for r in results:
        cells = "".join(
            f"{_CELL.get(r.grain_results.get(g, 'unknown'), '??'):>7s}" for g in grains
        )
        print(f"  {r.engine:12s}{cells}")

    print("\n=== Per-engine trail ===")
    for r in results:
        issue = r.issue_url or "(no issue)"
        pr = r.pull_request_url or "(no PR)"
        line = f"  {r.engine:12s} {r.status:15s} issue={issue}  pr={pr}"
        if r.note:
            line += f"  [{r.note}]"
        print(line)
