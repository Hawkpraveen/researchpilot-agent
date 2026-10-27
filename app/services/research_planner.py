from app.models.research import (
    CreateResearchPlanRequest,
    ResearchPlanBudget,
    ResearchPlanResponse,
    ResearchPlanTask,
)

_BUDGETS: dict[str, ResearchPlanBudget] = {
    "quick": ResearchPlanBudget(maxSubtasks=3, maxPages=5, maxFollowUpRounds=0, timeoutSeconds=60),
    "standard": ResearchPlanBudget(maxSubtasks=5, maxPages=15, maxFollowUpRounds=2, timeoutSeconds=180),
    "deep": ResearchPlanBudget(maxSubtasks=8, maxPages=30, maxFollowUpRounds=3, timeoutSeconds=300),
}


def create_research_plan(request: CreateResearchPlanRequest) -> ResearchPlanResponse:
    research_request = request.request
    budget = _BUDGETS[research_request.depth]
    criteria = research_request.criteria
    selected_criteria = criteria[: budget.maxSubtasks]
    tasks: list[ResearchPlanTask] = []

    if selected_criteria:
        for criterion in selected_criteria:
            if research_request.alternatives:
                questions = [
                    f"What evidence is available about {alternative} for {criterion.label}?"
                    for alternative in research_request.alternatives
                ]
            else:
                questions = [f"What evidence is available for {criterion.label} in this decision?"]
            tasks.append(
                ResearchPlanTask(
                    id=f"criterion-{criterion.id}",
                    criterionId=criterion.id,
                    question=f"{criterion.label}: {research_request.goal}",
                    searchQueries=questions,
                )
            )
    else:
        alternatives = research_request.alternatives or [research_request.goal]
        tasks.append(
            ResearchPlanTask(
                id="overview",
                criterionId=None,
                question=research_request.goal,
                searchQueries=[
                    f"What evidence is available about {alternative} for this decision?"
                    for alternative in alternatives
                ],
            )
        )

    return ResearchPlanResponse(
        jobId=request.jobId,
        contractVersion=request.contractVersion,
        status="planned",
        researchExecuted=False,
        budget=budget,
        tasks=tasks,
        omittedCriterionIds=[criterion.id for criterion in criteria[len(selected_criteria) :]],
        constraints=research_request.constraints,
        notice="This is a bounded plan only. No search, source verification, model analysis, or report generation was run.",
    )
