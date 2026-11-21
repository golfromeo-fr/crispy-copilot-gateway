"""Shared fixtures: isolated data dir per test."""
import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISPY_COPILOT_DATA_DIR", str(tmp_path / "data"))
