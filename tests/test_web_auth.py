"""Password boundaries, CLI guard, and SSE forwarding; no model or DB needed."""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from web.auth import DashboardPassword
from web import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "temporary-test-password")
    monkeypatch.setattr(server.app, "middleware_stack", None)
    with TestClient(server.app) as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/app.js", "/data.js", "/api/health",
                                  "/api/results", "/openapi.json"])
def test_all_routes_require_login(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")


@pytest.mark.parametrize("authorization", ["Basic !!!", "Bearer token", "Basic /w=="])
def test_malformed_credentials_fail_closed(client, authorization):
    assert client.get("/", headers={"Authorization": authorization}).status_code == 401


@pytest.mark.parametrize("credentials", [("teammate", "wrong"),
                                         ("other", "temporary-test-password")])
def test_wrong_credentials(client, credentials):
    assert client.get("/", auth=credentials).status_code == 401


def test_ask_is_blocked_before_model_runs(client, monkeypatch):
    def unexpected_call(*args):
        pytest.fail("Unauthenticated request reached inference")
    monkeypatch.setattr(server, "_run_stream", unexpected_call)
    assert client.post("/api/ask", json={"question": "Count customers"}).status_code == 401


def test_login_serves_page_and_sse(client, monkeypatch):
    credentials = ("teammate", "temporary-test-password")
    response = client.get("/", auth=credentials)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    monkeypatch.setattr(server, "_run_stream", lambda req: iter([
        'data: {"type":"arm_start","arm":"baseline"}\n\n',
        'data: {"type":"done","graded":false}\n\n',
    ]))
    response = client.post("/api/ask", json={"question": "Count customers"}, auth=credentials)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("data:") == 2


def test_sse_chunks_are_forwarded_immediately():
    sent = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        assert sent[-1]["body"] == b"first"
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    async def send(message):
        sent.append(message)

    header = b"Basic " + base64.b64encode(b"teammate:test-password")
    asyncio.run(DashboardPassword(downstream, password="test-password")(
        {"type": "http", "headers": [(b"authorization", header)]}, None, send))
    assert sent[-1]["body"] == b"last"


def test_local_use_without_password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    monkeypatch.setattr(server.app, "middleware_stack", None)
    with TestClient(server.app) as client:
        assert client.get("/").status_code == 200


def test_network_cli_requires_password(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setattr("sys.argv", ["web.server", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit) as error:
        server.main()
    assert error.value.code == 2


def test_prompt_password_applies_to_server(monkeypatch):
    monkeypatch.setattr("sys.argv", ["web.server", "--host", "0.0.0.0", "--password-prompt"])
    monkeypatch.setattr(server.getpass, "getpass", lambda prompt: "new-session-password")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "old-session-password")
    monkeypatch.setattr(server.app, "middleware_stack", None)

    def check_run(app, **kwargs):
        assert kwargs["host"] == "0.0.0.0"
        with TestClient(app) as client:
            assert client.get("/", auth=("teammate", "old-session-password")).status_code == 401
            assert client.get("/", auth=("teammate", "new-session-password")).status_code == 200

    monkeypatch.setattr("uvicorn.run", check_run)
    assert server.main() == 0
