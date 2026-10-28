import pytest
from pathlib import Path
from forex_ai.risk import account_guard


@pytest.fixture(autouse=True)
def isolate_account_fingerprint(tmp_path, monkeypatch):
    """Isolate on-disk account fingerprint during automated tests to avoid environment drift."""
    fake_path = tmp_path / "isolated_account_fingerprint"
    monkeypatch.setattr(account_guard, "DEFAULT_FINGERPRINT_PATH", fake_path)
