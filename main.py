"""Devin Driver entrypoint.

Wires the factory together: load config -> scan for work -> spawn & manage one
Devin session per engine -> render observability. The orchestration path is
identical for fake and live; DEVIN_MODE selects which client is used.

    DEVIN_MODE=fake python main.py    # deterministic, no key, runs green
    DEVIN_MODE=live python main.py    # real sessions (Grace's path)
"""

from __future__ import annotations

import json

from devin_driver import dashboard, orchestrator, scanner
from devin_driver.config import load_config
from devin_driver.devin_client import make_client
from devin_driver.github_client import make_github_client


def main() -> None:
    config = load_config()
    print(f"Devin Driver — mode={config.mode}")

    work_items = scanner.scan()
    print("\n=== Scan (work-list) ===")
    print(json.dumps([w.to_dict() for w in work_items], indent=2))

    client = make_client(config)
    # In live mode this raises if GitHub creds are missing — we must never spawn
    # real sessions while filing fake issues.
    github = make_github_client(config)
    # Fake sessions resolve instantly; live sessions take 10-40 min, so poll
    # on a real interval there.
    poll_interval = 0.0 if not config.is_live else 15.0

    print("\n=== Launching sessions ===")
    results = orchestrator.run(
        client, work_items, github=github, poll_interval=poll_interval
    )

    dashboard.render(results)


if __name__ == "__main__":
    main()
