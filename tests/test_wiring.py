"""Tests that the pieces are actually wired together (so the feature isn't dead
code) and that live mode fails loud on missing GitHub credentials."""

import pytest

import main as main_module
from devin_driver.config import Config
from devin_driver.github_client import FakeGitHubClient, LiveGitHubClient, make_github_client


def _config(**overrides):
    base = dict(
        mode="fake",
        devin_api_key="",
        devin_api_base="https://api.devin.ai/v1",
        github_token="",
        github_repo="",
    )
    base.update(overrides)
    return Config(**base)


def test_make_github_client_fake_mode_returns_fake():
    assert isinstance(make_github_client(_config(mode="fake")), FakeGitHubClient)


def test_make_github_client_live_mode_requires_credentials():
    # Live but no token/repo -> hard error, never a silent fake.
    with pytest.raises(ValueError):
        make_github_client(_config(mode="live"))


def test_make_github_client_live_mode_with_creds_returns_live():
    client = make_github_client(
        _config(mode="live", github_token="ghp_x", github_repo="grace-wj/superset")
    )
    assert isinstance(client, LiveGitHubClient)


def test_main_smoke_run_fake_mode(monkeypatch):
    monkeypatch.setenv("DEVIN_MODE", "fake")
    # Should run the whole factory end-to-end without raising.
    main_module.main()
