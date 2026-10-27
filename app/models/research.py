from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CriterionId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
GoalText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
ConstraintText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResearchCriterion(StrictModel):
    id: CriterionId
    label: ShortText
    weight: int = Field(ge=1, le=5, strict=True)


class ResearchRequest(StrictModel):
    goal: GoalText
    alternatives: list[ShortText] = Field(default_factory=list, max_length=5)
    criteria: list[ResearchCriterion] = Field(default_factory=list, max_length=10)
    constraints: list[ConstraintText] = Field(default_factory=list, max_length=10)
    depth: Literal["quick", "standard", "deep"] = "standard"

    @model_validator(mode="after")
    def require_unique_criterion_ids(self) -> "ResearchRequest":
        ids = [criterion.id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("Criterion IDs must be unique.")
        return self


class CreateResearchPlanRequest(StrictModel):
    jobId: Annotated[str, StringConstraints(pattern=r"^job_[A-Za-z0-9-]{1,120}$")]
    contractVersion: Literal["1.0"] = "1.0"
    request: ResearchRequest


class ResearchPlanBudget(StrictModel):
    maxSubtasks: int
    maxPages: int
    maxFollowUpRounds: int
    timeoutSeconds: int


class ResearchPlanTask(StrictModel):
    id: str
    criterionId: str | None
    question: str
    searchQueries: list[str]


class ResearchPlanResponse(StrictModel):
    jobId: str
    contractVersion: str
    status: Literal["planned"]
    researchExecuted: Literal[False]
    budget: ResearchPlanBudget
    tasks: list[ResearchPlanTask]
    omittedCriterionIds: list[str]
    constraints: list[str]
    notice: str
