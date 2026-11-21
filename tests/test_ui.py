"""Auth gate for the NiceGUI management UI served by build_app()."""
import base64

from fastapi.testclient import TestClient

from crispy_copilot_gateway import keys
from crispy_copilot_gateway.proxy import build_app


def _basic(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_ui_requires_basic_auth():
    res = TestClient(build_app()).get("/ui/")
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == 'Basic realm="crispy-copilot-gateway"'
    assert TestClient(build_app()).get("/ui/socket.io/?EIO=4&transport=polling").status_code == 401


def test_ui_accepts_proxy_key_as_password():
    raw = keys.create("ui-test")
    res = TestClient(build_app()).get("/ui/", headers=_basic("anyone", raw))
    assert res.status_code == 200
    assert "Proxy API keys" in res.text
    assert "Copilot usage" in res.text


def test_ui_rejects_wrong_password():
    keys.create("ui-test")
    res = TestClient(build_app()).get("/ui/", headers=_basic("anyone", "ck_wrong"))
    assert res.status_code == 401


def test_api_routes_still_need_proxy_key():
    client = TestClient(build_app())
    assert client.get("/v1/models").status_code == 401
    assert client.get("/healthz").json() == {"ok": True}
    raw = keys.create("t")
    assert client.get("/v1/models", headers={"x-api-key": raw}).status_code == 503
