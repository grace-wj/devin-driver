"""Runtime configuration, loaded from the environment (.env in development)."""

from __future__ import annotations

import os
from dataclasses import dataclass

# .env loading is a convenience for local dev; never required for the factory to
# run. The fake path needs no secrets at all.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional for the fake path
    pass


@dataclass(frozen=True)
class Config:
    mode: str  # "fake" | "live"
    devin_api_key: str
    devin_api_base: str
    github_token: str
    github_repo: str

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


def load_config() -> Config:
    return Config(
        mode=os.getenv("DEVIN_MODE", "fake").strip().lower(),
        devin_api_key=os.getenv("DEVIN_API_KEY", "").strip(),
        devin_api_base=os.getenv("DEVIN_API_BASE", "https://api.devin.ai/v1").rstrip("/"),
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        github_repo=os.getenv("GITHUB_REPO", "").strip(),
    )
