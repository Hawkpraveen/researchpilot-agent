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


def test_discovery_removes_boilerplate_without_redefining_subjects():
    assert research_executor.discovery_terms("research about Acme and give report to me") == "Acme"
    assert research_executor.discovery_terms("research about Entra and how to connect to it") == "Entra and how to connect to it"
    assert research_executor.discovery_terms("Explain OAuth and give simple steps") == "Explain OAuth and give simple steps"


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


def transport(output=None, searches=None, model_provider="ollama", resolution=None):
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
        is_resolution = "Identify the subject" in (body.get("system", "") or body.get("messages", [{}])[0].get("content", ""))
        resolved = resolution or {"status": "resolved", "subject": "OAuth authorization", "explanation": "The question and sources identify OAuth.", "candidates": [], "selectedSourceIds": ["S1"]}
        content = json.dumps(resolved if is_resolution else output or draft())
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
    assert report.researchStatus == "partial"
    assert report.resolution.subject == "OAuth authorization"
    assert "[S1]" in report.summary
    assert report.sources[0].snippet == TEXT
    assert len(report.sources) == 1  # duplicates and private addresses excluded
    assert report.sources[0].reliability == "low"
    assert "Unknown" in report.comparison[0].values["Findings"]
    assert sum(query["max_results"] for query in searches) <= 5
    assert len(searches) <= 3
    assert searches[0]["query"].startswith("Explain OAuth")
    assert "test-model-key" not in response.model_dump_json()
    assert response.usage["executionId"]
    assert len(response.usage["calls"]) == len(searches) + 2


@pytest.mark.anyio
@pytest.mark.parametrize("output", [draft("This excerpt was invented and does not exist in the source."), draft(source_id="S99")])
async def test_omits_fabricated_evidence_and_returns_labeled_source_excerpts(output):
    async with httpx.AsyncClient(transport=transport(output)) as client:
        response = await execute_research(request(), client)
    assert "not a synthesized answer" in response.report.summary
    assert TEXT in response.report.summary
    assert "S99" not in response.report.summary
    assert "invented" not in response.report.summary
    assert "no model recommendation passed" in response.report.recommendation
    assert any("Omitted" in item for item in response.report.uncertainties)
    assert response.report.sources[0].snippet == TEXT


@pytest.mark.anyio
async def test_keeps_valid_findings_when_another_finding_has_invalid_evidence():
    output = draft()
    output["summary"].append(draft("This fabricated passage was not retrieved.")["summary"][0])
    async with httpx.AsyncClient(transport=transport(output)) as client:
        response = await execute_research(request(), client)
    assert "OAuth grants limited access using tokens." in response.report.summary
    assert "not a synthesized answer" not in response.report.summary
    assert any("Omitted 1" in item for item in response.report.uncertainties)


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
    assert failure.value.usage["calls"]


