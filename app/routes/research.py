from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.execution import ExecuteResearchRequest, ExecuteResearchResponse
from app.services.research_executor import execute_research
from app.services.research_errors import ResearchError

from app.models.research import CreateResearchPlanRequest, ResearchPlanResponse
from app.services.research_planner import create_research_plan

router = APIRouter(prefix="/research", tags=["research"])


@router.post("/plan", response_model=ResearchPlanResponse)
async def plan_research(request: CreateResearchPlanRequest) -> ResearchPlanResponse:
    return create_research_plan(request)


@router.post("/run", response_model=ExecuteResearchResponse)
async def run_research(payload: ExecuteResearchRequest, request: Request):
    try:
        return await execute_research(payload)
    except ResearchError as error:
        return JSONResponse(status_code=error.status, content={"error": {
            "code": error.code, "message": error.message, "retryable": error.retryable,
            "requestId": getattr(request.state, "request_id", ""),
        }})
