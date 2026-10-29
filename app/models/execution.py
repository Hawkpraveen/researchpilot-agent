from typing import Literal
from uuid import uuid4
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator

from app.models.research import CreateResearchPlanRequest, StrictModel


class ProviderConfig(StrictModel):
    mode: Literal["local", "byok"]
    provider: Literal["ollama", "openai", "anthropic", "custom"]
    endpoint: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=150)
    apiKey: SecretStr = Field(default_factory=lambda: SecretStr(""))

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Use an HTTP(S) endpoint without credentials, query, or fragment.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_mode(self) -> "ProviderConfig":
        if (self.mode == "local") != (self.provider == "ollama"):
            raise ValueError("Local mode requires Ollama; cloud providers require BYOK mode.")
        return self


class ExecuteResearchRequest(CreateResearchPlanRequest):
    executionId: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=80)
    provider: ProviderConfig
    resume: "ResumeDiscovery | None" = None


class Evidence(StrictModel):
    sourceId: str
    excerpt: str = Field(min_length=20, max_length=1000)


class Finding(StrictModel):
    text: str = Field(min_length=1, max_length=6000)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


class ComparisonFinding(StrictModel):
    criterionId: str
    alternative: str
    finding: Finding


class ResearchDraft(StrictModel):
    summary: list[Finding] = Field(min_length=1, max_length=12)
    recommendation: Finding
    comparison: list[ComparisonFinding] = Field(default_factory=list, max_length=50)
    uncertainties: list[str] = Field(default_factory=list, max_length=30)


class SubjectCandidate(StrictModel):
    label: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=500)
    sourceIds: list[str] = Field(min_length=1, max_length=8)


class SubjectResolution(StrictModel):
    status: Literal["resolved", "ambiguous", "unresolved"]
    subject: str = Field(max_length=300)
    explanation: str = Field(min_length=1, max_length=1000)
    candidates: list[SubjectCandidate] = Field(max_length=4)
    selectedSourceIds: list[str] = Field(max_length=8)


class ResearchSource(StrictModel):
    id: str
    title: str
    domain: str
    url: str
    accessedAt: str
    snippet: str
    reliability: Literal["high", "medium", "low"] = "low"


class ComparisonRow(StrictModel):
    criterion: str
    values: dict[str, str]


class ResumeDiscovery(StrictModel):
    subject: str = Field(default="", max_length=150)
    context: str = Field(default="", max_length=500)
    consumedPages: int = Field(ge=0, le=30)
    sources: list[ResearchSource] = Field(min_length=1, max_length=30)


class ResearchReport(StrictModel):
    researchStatus: Literal["completed", "partial", "needs_clarification"] = "completed"
    resolution: SubjectResolution | None = None
    summary: str
    recommendation: str
    alternatives: list[str]
    comparison: list[ComparisonRow]
    sources: list[ResearchSource] = Field(min_length=1, max_length=30)
    uncertainties: list[str]


class ExecuteResearchResponse(StrictModel):
    usage: dict | None = None
    jobId: str
    contractVersion: Literal["1.0"] = "1.0"
    researchExecuted: Literal[True] = True
    report: ResearchReport


ExecuteResearchRequest.model_rebuild()