@pytest.mark.anyio
async def test_ambiguous_subject_returns_choices_without_generating_findings(monkeypatch):
    from app.models.execution import SubjectResolution
    calls = []

    async def resolve(*args):
        return SubjectResolution.model_validate({"status": "ambiguous", "subject": "", "explanation": "The name can refer to a company or a clinical score.",
            "candidates": [{"label": "Example company", "description": "A software business", "sourceIds": ["S1"]},
                           {"label": "Example score", "description": "A clinical scale", "sourceIds": ["S2"]}], "selectedSourceIds": []})

    async def synthesize(*args):
        calls.append(True)
        raise AssertionError("Ambiguous subjects must not generate a report")

    monkeypatch.setattr(research_executor, "resolve_subject", resolve)
    monkeypatch.setattr(research_executor, "synthesize", synthesize)
    def search(req):
        return httpx.Response(200, json={"results": [
            {"url": "https://company.example/", "title": "Company", "content": "A company that develops enterprise software products and tools."},
            {"url": "https://clinical.example/", "title": "Clinical scale", "content": "A clinical scoring system used to assess diagnostic imaging quality."}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(search)) as client:
        response = await execute_research(request(), client)
    assert response.report.researchStatus == "needs_clarification"
    assert len(response.report.resolution.candidates) == 2
    assert response.report.comparison == []
    assert not calls


@pytest.mark.anyio
async def test_context_resolves_subject_and_excludes_other_meaning_from_model(monkeypatch):
    from app.models.execution import SubjectResolution, ResearchDraft
    inputs = []
    searches = []
    async def resolve(_client, _provider, prompt):
        assert "software company" in prompt
        return SubjectResolution(status="resolved", subject="Example software company", explanation="User context specifies the company.", candidates=[], selectedSourceIds=["S1"])
    async def synthesize(_client, _provider, prompt):
        inputs.append(json.loads(prompt))
        return ResearchDraft.model_validate(draft())
    monkeypatch.setattr(research_executor, "resolve_subject", resolve)
    monkeypatch.setattr(research_executor, "synthesize", synthesize)
    def search(req):
        searches.append(json.loads(req.content))
        return httpx.Response(200, json={"results": [
            {"url": "https://company.example/", "title": "Company", "content": TEXT},
            {"url": "https://clinical.example/", "title": "Clinical scale", "content": "Unrelated clinical scoring system used to assess diagnostic imaging quality."}]})
    payload = request()
    payload.request.depth = "standard"
    payload.request.constraints = ["Research the software company"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(search)) as client:
        response = await execute_research(payload, client)
    assert inputs[0]["resolvedSubject"] == "Example software company"
    assert all(source["id"] != "S2" for source in inputs[0]["sources"])
    assert "Example software company" in searches[-1]["query"]
    assert sum(item["max_results"] for item in searches) <= 15
    assert response.report.resolution.status == "resolved"


@pytest.mark.anyio
async def test_unresolved_subject_requests_context_instead_of_inventing_a_meaning():
    unresolved = {"status": "unresolved", "subject": "", "explanation": "Search results do not establish the requested subject.", "candidates": [], "selectedSourceIds": []}
    async with httpx.AsyncClient(transport=transport(resolution=unresolved)) as client:
        response = await execute_research(request(), client)
    assert response.report.researchStatus == "needs_clarification"
    assert response.report.resolution.candidates == []


@pytest.mark.anyio
async def test_resolver_cannot_invent_source_references():
    invalid = {"status": "resolved", "subject": "Invented", "explanation": "Unsupported", "candidates": [], "selectedSourceIds": ["S99"]}
    async with httpx.AsyncClient(transport=transport(resolution=invalid)) as client:
        with pytest.raises(ResearchError) as failure:
            await execute_research(request(), client)
    assert failure.value.code == "MODEL_INVALID_RESPONSE"


@pytest.mark.anyio
async def test_adequate_multi_source_findings_can_complete_without_clarification(monkeypatch):
    from app.models.execution import SubjectResolution, ResearchDraft
    other = "OAuth clients request a limited scope and exchange authorization codes for access tokens."
    async def resolve(*args):
        return SubjectResolution(status="resolved", subject="OAuth", explanation="The results consistently describe OAuth authorization.", candidates=[], selectedSourceIds=["S1", "S2"])
    async def synthesize(*args):
        output = draft()
        output["summary"] = [draft()["summary"][0], draft(other, "S2")["summary"][0], draft()["summary"][0]]
        output["summary"][1]["text"] = "Clients exchange authorization codes for tokens."
        output["summary"][2]["text"] = "OAuth avoids sharing the user's password with the client."
        return ResearchDraft.model_validate(output)
    monkeypatch.setattr(research_executor, "resolve_subject", resolve)
    monkeypatch.setattr(research_executor, "synthesize", synthesize)
    payload = request()
    payload.request.criteria = []
    def search(req):
        return httpx.Response(200, json={"results": [
            {"url": "https://oauth.net/2/", "title": "OAuth", "content": TEXT},
            {"url": "https://auth.example/docs", "title": "OAuth flow", "content": other}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(search)) as client:
        response = await execute_research(payload, client)
    assert response.report.researchStatus == "completed"
    assert len(response.report.sources) == 2
    assert response.report.resolution.status == "resolved"


@pytest.mark.anyio
async def test_short_name_cannot_silently_ignore_matching_business_results(monkeypatch):
    from app.models.execution import SubjectResolution
    async def resolve(*args):
        return SubjectResolution(status="resolved", subject="ACME scoring scale", explanation="A diagnostic scoring scale.", candidates=[], selectedSourceIds=["S1"])
    monkeypatch.setattr(research_executor, "resolve_subject", resolve)
    def search(req):
        return httpx.Response(200, json={"results": [
            {"url": "https://clinical.example/scale", "title": "ACME scoring scale", "content": "ACME is a standardized clinical scoring scale used for imaging quality."},
            {"url": "https://business.example/acme", "title": "Acme - Company Profile and Products", "content": "Acme is a company providing business software for enterprise sales teams."}]})
    payload = request()
    payload.request.goal = "research about Acme and give report to me"
    async with httpx.AsyncClient(transport=httpx.MockTransport(search)) as client:
        response = await execute_research(payload, client)
    assert response.report.researchStatus == "needs_clarification"
    assert len(response.report.resolution.candidates) == 2
    assert response.report.resolution.selectedSourceIds == []


@pytest.mark.anyio
async def test_selected_subject_continues_saved_discovery_without_resolving_or_searching_again(monkeypatch):
    from app.models.execution import ResearchDraft, ResumeDiscovery, ResearchSource
    async def resolve(*args):
        raise AssertionError("A user-selected subject must not be resolved again")
    async def synthesize(_client, _provider, prompt):
        payload = json.loads(prompt)
        assert payload["resolvedSubject"] == "OAuth"
        assert payload["sources"][0]["id"] == "S7"
        return ResearchDraft.model_validate(draft(source_id="S7"))
    monkeypatch.setattr(research_executor, "resolve_subject", resolve)
    monkeypatch.setattr(research_executor, "synthesize", synthesize)
    payload = request()
    payload.resume = ResumeDiscovery(subject="OAuth", context="The authorization protocol", consumedPages=5,
        sources=[ResearchSource(id="S7", title="Saved OAuth source", domain="oauth.net", url="https://oauth.net/2/", accessedAt="2026-10-29T00:00:00Z", snippet=TEXT)])
    def network(_):
        raise AssertionError("Exhausted discovery budget must not be searched again")
    async with httpx.AsyncClient(transport=httpx.MockTransport(network)) as client:
        response = await execute_research(payload, client)
    assert response.jobId == payload.jobId
    assert response.report.resolution.subject == "OAuth"
    assert response.report.sources[0].id == "S7"


@pytest.mark.anyio
async def test_resumed_search_uses_remaining_budget_and_does_not_collide_with_saved_source_ids(monkeypatch):
    from app.models.execution import ResearchDraft, ResumeDiscovery, ResearchSource
    searches = []
    async def synthesize(_client, _provider, prompt):
        ids = [source["id"] for source in json.loads(prompt)["sources"]]
        assert "S7" in ids and "S8" in ids
        assert len(ids) == len(set(ids))
        return ResearchDraft.model_validate(draft(source_id="S7"))
    monkeypatch.setattr(research_executor, "synthesize", synthesize)
    payload = request()
    payload.request.depth = "standard"
    payload.resume = ResumeDiscovery(subject="OAuth", context="Authorization protocol", consumedPages=5,
        sources=[ResearchSource(id="S7", title="Saved source", domain="oauth.net", url="https://oauth.net/2/", accessedAt="2026-10-29T00:00:00Z", snippet=TEXT)])
    def network(req):
        query = json.loads(req.content)
        searches.append(query)
        return httpx.Response(200, json={"results": [{"url": "https://new.example/docs", "title": "More OAuth evidence", "content": TEXT}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(network)) as client:
        await execute_research(payload, client)
    assert searches
    assert all("OAuth" in item["query"] for item in searches)
    assert sum(item["max_results"] for item in searches) + 5 <= 15
