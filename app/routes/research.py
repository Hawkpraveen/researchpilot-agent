import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.execution import ExecuteResearchRequest, ExecuteResearchResponse
from app.services.research_executor import execute_research
from app.services.research_errors import ResearchError

from app.models.research import CreateResearchPlanRequest, ResearchPlanResponse
from app.services.research_planner import create_research_plan

router = APIRouter(prefix="/research", tags=["research"])
logger = logging.getLogger(__name__)


@router.post("/plan", response_model=ResearchPlanResponse)
async def plan_research(request: CreateResearchPlanRequest) -> ResearchPlanResponse:
    return create_research_plan(request)


@router.post("/run", response_model=ExecuteResearchResponse)
async def run_research(payload: ExecuteResearchRequest, request: Request):
    started = time.monotonic()
    logger.info("[research] job=%s status=started provider=%s model=%s", payload.jobId, payload.provider.provider, payload.provider.model)
    try:
        result = await execute_research(payload)
        logger.info("[research] job=%s status=completed elapsed=%.1fs sources=%s", payload.jobId, time.monotonic() - started, len(result.report.sources))
        return result
    except ResearchError as error:
        logger.error("[research] job=%s status=failed code=%s http=%s elapsed=%.1fs message=%s", payload.jobId, error.code, error.status, time.monotonic() - started, error.message)
        return JSONResponse(status_code=error.status, content={"usage": getattr(error, "usage", None), "error": {
            "code": error.code, "message": error.message, "retryable": error.retryable,
            "requestId": getattr(request.state, "request_id", ""),
        }})
