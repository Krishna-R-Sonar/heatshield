from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import pytest

from app.models.weather_evidence import (
    WeatherEvidence,
    WeatherLocationEvidence,
    WeatherMeasurement,
    WeatherProvenance,
    WeatherTimeSeries,
)
from app.providers.open_meteo import OpenMeteoError
from app.services.real_heat_assessment_service import RealHeatAssessmentService

LOCATION = {
    "id": "test-location",
    "name": "Test location",
    "state": "Test state",
    "lat": 1.0,
    "lng": 2.0,
}


class StubWeatherEvidenceService:
    def __init__(self, evidence: WeatherEvidence | None = None, error: Exception | None = None) -> None:
        self.evidence = evidence
        self.error = error
        self.requested_locations: list[dict[str, Any]] = []

    def get_for_location(self, location: dict[str, Any]) -> WeatherEvidence:
        self.requested_locations.append(location)
        if self.error is not None:
            raise self.error
        assert self.evidence is not None
        return self.evidence


def make_evidence(
    *,
    temperature: Any = 32.5,
    humidity: Any = 65.0,
    temperature_unit: str = "°C",
    humidity_unit: str = "%",
    temperature_time: str | None = None,
    humidity_time: str | None = None,
    retrieved_at: str | None = None,
    requested_latitude: float = 1.0,
    requested_longitude: float = 2.0,
    grid_latitude: float | None = 1.1,
    grid_longitude: float | None = 2.1,
) -> WeatherEvidence:
    now = datetime.now(timezone.utc)
    forecast_time = (now + timedelta(minutes=30)).isoformat()
    observed_at = retrieved_at or now.isoformat()

    def measurement(value: Any, unit: str, timestamp: str | None) -> WeatherMeasurement:
        valid_time = timestamp or forecast_time
        return WeatherMeasurement(
            value=value,
            unit=unit,
            status="AVAILABLE" if value is not None else "UNAVAILABLE",
            data_type="FORECAST",
            forecast_time=valid_time,
            provenance={
                "source": "Open-Meteo",
                "data_type": "FORECAST",
                "forecast_time": valid_time,
                "retrieval_time": observed_at,
                "requested_latitude": requested_latitude,
                "requested_longitude": requested_longitude,
            },
        )

    return WeatherEvidence(
        status="AVAILABLE",
        location=WeatherLocationEvidence(
            name=LOCATION["name"],
            state=LOCATION["state"],
            requested_latitude=requested_latitude,
            requested_longitude=requested_longitude,
            grid_latitude=grid_latitude,
            grid_longitude=grid_longitude,
            timezone="Etc/GMT-2",
        ),
        forecast={
            "temperature": measurement(temperature, temperature_unit, temperature_time),
            "humidity": measurement(humidity, humidity_unit, humidity_time),
        },
        hourly=WeatherTimeSeries(times=[], values={}, units={}),
        daily=WeatherTimeSeries(times=[], values={}, units={}),
        provenance=WeatherProvenance(
            retrieved_at=observed_at,
            quality="Mock Open-Meteo forecast evidence.",
            limitations=["Forecast values are model output, not ground observations."],
        ),
    )


def test_valid_forecast_weather_is_validated_but_assessment_remains_incomplete() -> None:
    evidence = make_evidence()
    service = StubWeatherEvidenceService(evidence)

    result = RealHeatAssessmentService(service).assess(LOCATION)

    assert result["status"] == "INCOMPLETE"
    assert result["assessmentType"] == "FORECAST_HEAT"
    assert result["scoreStatus"] == "NOT_CALCULATED"
    assert result["riskScore"] is None
    assert result["riskLevel"] is None
    assert result["forecastInputs"]["temperature"]["value"] == 32.5
    assert result["forecastInputs"]["temperature"]["status"] == "VALIDATED"
    assert result["forecastInputs"]["relativeHumidity"]["value"] == 65.0
    assert result["forecastInputs"]["relativeHumidity"]["status"] == "VALIDATED"
    assert result["assessmentWindow"]["forecastTime"] == evidence.forecast["temperature"].forecast_time
    assert result["provenance"]["dataType"] == "FORECAST"
    assert result["provenance"]["requestedCoordinates"] == {"latitude": 1.0, "longitude": 2.0}
    assert result["provenance"]["gridCoordinates"] == {"latitude": 1.1, "longitude": 2.1}
    assert result["missingInputs"] == [
        "heat_index_c",
        "elevated_temp_duration_hours",
        "population_exposure_index",
        "infrastructure_exposure_index",
    ]
    assert len(service.requested_locations) == 1
    assert service.requested_locations[0] is LOCATION


