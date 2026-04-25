# tests/test_mcp_server.py
import json, pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

os.environ.setdefault("APP_TOKEN", "test-token")
os.environ.setdefault("WEBAPP_PUBLIC_URL", "http://192.168.1.13:5000")
os.environ.setdefault("DATA_DIR", "/tmp/test-data")
os.environ.setdefault("USER_DATA_DIR", "/tmp/test-userdata")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

from unittest.mock import patch, MagicMock
from pathlib import Path


def _make_app():
    """Create Flask test app with mcp_server blueprint registered."""
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["APP_TOKEN"] = "test-token"
    app.config["WEBAPP_PUBLIC_URL"] = "http://192.168.1.13:5000"
    app.config["APP_DATA_DIR"] = Path("/tmp/test-data")
    app.config["APP_USER_DATA_DIR"] = Path("/tmp/test-userdata")
    app.config["APP_REDIS"] = MagicMock()
    app.config["APP_CELERY"] = MagicMock()
    from routes.mcp_server import bp
    app.register_blueprint(bp)
    return app


def _post(client, method, params=None, req_id=1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        body["params"] = params
    return client.post("/mcp", json=body)


class TestMCPAuth:
    def test_bad_token_returns_error(self):
        app = _make_app()
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "list_dlc_projects",
                "arguments": {"session_token": "wrong"}
            })
            data = resp.get_json()
            assert "error" in data


class TestMCPInitialize:
    def test_initialize_returns_protocol_version(self):
        app = _make_app()
        with app.test_client() as c:
            resp = _post(c, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hermes", "version": "1.0"}
            })
            data = resp.get_json()
            assert data["result"]["protocolVersion"] == "2024-11-05"
            assert "Mcp-Session-Id" in resp.headers


class TestMCPToolsList:
    def test_tools_list_returns_9_tools(self):
        app = _make_app()
        with app.test_client() as c:
            resp = _post(c, "tools/list")
            data = resp.get_json()
            tools = data["result"]["tools"]
            names = [t["name"] for t in tools]
            assert "list_dlc_projects" in names
            assert "list_anipose_projects" in names
            assert "jitter_prelabel" in names
            assert "get_task_status" in names
            assert "webapp_link" in names
            assert len(tools) == 9


class TestMCPToolsCall:
    def test_list_dlc_projects_empty_data_dir(self, tmp_path):
        app = _make_app()
        app.config["APP_DATA_DIR"] = tmp_path
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "list_dlc_projects",
                "arguments": {"session_token": "test-token"}
            })
            data = resp.get_json()
            assert "result" in data
            content = data["result"]["content"][0]["text"]
            assert isinstance(json.loads(content), list)

    def test_list_anipose_projects(self, tmp_path):
        app = _make_app()
        app.config["APP_DATA_DIR"] = tmp_path
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "list_anipose_projects",
                "arguments": {"session_token": "test-token"}
            })
            data = resp.get_json()
            assert "result" in data

    def test_webapp_link_contains_token(self):
        app = _make_app()
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "webapp_link",
                "arguments": {"session_token": "test-token"}
            })
            data = resp.get_json()
            content = json.loads(data["result"]["content"][0]["text"])
            assert "192.168.1.13:5000" in content["url"]
            assert "token=test-token" in content["url"]

    def test_get_task_status_pending(self):
        app = _make_app()
        mock_celery = MagicMock()
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.info = None
        mock_celery.AsyncResult.return_value = mock_result
        app.config["APP_CELERY"] = mock_celery
        with app.test_client() as c:
            resp = _post(c, "tools/call", {
                "name": "get_task_status",
                "arguments": {"session_token": "test-token", "task_id": "abc-123"}
            })
            data = resp.get_json()
            content = json.loads(data["result"]["content"][0]["text"])
            assert content["state"] == "PENDING"

    def test_notifications_initialized_returns_204(self):
        app = _make_app()
        with app.test_client() as c:
            resp = c.post("/mcp", json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            })
            assert resp.status_code == 204
