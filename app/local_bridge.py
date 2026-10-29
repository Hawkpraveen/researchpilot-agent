"""Optional authenticated gateway for connecting hosted ResearchPilot to local Ollama.

Run separately on the developer's computer, never as part of the hosted agent.
"""

import asyncio
import json
import os
import secrets

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

MAX_BODY_BYTES = 256 * 1024
UPSTREAM = "http://127.0.0.1:11434"


def create_bridge(token: str | None = None, transport=None) -> FastAPI:
    expected = token if token is not None else os.getenv("RESEARCHPILOT_BRIDGE_TOKEN", "")
    if len(expected) < 32:
        raise RuntimeError("Set RESEARCHPILOT_BRIDGE_TOKEN to a random secret of at least 32 characters.")

    async def authenticate(request: Request):
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            raise HTTPException(401, "A valid bridge key is required.", headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, dependencies=[Depends(authenticate)])
    busy = asyncio.Lock()

    async def upstream(method: str, route: str, payload=None):
        try:
            async with httpx.AsyncClient(
                transport=transport, timeout=httpx.Timeout(115, connect=5),
                follow_redirects=False, trust_env=False,
            ) as client:
                response = await client.request(method, UPSTREAM + route, json=payload)
            if response.is_redirect:
                return JSONResponse({"error": "Ollama returned an unexpected redirect."}, status_code=502)
            return Response(response.content, status_code=response.status_code,
                            media_type="application/json", headers={"Cache-Control": "no-store"})
        except httpx.TimeoutException:
            return JSONResponse({"error": "The local model exceeded the bridge timeout."}, status_code=504)
        except httpx.HTTPError:
            return JSONResponse({"error": "Local Ollama is unavailable. Start Ollama on this computer."}, status_code=502)

    @app.get("/v1/models")
    async def models():
        return await upstream("GET", "/v1/models")

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY_BYTES:
                raise HTTPException(413, "The model request is too large.")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Send a valid JSON model request.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("model"), str) or not isinstance(payload.get("messages"), list):
            raise HTTPException(400, "A model and messages array are required.")
        if payload.get("stream"):
            raise HTTPException(400, "This research bridge supports non-streaming requests only.")
        # The hosted agent uses OpenAI's newer field; Ollama accepts max_tokens.
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        limit = payload.get("max_tokens", 2500)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4096:
            raise HTTPException(400, "max_tokens must be between 1 and 4096.")
        payload["max_tokens"] = limit
        payload["stream"] = False
        if busy.locked():
            raise HTTPException(429, "The local model is busy. Try again shortly.", headers={"Retry-After": "5"})
        async with busy:
            return await upstream("POST", "/v1/chat/completions", payload)

    return app
