import asyncio
import ipaddress
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config.settings import get_settings
from app.models.execution import (
    ExecuteResearchRequest, ExecuteResearchResponse, Finding, ResearchReport, ResearchSource,
    ComparisonRow, SubjectCandidate, SubjectResolution,
)
from app.services.research_errors import ResearchError
from app.services.research_planner import create_research_plan
from app.services.research_providers import search_web, synthesize, resolve_subject
from app.models.research import ResearchPlanResponse
from app.services.usage_ledger import usage_scope

logger = logging.getLogger(__name__)


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


def discovery_terms(goal: str) -> str:
    # Remove request boilerplate, never replace or spell-correct the subject itself.
    value = re.sub(r"^(?:please\s+)?(?:research(?:\s+(?:about|on))?|tell\s+me\s+about|give\s+me\s+(?:an?\s+)?(?:report|overview)\s+(?:on|about))\s+", "", goal.strip(), flags=re.I)
    value = re.sub(r"\s+(?:and\s+)?(?:give|send|provide|write|create)\s+(?:me\s+)?(?:an?\s+)?report\b.*$", "", value, flags=re.I)
    return value.strip() or goal


async def execute_research(request: ExecuteResearchRequest, client: httpx.AsyncClient | None = None) -> ExecuteResearchResponse:
    with usage_scope(request.jobId, request.executionId) as ledger:
        try:
            result = await _execute_research(request, client)
            result.usage = ledger.snapshot()
            return result
        except Exception as error:
            error.usage = ledger.snapshot()
            raise


