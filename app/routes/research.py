from fastapi import APIRouter

from app.models.research import CreateResearchPlanRequest, ResearchPlanResponse
from app.services.research_planner import create_research_plan

router = APIRouter(prefix="/research", tags=["research"])


@router.post("/plan", response_model=ResearchPlanResponse)
async def plan_research(request: CreateResearchPlanRequest) -> ResearchPlanResponse:
    return create_research_plan(request)
