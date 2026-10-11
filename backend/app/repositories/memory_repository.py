from typing import Any

from app.repositories.base import RiskAssessmentRepository


class InMemoryRiskRepository(RiskAssessmentRepository):
    """Local MVP store; replace with DynamoDB implementation for AWS."""

    def __init__(self) -> None:
        self._latest: dict[str, dict[str, Any]] = {}

    def save_assessment(self, location_id: str, assessment: dict[str, Any]) -> None:
        self._latest[location_id] = assessment

    def get_latest(self, location_id: str) -> dict[str, Any] | None:
        return self._latest.get(location_id)
