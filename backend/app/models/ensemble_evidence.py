from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class EnsembleStatistics(BaseModel):
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    unit: str
    member_count: int
    mean: float | None = None
    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    spread_iqr: float | None = None
    uncertainty: Literal["LOW", "MODERATE", "HIGH", "UNAVAILABLE"]
    uncertainty_reason: str


class EnsembleDailyEvidence(BaseModel):
    date: str
    temperature_max: EnsembleStatistics
    precipitation_sum: EnsembleStatistics


class EnsembleProvenance(BaseModel):
    source: str = "Open-Meteo Ensemble API"
    source_url: str = "https://open-meteo.com/en/docs/ensemble-api"
    model: str = "ECMWF IFS 0.25° ensemble"
    model_id: str = "ecmwf_ifs025"
    data_type: Literal["ENSEMBLE_FORECAST"] = "ENSEMBLE_FORECAST"
    retrieved_at: str
    forecast_generated_at: str | None = None
    timezone: str | None = None
    requested_latitude: float
    requested_longitude: float
    grid_latitude: float | None = None
    grid_longitude: float | None = None
    spatial_resolution: str = "0.25 degrees (approximately 25 km)"
    forecast_period: str | None = None
    classification_method: str
    limitations: list[str]


class EnsembleForecastEvidence(BaseModel):
    status: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE"]
    member_count: int
    daily: list[EnsembleDailyEvidence]
    provenance: EnsembleProvenance