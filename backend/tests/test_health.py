"""Tests for the foundation health endpoints.

Run with: uv run pytest
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tenderpilot.main import app

client = TestClient(app)


def test_liveness_returns_alive() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_never_claims_unconfigured_dependencies_are_healthy() -> None:
    # Database and object_storage are intentionally NOT asserted here -
    # both are real, live checks whose result depends on whether Postgres
    # and SeaweedFS containers happen to be running wherever this test
    # executes (not true in CI). See the mocked readiness tests below,
    # which control those results deterministically instead of depending
    # on real infrastructure.
    response = client.get("/health/ready")
    body = response.json()
    # job_queue doesn't exist yet - nothing in this backend talks to
    # RabbitMQ - must never be reported as anything other than
    # not_configured.
    assert body["dependencies"]["job_queue"] == "not_configured"
    # Model provider status depends on real .env content - only assert it's
    # one of the two honest values, not which one, so this test doesn't
    # depend on whether real credentials are present in this environment.
    assert body["dependencies"]["model_provider_chat"] in (
        "credentials_present",
        "not_configured",
    )


@pytest.fixture
def mock_database_status(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Lets a test control what tenderpilot.db.get_database_status()
    returns, without needing a real database - patches the name as
    imported into tenderpilot.main (by attribute name, via monkeypatch,
    so this stays a plain attribute-string patch rather than something
    mypy's strict re-export checking has an opinion about), since that's
    the reference readiness() actually calls."""
    box: dict[str, str] = {"value": "healthy"}

    async def fake_get_database_status(_settings: object) -> str:
        return box["value"]

    monkeypatch.setattr("tenderpilot.main.get_database_status", fake_get_database_status)
    yield box


@pytest.fixture
def mock_storage_status(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Same pattern as mock_database_status above, for
    tenderpilot.storage.get_storage_status() as imported into
    tenderpilot.main - lets a test control the object_storage result
    without needing a real SeaweedFS container."""
    box: dict[str, str] = {"value": "healthy"}

    async def fake_get_storage_status(_settings: object) -> str:
        return box["value"]

    monkeypatch.setattr("tenderpilot.main.get_storage_status", fake_get_storage_status)
    yield box


def test_readiness_reports_ready_200_when_database_and_storage_are_healthy(
    mock_database_status: dict[str, str],
    mock_storage_status: dict[str, str],
) -> None:
    mock_database_status["value"] = "healthy"
    mock_storage_status["value"] = "healthy"
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["dependencies"]["database"] == "healthy"
    assert body["dependencies"]["object_storage"] == "healthy"


def test_readiness_reports_degraded_503_when_database_is_unreachable(
    mock_database_status: dict[str, str],
    mock_storage_status: dict[str, str],
) -> None:
    mock_database_status["value"] = "unreachable"
    mock_storage_status["value"] = "healthy"
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["database"] == "unreachable"


def test_readiness_reports_degraded_503_when_database_not_configured(
    mock_database_status: dict[str, str],
    mock_storage_status: dict[str, str],
) -> None:
    mock_database_status["value"] = "not_configured"
    mock_storage_status["value"] = "healthy"
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["database"] == "not_configured"


def test_readiness_reports_degraded_503_when_storage_is_unreachable(
    mock_database_status: dict[str, str],
    mock_storage_status: dict[str, str],
) -> None:
    # Proves object_storage genuinely gates overall readiness rather than
    # being purely informational - a database-only view of "ready" would
    # miss a real storage outage.
    mock_database_status["value"] = "healthy"
    mock_storage_status["value"] = "unreachable"
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["object_storage"] == "unreachable"


def test_readiness_reports_degraded_503_when_storage_not_configured(
    mock_database_status: dict[str, str],
    mock_storage_status: dict[str, str],
) -> None:
    # Never conflate "no credentials set" with "healthy" - the whole
    # point of separating configuration presence from connectivity.
    mock_database_status["value"] = "healthy"
    mock_storage_status["value"] = "not_configured"
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["object_storage"] == "not_configured"


def test_liveness_is_unaffected_by_database_status(
    mock_database_status: dict[str, str],
) -> None:
    mock_database_status["value"] = "unreachable"
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_every_response_carries_a_request_id_header() -> None:
    response = client.get("/health/live")
    assert "X-Request-Id" in response.headers
    assert len(response.headers["X-Request-Id"]) == 36  # UUID4 string length


def test_unknown_route_returns_structured_error_not_a_stack_trace() -> None:
    response = client.get("/this-route-does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == "http_error"
