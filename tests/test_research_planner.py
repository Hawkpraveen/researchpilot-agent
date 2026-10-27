import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def post_plan(payload: dict[str, object], headers: dict[str, str] | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post("/api/v1/research/plan", json=payload, headers=headers)


def make_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "goal": "Compare options for a small research team",
        "alternatives": ["Option A", "Option B"],
        "criteria": [
            {"id": "privacy", "label": "Privacy", "weight": 5},
            {"id": "cost", "label": "Cost", "weight": 3},
        ],
        "constraints": ["Use publicly available evidence"],
        "depth": "quick",
    }
    request.update(overrides)
    return {"jobId": "job_test-123", "contractVersion": "1.0", "request": request}


@pytest.mark.anyio
async def test_plan_is_bounded_and_does_not_claim_to_have_researched() -> None:
    response = await post_plan(make_request())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "planned"
    assert body["researchExecuted"] is False
    assert body["budget"] == {
        "maxSubtasks": 3,
        "maxPages": 5,
        "maxFollowUpRounds": 0,
        "timeoutSeconds": 60,
    }
    assert [task["criterionId"] for task in body["tasks"]] == ["privacy", "cost"]
    assert len(body["tasks"][0]["searchQueries"]) == 2
    assert body["omittedCriterionIds"] == []
    assert "No search" in body["notice"]


@pytest.mark.anyio
async def test_plan_caps_subtasks_and_reports_omitted_criteria() -> None:
    criteria = [{"id": f"criterion-{index}", "label": f"Criterion {index}", "weight": 1} for index in range(5)]
    response = await post_plan(make_request(criteria=criteria))

    assert response.status_code == 200
    body = response.json()
    assert len(body["tasks"]) == 3
    assert body["omittedCriterionIds"] == ["criterion-3", "criterion-4"]


@pytest.mark.anyio
async def test_plan_rejects_unknown_fields_and_duplicate_criteria() -> None:
    unknown_field_request = make_request(extra={"provider": "untrusted"})
    duplicate_criteria = [
        {"id": "privacy", "label": "Privacy", "weight": 5},
        {"id": "privacy", "label": "Other privacy criterion", "weight": 2},
    ]

    unknown_response = await post_plan(unknown_field_request)
    duplicate_response = await post_plan(make_request(criteria=duplicate_criteria))

    assert unknown_response.status_code == 422
    assert unknown_response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert duplicate_response.status_code == 422


@pytest.mark.anyio
async def test_plan_echoes_valid_request_id() -> None:
    response = await post_plan(make_request(), headers={"X-Request-ID": "be-request-123"})

    assert response.headers["x-request-id"] == "be-request-123"
