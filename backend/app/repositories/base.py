from abc import ABC, abstractmethod
from typing import Any


class RiskAssessmentRepository(ABC):
    @abstractmethod
    def save_assessment(self, location_id: str, assessment: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_latest(self, location_id: str) -> dict[str, Any] | None: ...
