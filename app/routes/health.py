from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/api/v1/health", include_in_schema=False)
async def health() -> dict[str, str | list[str]]:
    return {
        "service": "researchpilot-agent",
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capabilities": ["bounded_research_planning"],
    }
