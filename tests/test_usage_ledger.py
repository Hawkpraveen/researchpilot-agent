import asyncio
import httpx
import pytest
from app.services.usage_ledger import usage_scope, record_call
from app.services.research_providers import post_json, search_web
from app.services.research_errors import ResearchError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("provider,body,expected", [
    ("ollama", {"prompt_eval_count": 20, "eval_count": 8}, (20, 8, 28)),
    ("openai", {"usage": {"prompt_tokens": 20, "completion_tokens": 8, "prompt_tokens_details": {"cached_tokens": 10}}}, (20, 8, 28)),
    ("anthropic", {"usage": {"input_tokens": 20, "output_tokens": 8, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}}, (35, 8, 43)),
    ("custom", {}, (None, None, None)),
])
def test_counts_reported_tokens_without_inventing_missing_usage(provider, body, expected):
    with usage_scope("job_usage", "attempt-1") as ledger:
        record_call(provider, "synthesis", "model", body, 200, 14)
        call = ledger.calls[0]
        assert (call["inputTokens"], call["outputTokens"], call["totalTokens"]) == expected
        assert "apiKey" not in str(ledger.snapshot())


@pytest.mark.anyio
async def test_tavily_requests_actual_usage_and_does_not_count_results_as_credits():
    def handle(request):
        import json
        assert json.loads(request.content)["include_usage"] is True
        return httpx.Response(200, json={"usage": {"credits": 1}, "results": [{"url": "https://example.com"}]})
    with usage_scope("job_usage", "attempt-2") as ledger:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await search_web(client, "private-key", "private-query", 5)
        assert ledger.calls[0]["credits"] == 1
        assert "private-key" not in str(ledger.snapshot())
        assert "private-query" not in str(ledger.snapshot())


@pytest.mark.anyio
async def test_timeout_retains_prior_usage_and_marks_unreturned_call_unknown():
    async def timeout(_):
        raise httpx.ReadTimeout("sensitive provider details")
    with usage_scope("job_usage", "attempt-3") as ledger:
        record_call("tavily", "search", None, {"usage": {"credits": 1}}, 200, 10)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(ResearchError):
                await post_json(client, "http://model.example/chat", {"model": "test"}, {}, "MODEL", "ollama", "synthesis")
        assert ledger.calls[0]["credits"] == 1
        assert ledger.calls[1]["totalTokens"] is None
        assert ledger.calls[1]["status"] == "failed"
        assert "sensitive" not in str(ledger.snapshot())


@pytest.mark.anyio
async def test_request_contexts_do_not_mix_usage():
    async def work(execution_id, amount):
        with usage_scope("job_usage", execution_id) as ledger:
            await asyncio.sleep(0)
            record_call("ollama", "synthesis", "test", {"prompt_eval_count": amount, "eval_count": 1}, 200, 1)
            return ledger.snapshot()
    first, second = await asyncio.gather(work("a", 10), work("b", 40))
    assert first["calls"][0]["inputTokens"] == 10
    assert second["calls"][0]["inputTokens"] == 40
