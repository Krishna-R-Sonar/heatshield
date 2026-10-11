from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WeatherLocationEvidence(BaseModel):
    name: str
    state: str = ""
    country: str = ""
    requested_latitude: float
    requested_longitude: float
    grid_latitude: float | None = None
    grid_longitude: float | None = None
    elevation_m: float | None = None
    timezone: str | None = None


class WeatherMeasurement(BaseModel):
    value: float | int | None
    unit: str
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    data_type: Literal["FORECAST"] = "FORECAST"
    forecast_time: str | None = None
    provenance: dict[str, Any]


class WeatherTimeSeries(BaseModel):
    times: list[str]
    values: dict[str, list[float | int | None]]
    units: dict[str, str]
    interval_minutes: int | None = None
    aggregation_period: str | None = None
    provenance: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class WeatherProviderStatus(BaseModel):
    provider: str
    status: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE", "NOT_CONFIGURED", "NOT_NEEDED"]
    retrieved_at: str | None = None
    fields: list[str] = Field(default_factory=list)
    used_fields: list[str] = Field(default_factory=list)
    reason: str | None = None


class WeatherProviderDisagreement(BaseModel):
    variable: str
    first_provider: str
    first_value: float
    second_provider: str
    second_value: float
    difference: float
    unit: str
    valid_time: str
    materiality_threshold: float


class HistoricalBaselineStatistics(BaseModel):
    mean: float
    median: float
    p25: float
    p75: float
    minimum: float
    maximum: float
    sample_count: int


class HistoricalAnomalyPoint(BaseModel):
    date: str
    indicator: Literal["temperature_max", "precipitation", "rainfall"]
    current_value: float | None
    unit: str
    anomaly: float | None
    percentile: float | None
    historical_baseline: HistoricalBaselineStatistics | None
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    forecast_data_type: Literal["FORECAST"] = "FORECAST"
    baseline_data_type: Literal["REANALYSIS"] = "REANALYSIS"
    forecast_source: str | None = None


class HistoricalAnomalyEvidence(BaseModel):
    status: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE"]
    anomalies: list[HistoricalAnomalyPoint]
    baseline_period: str = "1991-2020"
    methodology: str
    source: str = "Open-Meteo Historical Weather API"
    model: str = "ERA5"
    spatial_resolution: str = "0.25 degrees (approximately 25 km)"
    retrieved_at: str
    grid_latitude: float | None = None
    grid_longitude: float | None = None
    quality: str
    limitations: list[str]


class WeatherProvenance(BaseModel):
    source: str = "Open-Meteo"
    source_url: str = "https://open-meteo.com/"
    api: str = "Weather Forecast API"
    data_type: Literal["FORECAST"] = "FORECAST"
    model_selection: str = "auto"
    model_id: str | None = None
    forecast_generated_at: str | None = None
    retrieved_at: str
    generationtime_ms: float | None = None
    timezone: str | None = None
    spatial_resolution: str = "Model-dependent; exact resolution is not returned for auto selection."
    quality: str
    limitations: list[str]


class WeatherEvidence(BaseModel):
    status: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE"]
    location: WeatherLocationEvidence
    forecast: dict[str, WeatherMeasurement]
    hourly: WeatherTimeSeries
    daily: WeatherTimeSeries
    provenance: WeatherProvenance
    historical_anomalies: HistoricalAnomalyEvidence | None = None
    provider_status: list[WeatherProviderStatus] = Field(default_factory=list)
    provider_disagreements: list[WeatherProviderDisagreement] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)