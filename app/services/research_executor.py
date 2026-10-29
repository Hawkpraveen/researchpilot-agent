import asyncio
import ipaddress
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config.settings import get_settings
from app.models.execution import (
    ExecuteResearchRequest, ExecuteResearchResponse, Finding, ResearchReport, ResearchSource,
    ComparisonRow,
)
from app.services.research_errors import ResearchError
from app.services.research_planner import create_research_plan
from app.services.research_providers import search_web, synthesize
from app.models.research import ResearchPlanResponse


def public_url(value: str) -> str | None:
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme not in {"http", "https"} or not host or url.username or url.password:
            return None
        if host == "localhost" or "." not in host or host.endswith((".local", ".internal", ".localhost")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return urlunsplit((url.scheme, url.netloc.lower(), url.path or "/", url.query, ""))
    except (ValueError, TypeError):
        return None


def normalize(value: str) -> str:
    return " ".join(value.split())


async def execute_research(request: ExecuteResearchRequest, client: httpx.AsyncClient | None = None) -> ExecuteResearchResponse:
    settings = get_settings()
    if not settings.tavily_api_key:
        raise ResearchError("SEARCH_NOT_CONFIGURED", "Set TAVILY_API_KEY in the Python agent's .env file, then restart the agent.", status=503)
    if request.provider.provider in {"openai", "anthropic"} and not request.provider.apiKey.get_secret_value():
        raise ResearchError("PROVIDER_NOT_CONFIGURED", "Save your model API key in Settings before starting research.", status=503)
    plan = create_research_plan(request)
    try:
        async with asyncio.timeout(plan.budget.timeoutSeconds):
            if client is not None:
                return await run_research(request, plan, client, settings.tavily_api_key)
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), follow_redirects=False) as owned_client:
                return await run_research(request, plan, owned_client, settings.tavily_api_key)
    except TimeoutError as error:
        raise ResearchError("RESEARCH_TIMEOUT", "Research exceeded its time budget. Try a smaller question or a larger depth budget.", True, 504) from error


async def run_research(request: ExecuteResearchRequest, plan: ResearchPlanResponse, client: httpx.AsyncClient, search_key: str) -> ExecuteResearchResponse:
    sources: dict[str, ResearchSource] = {}
    contents: dict[str, str] = {}
    seen: set[str] = set()
    remaining = plan.budget.maxPages
    queries = [task.question for task in plan.tasks]
    # A separate overview query keeps general requests (for example OAuth steps) central
    # even when the form also supplies vendor-comparison criteria.
    queries = [request.request.goal] + queries[:max(0, plan.budget.maxSubtasks - 1)]
    alternatives = "; ".join(request.request.alternatives)
    for index, query in enumerate(queries):
        if remaining <= 0:
            break
        limit = min(5, max(1, remaining // (len(queries) - index)))
        results = await search_web(client, search_key, f"{query} {alternatives}".strip(), limit)
        remaining -= limit  # Count attempted result slots, including duplicates and rejected URLs.
        for result in results:
            url = public_url(result.get("url", ""))
            content = result.get("raw_content") or result.get("content") or ""
            if not url or url in seen or not isinstance(content, str) or len(normalize(content)) < 40:
                continue
            seen.add(url)
            source_id = f"S{len(sources) + 1}"
            contents[source_id] = normalize(content)[:2400]
            sources[source_id] = ResearchSource(
                id=source_id, title=str(result.get("title") or url)[:300],
                domain=urlsplit(url).hostname or "", url=url,
                accessedAt=datetime.now(timezone.utc).isoformat(),
                snippet=contents[source_id][:500], reliability="low",
            )
    if not sources:
        raise ResearchError("NO_RESEARCH_EVIDENCE", "Web search returned no usable source content. Try a more specific research question.", True)

    draft = await synthesize(client, request.provider, json.dumps({
        "request": request.request.model_dump(),
        "instructions": "For general technical questions answer with explanation and numbered steps. Use alternative 'Findings' when none are supplied. Omit comparisons without evidence; unknown cells are added by the application.",
        "sources": [{"id": sid, "url": source.url, "title": source.title, "content": contents[sid]} for sid, source in sources.items()],
    }))
    used: dict[str, list[str]] = {}

    def validate_finding(finding: Finding) -> str:
        ids = []
        for evidence in finding.evidence:
            excerpt = normalize(evidence.excerpt)
            if evidence.sourceId not in contents or excerpt not in contents[evidence.sourceId]:
                raise ResearchError("UNSUPPORTED_EVIDENCE", "The model cited a source or excerpt that was not retrieved. No report was published.", True)
            ids.append(evidence.sourceId)
            used.setdefault(evidence.sourceId, []).append(excerpt)
        # Citation tokens come from validated provenance, never model-written references.
        text = re.sub(r"\[S\d+\]", "", finding.text).strip()
        return f"{text} {' '.join(f'[{sid}]' for sid in dict.fromkeys(ids))}"

    summary = "\n\n".join(validate_finding(finding) for finding in draft.summary)
    recommendation = validate_finding(draft.recommendation)
    labels = request.request.alternatives or ["Findings"]
    comparisons: dict[tuple[str, str], str] = {}
    criterion_ids = {criterion.id for criterion in request.request.criteria}
    for item in draft.comparison:
        key = (item.criterionId, item.alternative)
        if item.criterionId not in criterion_ids or item.alternative not in labels or key in comparisons:
            raise ResearchError("MODEL_INVALID_RESPONSE", "The model returned an invalid comparison.")
        comparisons[key] = validate_finding(item.finding)
    uncertainties = list(draft.uncertainties)
    rows = []
    for criterion in request.request.criteria:
        values = {label: comparisons.get((criterion.id, label), "Unknown: insufficient evidence within the research budget.") for label in labels}
        rows.append(ComparisonRow(criterion=criterion.label, values=values))
        if any((criterion.id, label) not in comparisons for label in labels):
            uncertainties.append(f"Incomplete evidence for {criterion.label}.")
    uncertainties.extend([
        "Source excerpts and citation identities were checked against retrieved text; factual correctness and claim entailment have not been independently verified.",
        "Source reliability is unassessed. Publication dates may be unavailable; retrieval timestamps are recorded.",
        "This run uses a bounded first-pass search; automated gap follow-up and independent contradiction checking are not implemented.",
    ])
    for sid, excerpts in used.items():
        sources[sid].snippet = " … ".join(dict.fromkeys(excerpts))[:2000]
    report = ResearchReport(
        summary=summary, recommendation=recommendation, alternatives=[], comparison=rows,
        sources=[sources[sid] for sid in used], uncertainties=list(dict.fromkeys(uncertainties)),
    )
    return ExecuteResearchResponse(jobId=request.jobId, report=report)