async def _execute_research(request: ExecuteResearchRequest, client: httpx.AsyncClient | None = None) -> ExecuteResearchResponse:
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
    context = " ".join(request.request.constraints + ([request.resume.context] if request.resume else []))
    next_source = 0
    if request.resume:
        logger.info("[research] job=%s stage=resuming_discovery sources=%s", request.jobId, len(request.resume.sources))
        remaining = max(0, remaining - request.resume.consumedPages)
        for source in request.resume.sources:
            if not public_url(source.url) or not re.fullmatch(r"S[1-9][0-9]*", source.id) or source.id in sources:
                raise ResearchError("MODEL_INVALID_RESPONSE", "Saved discovery contains an invalid source.")
            sources[source.id] = source
            contents[source.id] = normalize(source.snippet)
            seen.add(source.url)
            next_source = max(next_source, int(source.id[1:]))
    alternatives = "; ".join(request.request.alternatives)

    async def collect(query: str, limit: int):
        nonlocal remaining, next_source
        limit = min(limit, remaining)
        if limit <= 0:
            return
        logger.info("[research] job=%s stage=search slots=%s", request.jobId, limit)
        results = await search_web(client, search_key, query.strip(), limit)
        remaining -= limit
        for result in results:
            url = public_url(result.get("url", ""))
            content = result.get("content") or result.get("raw_content") or ""
            if not url or url in seen or not isinstance(content, str) or len(normalize(content)) < 40:
                continue
            seen.add(url)
            next_source += 1
            source_id = f"S{next_source}"
            contents[source_id] = normalize(content)[:1200 if request.provider.provider == "ollama" else 2400]
            sources[source_id] = ResearchSource(
                id=source_id, title=str(result.get("title") or url)[:300],
                domain=urlsplit(url).hostname or "", url=url,
                accessedAt=datetime.now(timezone.utc).isoformat(),
                snippet=contents[source_id][:500], reliability="low",
            )

    # Discover meanings before generic form criteria can bias the topic.
    terms = discovery_terms(request.request.goal)
    if not request.resume:
        await collect(f"{terms} {context} {alternatives}", min(2, remaining))
        await collect(f'{terms} {context} official website company product', min(2, remaining))
        await collect(f'{terms} {context} meaning definition concept', min(1, remaining))
    elif not request.resume.subject:
        await collect(f"{terms} {context} {alternatives}", min(3, remaining))
    if not sources:
        raise ResearchError("NO_RESEARCH_EVIDENCE", "Web search returned no usable source content. Try a more specific research question.", True)
    logger.info("[research] job=%s stage=resolving_subject sources=%s", request.jobId, len(sources))
    if request.resume and request.resume.subject:
        resolution = SubjectResolution(status="resolved", subject=request.resume.subject,
            explanation="You selected this subject from the saved discovery results.", candidates=[], selectedSourceIds=list(sources))
    else:
        resolution = await resolve_subject(client, request.provider, json.dumps({
            "goal": request.request.goal, "context": context,
            "alternatives": request.request.alternatives,
            "sources": [{"id": sid, "title": source.title, "url": source.url, "content": contents[sid][:500]} for sid, source in sources.items()],
        }))
    referenced = resolution.selectedSourceIds + [sid for candidate in resolution.candidates for sid in candidate.sourceIds]
    if any(sid not in sources for sid in referenced) or (resolution.status == "resolved" and (not resolution.subject or not resolution.selectedSourceIds)):
        raise ResearchError("MODEL_INVALID_RESPONSE", "The subject resolver returned unsupported source references.")
    if resolution.status == "ambiguous" and len(resolution.candidates) < 2:
        resolution.status = "unresolved"
    # A small local model can overlook company results when a similarly named concept dominates.
    # For an unqualified short name, require clarification rather than hide matching business evidence.
    business_words = r"\b(?:company|business|platform|software|products?|organization|organisation|vendor)\b"
    subject_key = re.sub(r"\W", "", terms).casefold()
    unqualified = len(terms.split()) <= 3 and not context.strip() and not request.request.alternatives and not request.resume
    selected_description = " ".join([resolution.subject, resolution.explanation] + [candidate.description for candidate in resolution.candidates if set(candidate.sourceIds) & set(resolution.selectedSourceIds)])
    if unqualified and subject_key and resolution.status == "resolved" and not re.search(business_words, selected_description, re.I):
        alternatives_found = [(sid, source) for sid, source in sources.items()
            if sid not in resolution.selectedSourceIds
            and subject_key in re.sub(r"\W", "", source.title).casefold()
            and re.search(business_words, source.title, re.I)]
        if alternatives_found:
            chosen = SubjectCandidate(label=resolution.subject[:150], description=resolution.explanation[:500], sourceIds=resolution.selectedSourceIds)
            sid, other = alternatives_found[0]
            resolution.candidates = [chosen, SubjectCandidate(label=other.title[:150], description=contents[sid][:500], sourceIds=[sid])]
            resolution.status = "ambiguous"
            resolution.explanation = "Search found both a conceptual meaning and matching company or product results. The question does not indicate which one is intended."
            resolution.selectedSourceIds = []
            resolution.subject = ""
    if resolution.status != "resolved":
        logger.info("[research] job=%s status=needs_clarification candidates=%s", request.jobId, len(resolution.candidates))
        refs = " ".join(f"[{sid}]" for sid in sources)
        return ExecuteResearchResponse(jobId=request.jobId, report=ResearchReport(
            researchStatus="needs_clarification", resolution=resolution,
            summary=f"Which subject did you mean? Search results did not establish one clear meaning. {refs}",
            recommendation=f"Choose a meaning or add a little context so the research can continue. {refs}",
            alternatives=[], comparison=[], sources=list(sources.values()),
            uncertainties=["The research subject is not resolved. No final findings or recommendation have been generated."],
        ))
    logger.info("[research] job=%s stage=subject_resolved subject=%s", request.jobId, resolution.subject)
    # Exclude discovery results about other meanings from synthesis.
    selected = set(resolution.selectedSourceIds)
    resolution.candidates = [candidate for candidate in resolution.candidates if set(candidate.sourceIds).issubset(selected)]
    sources = {sid: source for sid, source in sources.items() if sid in selected}
    contents = {sid: text for sid, text in contents.items() if sid in selected}
    queries = [f"{resolution.subject}: {task.question} {context} {alternatives}" for task in plan.tasks]
    for index, query in enumerate(queries[:max(0, plan.budget.maxSubtasks - 3)]):
        slots_left = max(1, min(len(queries), plan.budget.maxSubtasks - 3) - index)
        await collect(query, min(5, max(1, remaining // slots_left)))

    logger.info("[research] job=%s stage=model_generation model=%s sources=%s", request.jobId, request.provider.model, len(sources))
    model_sources = list(sources.items())
    if request.provider.provider == "ollama":
        diverse = []
        remaining_sources = []
        domains = set()
        for sid, source in model_sources:
            if source.domain not in domains:
                diverse.append((sid, source))
                domains.add(source.domain)
            else:
                remaining_sources.append((sid, source))
        model_sources = (diverse + remaining_sources)[:8]
    draft = await synthesize(client, request.provider, json.dumps({
        "request": request.request.model_dump(),
        "resolvedSubject": resolution.subject,
        "subjectResolution": resolution.explanation,
        "instructions": "For general technical questions answer with explanation and numbered steps. Use alternative 'Findings' when none are supplied. Omit comparisons without evidence; unknown cells are added by the application.",
        "sources": [{"id": sid, "url": source.url, "title": source.title, "content": contents[sid]} for sid, source in model_sources],
    }))
    logger.info("[research] job=%s stage=validating_evidence", request.jobId)
    used: dict[str, list[str]] = {}

    rejected_findings = 0

    def validate_finding(finding: Finding) -> str | None:
        nonlocal rejected_findings
        ids = []
        checked = []
        for evidence in finding.evidence:
            excerpt = normalize(evidence.excerpt)
            if evidence.sourceId not in contents or excerpt not in contents[evidence.sourceId]:
                rejected_findings += 1
                logger.warning("[research] job=%s stage=validating_evidence rejected_finding=true reason=excerpt_mismatch", request.jobId)
                return None
            ids.append(evidence.sourceId)
            checked.append((evidence.sourceId, excerpt))
        for sid, excerpt in checked:
            used.setdefault(sid, []).append(excerpt)
        # Citation tokens come from validated provenance, never model-written references.
        text = re.sub(r"\[S\d+\]", "", finding.text).strip()
        return f"{text} {' '.join(f'[{sid}]' for sid in dict.fromkeys(ids))}"

    validated_summary = list(dict.fromkeys(text for finding in draft.summary if (text := validate_finding(finding))))
    summary = "\n\n".join(validated_summary)
    if not summary:
        logger.warning("[research] job=%s stage=extractive_fallback reason=no_valid_summary", request.jobId)
        excerpts = []
        for sid in list(sources)[:3]:
            excerpt = contents[sid][:500]
            used.setdefault(sid, []).append(excerpt)
            excerpts.append(f"Retrieved excerpt from {sources[sid].title}:\n{excerpt} [{sid}]")
        summary = "The generated summary could not be validated. These are excerpts from retrieved sources, not a synthesized answer.\n\n" + "\n\n".join(excerpts)
    recommendation = validate_finding(draft.recommendation)
    if recommendation is None:
        recommendation = f"Review the retrieved source excerpts before deciding; no model recommendation passed evidence checks. [{next(iter(used))}]"
    labels = request.request.alternatives or ["Findings"]
    comparisons: dict[tuple[str, str], str] = {}
    criterion_ids = {criterion.id for criterion in request.request.criteria}
    for item in draft.comparison:
        key = (item.criterionId, item.alternative)
        if item.criterionId not in criterion_ids or item.alternative not in labels or key in comparisons:
            raise ResearchError("MODEL_INVALID_RESPONSE", "The model returned an invalid comparison.")
        value = validate_finding(item.finding)
        if value is not None:
            comparisons[key] = value
    uncertainties = list(draft.uncertainties)
    if rejected_findings:
        uncertainties.append(f"Omitted {rejected_findings} model findings with invalid source references or excerpts. The report may be incomplete.")
    rows = []
    coverage_incomplete = False
    for criterion in request.request.criteria:
        values = {label: comparisons.get((criterion.id, label), "Unknown: insufficient evidence within the research budget.") for label in labels}
        rows.append(ComparisonRow(criterion=criterion.label, values=values))
        if any((criterion.id, label) not in comparisons for label in labels):
            coverage_incomplete = True
            uncertainties.append(f"Incomplete evidence for {criterion.label}.")
    uncertainties.extend([
        "Source excerpts and citation identities were checked against retrieved text; factual correctness and claim entailment have not been independently verified.",
        "Source reliability is unassessed. Publication dates may be unavailable; retrieval timestamps are recorded.",
        "This run uses a bounded first-pass search; automated gap follow-up and independent contradiction checking are not implemented.",
    ])
    for sid, excerpts in used.items():
        sources[sid].snippet = " … ".join(dict.fromkeys(excerpts))[:2000]
    report = ResearchReport(
        researchStatus="partial" if rejected_findings or coverage_incomplete or len(used) < 2 or len(validated_summary) < 3 or summary.startswith("The generated summary could not") else "completed",
        resolution=resolution,
        summary=summary, recommendation=recommendation, alternatives=[], comparison=rows,
        sources=[source for sid, source in sources.items() if sid in used or sid in selected], uncertainties=list(dict.fromkeys(uncertainties)),
    )
    return ExecuteResearchResponse(jobId=request.jobId, report=report)
