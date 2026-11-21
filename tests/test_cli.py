"""CLI surface via CliRunner: device-flow login, key management."""
from typer.testing import CliRunner

from crispy_copilot_gateway import connections
from crispy_copilot_gateway.cli import app

runner = CliRunner()


def _patch_flow(monkeypatch, complete):
    async def start():
        return {"user_code": "ABCD", "verification_uri": "https://github.com/login/device",
                "device_code": "dc", "interval": 5}

    async def poll(device_code):
        return {"access_token": "gh-token"}

    monkeypatch.setattr(connections, "start_device_flow", start)
    monkeypatch.setattr(connections, "poll_once", poll)
    monkeypatch.setattr(connections, "complete_login", complete)
    monkeypatch.setattr("crispy_copilot_gateway.cli.webbrowser.open", lambda url: None)


def test_login_completes_device_flow(monkeypatch):
    async def complete(label, token):
        connections.save(label, token)
        return {"label": label, "api_endpoint": None}
    _patch_flow(monkeypatch, complete)
    result = runner.invoke(app, ["login", "work"])
    assert result.exit_code == 0
    assert "Connected 'work'." in result.output
    assert connections.load_all()["work"]["github_token"] == "gh-token"


def test_login_no_copilot_access_exits(monkeypatch):
    async def complete(label, token):
        raise ValueError("GitHub account has no Copilot access")
    _patch_flow(monkeypatch, complete)
    result = runner.invoke(app, ["login", "work"])
    assert result.exit_code == 1
    assert "no Copilot access" in result.output


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
