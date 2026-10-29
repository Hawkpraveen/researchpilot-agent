import asyncio
import json

import httpx
import pytest

from app.config.settings import get_settings
from app.main import app
from app.models.execution import ExecuteResearchRequest
from app.services.research_errors import ResearchError
from app.services.research_executor import execute_research, public_url
from app.services import research_executor

TEXT = "OAuth uses access tokens to grant limited access to protected resources without sharing a password."


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-search-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def request(provider="ollama"):
    return ExecuteResearchRequest.model_validate({
        "jobId": "job_test-123", "contractVersion": "1.0",
        "request": {"goal": "Explain OAuth and give simple steps", "depth": "quick",
                    "criteria": [{"id": "security", "label": "Security", "weight": 5}]},
        "provider": {"mode": "local" if provider == "ollama" else "byok", "provider": provider,
                     "endpoint": "http://localhost:11434" if provider == "ollama" else "https://model.example/v1",
                     "model": "test-model", "apiKey": "test-model-key"},
    })


def draft(excerpt=TEXT, source_id="S1"):
    finding = {"text": "OAuth grants limited access using tokens.", "evidence": [{"sourceId": source_id, "excerpt": excerpt}]}
    return {"summary": [finding], "recommendation": finding, "comparison": [], "uncertainties": []}


def transport(output=None, searches=None, model_provider="ollama"):
    def handle(req):
        body = json.loads(req.content)
        if req.url.host == "api.tavily.com":
            assert req.headers["Authorization"] == "Bearer test-search-key"
            assert body["include_raw_content"] == "text"
            if searches is not None:
                searches.append(body)
            return httpx.Response(200, json={"results": [
                {"url": "https://oauth.net/2/", "title": "OAuth 2", "raw_content": TEXT},
                {"url": "http://127.0.0.1/secrets", "raw_content": TEXT},
            ][:body["max_results"]]})
        assert "test-search-key" not in req.content.decode()
        content = json.dumps(output or draft())
        if model_provider == "ollama":
            assert req.url.path == "/api/chat"
            assert body["stream"] is False
            return httpx.Response(200, json={"message": {"content": content}})
        if model_provider == "anthropic":
            assert req.url.path == "/v1/messages"
            assert req.headers["x-api-key"] == "test-model-key"
            return httpx.Response(200, json={"content": [{"type": "text", "text": content}]})
        assert req.url.path == "/v1/chat/completions"
        assert req.headers["authorization"] == "Bearer test-model-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return httpx.MockTransport(handle)


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["ollama", "openai", "anthropic", "custom"])
async def test_executes_search_and_selected_model_with_citations(provider):
    searches = []
    async with httpx.AsyncClient(transport=transport(searches=searches, model_provider=provider)) as client:
        response = await execute_research(request(provider), client)
    report = response.report
    assert response.researchExecuted is True
    assert report.researchStatus == "completed"
    assert "[S1]" in report.summary
    assert report.sources[0].snippet == TEXT
    assert len(report.sources) == 1  # duplicates and private addresses excluded
    assert report.sources[0].reliability == "low"
    assert "Unknown" in report.comparison[0].values["Findings"]
    assert sum(query["max_results"] for query in searches) <= 5
    assert len(searches) <= 3
    assert searches[0]["query"].startswith("Explain OAuth")
    assert "test-model-key" not in response.model_dump_json()


@pytest.mark.anyio
@pytest.mark.parametrize("output", [draft("This excerpt was invented and does not exist in the source."), draft(source_id="S99")])
async def test_rejects_fabricated_evidence(output):
    async with httpx.AsyncClient(transport=transport(output)) as client:
        with pytest.raises(ResearchError, match="not retrieved") as failure:
            await execute_research(request(), client)
    assert failure.value.code == "UNSUPPORTED_EVIDENCE"


@pytest.mark.anyio
async def test_missing_search_configuration_fails_before_network(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=transport()) as client:
        with pytest.raises(ResearchError) as failure:
            await execute_research(request(), client)
    assert failure.value.code == "SEARCH_NOT_CONFIGURED"


@pytest.mark.anyio
async def test_no_search_results_does_not_generate_report():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []}))) as client:
        with pytest.raises(ResearchError) as failure:
            await execute_research(request(), client)
    assert failure.value.code == "NO_RESEARCH_EVIDENCE"


@pytest.mark.anyio
async def test_provider_auth_error_is_safe():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret diagnostic"))) as client:
        with pytest.raises(ResearchError) as failure:
            await execute_research(request(), client)
    assert failure.value.code == "SEARCH_AUTH_FAILED"
    assert "secret diagnostic" not in str(failure.value)


@pytest.mark.anyio
async def test_route_returns_actionable_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "")
    get_settings.cache_clear()
    payload = request().model_dump(mode="json")
    payload["provider"]["apiKey"] = ""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/v1/research/run", json=payload, headers={"X-Request-ID": "test-req"})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "SEARCH_NOT_CONFIGURED"
    assert result.json()["error"]["requestId"] == "test-req"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://localhost/x", "http://192.168.1.1/", "https://user:pass@example.com/"])
def test_rejects_unsafe_source_urls(url):
    assert public_url(url) is None


@pytest.mark.anyio
async def test_total_time_budget_interrupts_provider_work(monkeypatch):
    plan = research_executor.create_research_plan(request()).model_copy(deep=True)
    plan.budget.timeoutSeconds = 0
    monkeypatch.setattr(research_executor, "create_research_plan", lambda _: plan)

    async def stalled_provider(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(stalled_provider)) as client:
        with pytest.raises(ResearchError) as failure:
            await execute_research(request(), client)
    assert failure.value.code == "RESEARCH_TIMEOUT"


@pytest.mark.anyio
async def test_invalid_model_json_is_not_published():
    searches = transport()

    async def invalid_model(req):
        if req.url.host == "api.tavily.com":
            return await searches.handle_async_request(req)
        return httpx.Response(200, json={"message": {"content": "not JSON"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_model)) as client:
        with pytest.raises(ResearchError) as failure:
            await execute_research(request(), client)
    assert failure.value.code == "MODEL_INVALID_RESPONSE"
