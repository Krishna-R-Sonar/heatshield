from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class FloodLocationEvidence(BaseModel):
    requested_latitude: float
    requested_longitude: float
    grid_latitude: float | None = None
    grid_longitude: float | None = None
    elevation_m: float | None = None
    timezone: str | None = None
    river_association: Literal["UNVERIFIED"] = "UNVERIFIED"


class FloodMeasurement(BaseModel):
    value: float | None
    unit: str
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    data_type: Literal["FORECAST", "REANALYSIS", "UNAVAILABLE"]
    reference_time: str
    provenance: dict[str, Any]


class FloodDailyEvidence(BaseModel):
    time: str
    data_type: Literal["FORECAST", "REANALYSIS", "UNAVAILABLE"]
    discharge: FloodMeasurement
    mean: FloodMeasurement
    median: FloodMeasurement
    minimum: FloodMeasurement
    maximum: FloodMeasurement
    p25: FloodMeasurement
    p75: FloodMeasurement
    discharge_spread_iqr: float | None = None
    uncertainty: Literal["LOW", "MODERATE", "HIGH", "UNAVAILABLE"] = "UNAVAILABLE"
    uncertainty_reason: str


class FloodProvenance(BaseModel):
    source: str = "Open-Meteo Global Flood API"
    source_url: str = "https://open-meteo.com/en/docs/flood-api"
    dataset: str = "Global Flood Awareness System (GloFAS)"
    model: str = "GloFAS v4 (Open-Meteo documented default)"
    model_version_in_payload: bool = False
    data_type: Literal["MODEL_EVIDENCE"] = "MODEL_EVIDENCE"
    retrieved_at: str
    forecast_generated_at: str | None = None
    generationtime_ms: float | None = None
    timezone: str | None = None
    spatial_resolution_km: float = 5.0
    requested_latitude: float
    requested_longitude: float
    grid_latitude: float | None = None
    grid_longitude: float | None = None
    historical_period_available: str = "1984 through July 2022 (GloFAS v4 reanalysis)"
    returned_historical_period: str | None = None
    returned_forecast_period: str | None = None
    forecast_horizon_days: int = 30
    quality: str
    limitations: list[str]


class FloodEvidence(BaseModel):
    status: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE"]
    location: FloodLocationEvidence
    daily: list[FloodDailyEvidence]
    provenance: FloodProvenance