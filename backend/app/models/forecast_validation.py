from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ForecastPerformanceMetric(BaseModel):
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    variable: Literal["temperature", "precipitation"]
    forecast_horizon_hours: int
    mean_absolute_error: float | None = None
    unit: str
    sample_count: int


class ForecastValidationEvidence(BaseModel):
    status: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE"]
    title: str = "Historical forecast performance"
    metrics: list[ForecastPerformanceMetric]
    verification_period: str
    forecast_source: str = "Open-Meteo Previous Runs API"
    forecast_model_selection: str = "auto"
    reference_source: str = "Open-Meteo Historical Weather API"
    reference_model: str = "ERA5 reanalysis"
    reference_data_type: Literal["REANALYSIS"] = "REANALYSIS"
    retrieved_at: str
    quality: str
    limitations: list[str]