@pytest.mark.parametrize(
    ("evidence", "reason_code", "field"),
    [
        (make_evidence(temperature=None), "FORECAST_MEASUREMENT_UNAVAILABLE", "temperature"),
        (make_evidence(humidity=None), "FORECAST_MEASUREMENT_UNAVAILABLE", "relativeHumidity"),
        (make_evidence(humidity=101), "INVALID_MEASUREMENT_VALUE", "relativeHumidity"),
        (make_evidence(temperature_unit="°F"), "UNIT_MISMATCH", "temperature"),
        (make_evidence(humidity_unit="fraction"), "UNIT_MISMATCH", "relativeHumidity"),
        (
            make_evidence(
                temperature_time=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            ),
            "STALE_OR_INVALID_FORECAST_TIME",
            "temperature",
        ),
        (
            make_evidence(
                temperature_time=(datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            ),
            "STALE_OR_INVALID_FORECAST_TIME",
            "temperature",
        ),
        (
            make_evidence(retrieved_at=(datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()),
            "STALE_OR_INVALID_EVIDENCE",
            "retrieved_at",
        ),
        (
            make_evidence(requested_latitude=3.0),
            "LOCATION_ASSOCIATION_MISMATCH",
            "location",
        ),
        (
            make_evidence(
                temperature_time=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                humidity_time=(datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat(),
            ),
            "FORECAST_TIMESTAMP_MISMATCH",
            "forecastTime",
        ),
        (make_evidence(grid_latitude=None), "INVALID_PROVIDER_GRID_COORDINATES", "location"),
    ],
)
def test_invalid_or_incomplete_forecast_inputs_are_rejected(
    evidence: WeatherEvidence,
    reason_code: str,
    field: str,
) -> None:
    result = RealHeatAssessmentService(StubWeatherEvidenceService(evidence)).assess(LOCATION)

    assert result["status"] == "UNAVAILABLE"
    assert result["riskScore"] is None
    assert result["riskLevel"] is None
    assert any(
        reason["code"] == reason_code and reason["input"] == field
        for reason in result["reasons"]
    )


def test_temperature_sanity_bounds_are_validated() -> None:
    below_range = RealHeatAssessmentService(
        StubWeatherEvidenceService(make_evidence(temperature=-90.1))
    ).assess(LOCATION)
    above_range = RealHeatAssessmentService(
        StubWeatherEvidenceService(make_evidence(temperature=60.1))
    ).assess(LOCATION)

    for result in (below_range, above_range):
        assert result["status"] == "UNAVAILABLE"
        assert result["riskScore"] is None
        assert any(
            reason["code"] == "INVALID_MEASUREMENT_VALUE"
            and reason["input"] == "temperature"
            for reason in result["reasons"]
        )


def test_provider_failure_is_an_explicit_unavailable_assessment() -> None:
    service = StubWeatherEvidenceService(error=OpenMeteoError("Open-Meteo request timed out."))

    result = RealHeatAssessmentService(service).assess(LOCATION)

    assert result["status"] == "UNAVAILABLE"
    assert result["riskScore"] is None
    assert result["riskLevel"] is None
    assert any(reason["code"] == "WEATHER_PROVIDER_UNAVAILABLE" for reason in result["reasons"])


def test_missing_evidence_service_does_not_fall_back_to_demo_inputs() -> None:
    result = RealHeatAssessmentService(None).assess(LOCATION)

    assert result["status"] == "UNAVAILABLE"
    assert result["riskScore"] is None
    assert result["riskLevel"] is None
    assert any(
        reason["code"] == "WEATHER_EVIDENCE_SERVICE_UNAVAILABLE"
        for reason in result["reasons"]
    )
