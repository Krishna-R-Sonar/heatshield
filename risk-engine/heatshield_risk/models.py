from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class RiskLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


Scenario = Literal["flood_glof", "extreme_heat"]


@dataclass
class LocationInfo:
    id: str
    name: str
    state: str
    lat: float
    lng: float


@dataclass
class GlacialLakeInfo:
    available: bool
    lake_id: str | None = None
    status: Literal["normal", "watch", "elevated"] | None = None
    notes: str | None = None


@dataclass
class FloodInputs:
    location_id: str
    rainfall_intensity_mm_hr: float | None
    cumulative_rainfall_mm_24h: float | None
    river_level_m: float | None
    river_threshold_m: float | None
    glacial_lake: GlacialLakeInfo
    historical_flood_events_5y: int | None
    population_exposure_index: float | None
    infrastructure_exposure_index: float | None
    data_provenance: str = "DEMO"
    timestamp: str = ""


@dataclass
class HeatInputs:
    location_id: str
    temperature_c: float | None
    humidity_pct: float | None
    heat_index_c: float | None
    elevated_temp_duration_hours: float | None
    population_exposure_index: float | None
    infrastructure_exposure_index: float | None
    data_provenance: str = "DEMO"
    timestamp: str = ""


@dataclass
class ContributingFactor:
    id: str
    label: str
    severity: Literal["low", "moderate", "high"]
    kind: Literal["observed", "calculated", "historical"]
    detail: str
    score_contribution: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "severity": self.severity,
            "kind": self.kind,
            "detail": self.detail,
            "scoreContribution": self.score_contribution,
        }


@dataclass
class ObservationEntry:
    label: str
    value: str
    kind: Literal["observed", "calculated", "historical", "unavailable"]

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value, "kind": self.kind}


@dataclass
class DataSourceRef:
    name: str
    note: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "note": self.note}


@dataclass
class RiskAssessmentResult:
    location: LocationInfo
    scenario: Scenario
    risk_score: float
    risk_level: RiskLevel
    risk_category: str
    confidence: float
    uncertainty: str
    contributing_factors: list[ContributingFactor] = field(default_factory=list)
    observations: list[ObservationEntry] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    data_sources: list[DataSourceRef] = field(default_factory=list)
    timestamp: str = ""
    simulation_active: bool = False
    component_scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": {
                "id": self.location.id,
                "name": self.location.name,
                "state": self.location.state,
                "lat": self.location.lat,
                "lng": self.location.lng,
            },
            "scenario": self.scenario,
            "riskScore": round(self.risk_score, 1),
            "riskLevel": self.risk_level.value,
            "riskCategory": self.risk_category,
            "confidence": round(self.confidence, 2),
            "uncertainty": self.uncertainty,
            "contributingFactors": [f.to_dict() for f in self.contributing_factors],
            "observations": [o.to_dict() for o in self.observations],
            "recommendedActions": self.recommended_actions,
            "dataSources": [d.to_dict() for d in self.data_sources],
            "timestamp": self.timestamp,
            "simulationActive": self.simulation_active,
            "componentScores": self.component_scores,
        }
