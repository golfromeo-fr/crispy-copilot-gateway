"""CLI surface via CliRunner: login guard, key management."""
from typer.testing import CliRunner

from crispy_copilot_gateway import connections
from crispy_copilot_gateway.cli import app

runner = CliRunner()


def test_login_without_client_id_exits_with_guidance(monkeypatch):
    monkeypatch.setattr(connections, "CLIENT_ID", "")
    result = runner.invoke(app, ["login", "work"])
    assert result.exit_code == 1
    assert "CRISPY_COPILOT_CLIENT_ID" in result.output


def test_key_lifecycle_roundtrip():
    created = runner.invoke(app, ["key", "zcode"])
    assert created.exit_code == 0
    raw = created.output.strip()
    assert raw.startswith("ck_")
    listed = runner.invoke(app, ["keys"])
    assert "zcode" in listed.output and raw not in listed.output
    revoked = runner.invoke(app, ["revoke", "zcode"])
    assert revoked.output.strip() == "revoked"
    assert runner.invoke(app, ["revoke", "zcode"]).output.strip() == "not found"
