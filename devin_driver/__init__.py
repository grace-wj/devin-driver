"""Devin Driver — a deterministic factory that drives Devin sessions to
empirically verify Superset's per-engine time-grain SQL.

The orchestrator code path is identical for fake and live runs; the only
difference is which DevinClient implementation is wired in (see devin_client).
"""
