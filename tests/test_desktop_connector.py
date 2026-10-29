import importlib.util
from pathlib import Path
import threading
import http.client
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("desktop_connector", Path(__file__).parents[1] / "connector" / "connector.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


def test_loopback_pairing_requires_trusted_origin_and_never_returns_credentials(tmp_path):
    connector = desktop.Connector(state_path=tmp_path / "state.json")
    server = desktop.control_server(connector, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request("GET", "/status", headers={"Origin": "https://evil.example", "Host": "127.0.0.1:0"})
        assert connection.getresponse().status == 403
        connection.request("GET", "/status", headers={"Origin": connector.web, "Host": "127.0.0.1:0"})
        response = connection.getresponse()
        assert response.status == 200
        assert "token" not in response.read().decode()
        assert response.headers["Access-Control-Allow-Origin"] == connector.web
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_pairing_secret_is_saved_locally_and_generation_only_uses_fixed_ollama_address(tmp_path):
    connector = desktop.Connector(state_path=tmp_path / "state.json")
    with patch.object(desktop, "request_json", return_value={"token": "device-secret"}) as call:
        connector.pair("one-time-code")
        assert connector.token == "device-secret"
        assert "device-secret" in connector.state_path.read_text()
        assert call.call_args.args[0] == connector.api + "/connectors/pair"
    connector.models = ["llama3.2:3b"]
    with patch.object(desktop, "request_json", side_effect=[{"message": {"content": "{}"}}, {}]) as call:
        connector.generate({"id": "task", "payload": {"model": "llama3.2:3b", "stream": False}}, "device-secret")
        assert call.call_args_list[0].args[0] == "http://127.0.0.1:11434/api/chat"
        assert call.call_args_list[1].args[0] == connector.api + "/connectors/runtime/results"
    with patch.object(desktop, "request_json", return_value={}) as call:
        connector.generate({"id": "task", "payload": {"model": "not-installed", "stream": False}}, "device-secret")
        assert all("11434" not in invocation.args[0] for invocation in call.call_args_list)


def test_model_discovery_excludes_embedding_only_models(tmp_path):
    connector = desktop.Connector(state_path=tmp_path / "state.json")
    connector.token = "device-secret"
    def respond(url, payload=None, token=None, timeout=15):
        if url.endswith("/api/tags"):
            return {"models": [{"name": "chat"}, {"name": "embedding"}]}
        if url.endswith("/api/show"):
            return {"capabilities": ["completion" if payload["model"] == "chat" else "embedding"]}
        connector.stop.set()
        return {"task": None}
    with patch.object(desktop, "request_json", side_effect=respond):
        connector.run()
    assert connector.models == ["chat"]
