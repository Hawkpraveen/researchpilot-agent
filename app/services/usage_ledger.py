"""Request-local provider counters, independent of report parsing and success."""
import json
import logging
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
_current = ContextVar("research_usage", default=None)


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


@dataclass
class UsageLedger:
    job_id: str
    execution_id: str
    calls: list = field(default_factory=list)

    def snapshot(self):
        return {"executionId": self.execution_id, "calls": list(self.calls)}


@contextmanager
def usage_scope(job_id, execution_id):
    ledger = UsageLedger(job_id, execution_id)
    token = _current.set(ledger)
    try:
        yield ledger
    finally:
        _current.reset(token)


def record_call(provider, stage, model, body, http_status, duration_ms):
    ledger = _current.get()
    if ledger is None:
        return
    body = body or {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    cached = written = None
    if provider == "ollama":
        input_tokens, output_tokens = number(body.get("prompt_eval_count")), number(body.get("eval_count"))
    elif provider == "anthropic":
        base = number(usage.get("input_tokens"))
        cached, written = number(usage.get("cache_read_input_tokens")), number(usage.get("cache_creation_input_tokens"))
        input_tokens = base + (cached or 0) + (written or 0) if base is not None else None
        output_tokens = number(usage.get("output_tokens"))
    else:
        input_tokens, output_tokens = number(usage.get("prompt_tokens")), number(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details") or {}
        cached = number(details.get("cached_tokens")) if isinstance(details, dict) else None
    call = {"provider": provider, "stage": stage, "model": model,
        "httpStatus": http_status, "status": "completed" if http_status is not None and 200 <= http_status < 300 else "failed",
        "durationMs": max(0, round(duration_ms)), "inputTokens": input_tokens, "outputTokens": output_tokens,
        "cachedInputTokens": cached, "cacheWriteTokens": written,
        "totalTokens": input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None,
        "credits": number(usage.get("credits")) if provider == "tavily" else None}
    ledger.calls.append(call)
    logger.info("[usage] %s", json.dumps({"jobId": ledger.job_id, "executionId": ledger.execution_id, **call}))
