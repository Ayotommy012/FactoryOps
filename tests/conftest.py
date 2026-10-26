"""Shared pytest configuration.

Live Meterless inference is isolated by default: the runtime's cloud lane shares
one small upstream quota with the running application, so tests that silently
picked up the real runtime token were both flaky and a source of the 429s seen
in the console. Real token sources (env vars, %LOCALAPPDATA% / home proxy-token)
are hidden; tests that set their own token still work.
Set FACTORYOPS_LIVE_METERLESS_TESTS=1 to opt back in to live inference.
"""

import os

import pytest

from app.providers.meterless import reset_rate_limit_state

_TOKEN_ENV_VARS = ("METERLESS_API_KEY", "METERLESS_RUNTIME_TOKEN", "METERLESS_TOKEN", "METERLESS_STATE_DIR")


@pytest.fixture(autouse=True)
def _isolate_meterless(monkeypatch, tmp_path):
    if os.getenv("FACTORYOPS_LIVE_METERLESS_TESTS", "").strip() not in ("1", "true", "yes"):
        for var in _TOKEN_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        isolated_home = tmp_path / "isolated_home"
        isolated_home.mkdir()
        monkeypatch.setenv("LOCALAPPDATA", str(isolated_home))
        monkeypatch.setenv("USERPROFILE", str(isolated_home))
        monkeypatch.setenv("HOME", str(isolated_home))
    # Keep backoff short so retry tests stay fast and deterministic within budgets.
    monkeypatch.setenv("METERLESS_BACKOFF_BASE_SEC", "0.05")
    monkeypatch.setenv("METERLESS_BACKOFF_CAP_SEC", "0.5")
    monkeypatch.setenv("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "false")
    monkeypatch.delenv("METERLESS_DISABLED", raising=False)
    reset_rate_limit_state()
    from app.services import recovery
    recovery._EXPLANATION_CACHE.clear()
    recovery._EXPLANATION_FAILURES.clear()
    yield
    reset_rate_limit_state()
