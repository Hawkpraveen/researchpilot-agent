import asyncio
import json

import httpx
import pytest

from app.local_bridge import MAX_BODY_BYTES, create_bridge

KEY = "test-bridge-key-" + "a" * 32


def test_bridge_requires_secret():
    with pytest.raises(RuntimeError):
        create_bridge(token="")


def test_auth_paths_and_translation():
    async def run():
        calls = []

        def ollama(request):
            calls.append(request)
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "llama3.2:3b"}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

        app = create_bridge(KEY, httpx.MockTransport(ollama))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge") as client:
            assert (await client.get("/v1/models")).status_code == 401
            assert not calls
            headers = {"Authorization": f"Bearer {KEY}"}
            response = await client.get("/v1/models", headers=headers)
            assert response.json()["data"][0]["id"] == "llama3.2:3b"
            assert (await client.post("/api/pull", headers=headers, json={"name": "test"})).status_code == 404
            assert (await client.get("/docs", headers=headers)).status_code == 404
            response = await client.post("/v1/chat/completions", headers=headers, json={
                "model": "llama3.2:3b", "messages": [], "max_completion_tokens": 900,
            })
            assert response.status_code == 200
            body = json.loads(calls[-1].content)
            assert body["max_tokens"] == 900 and body["stream"] is False
            assert "max_completion_tokens" not in body
            assert "authorization" not in calls[-1].headers
            assert str(calls[-1].url) == "http://127.0.0.1:11434/v1/chat/completions"
            assert (await client.post("/v1/chat/completions", headers=headers, content=b"x" * (MAX_BODY_BYTES + 1))).status_code == 413
            assert (await client.post("/v1/chat/completions", headers=headers, json={
                "model": "test", "messages": [], "stream": True,
            })).status_code == 400
    asyncio.run(run())


def test_bridge_reports_ollama_unavailable_without_leaking_details():
    async def run():
        def fail(request):
            raise httpx.ConnectError("private system details", request=request)
        app = create_bridge(KEY, httpx.MockTransport(fail))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge") as client:
            response = await client.get("/v1/models", headers={"Authorization": f"Bearer {KEY}"})
            assert response.status_code == 502
            assert "private system details" not in response.text
    asyncio.run(run())
