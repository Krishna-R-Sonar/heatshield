from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-engine"))
sys.path.insert(0, str(ROOT / "ai"))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings
from app.main import app
from app.providers.open_meteo_validation import (
    OpenMeteoForecastValidationProvider,
    OpenMeteoValidationError,
)
from app.services.forecast_validation_service import ForecastValidationService


def _payloads() -> tuple[dict, dict]:
    start = datetime.combine(date.today() - timedelta(days=3), datetime.min.time())
    times = [(start + timedelta(hours=index)).isoformat(timespec="minutes") for index in range(3)]
    previous_units = {"time": "iso8601"}
    previous_hourly = {"time": times}
    values = {
        "temperature_2m_previous_day1": [12.0, 15.0, None],
        "temperature_2m_previous_day2": [13.0, 16.0, 18.0],
        "precipitation_previous_day1": [0.0, 5.0, 10.0],
        "precipitation_previous_day2": [1.0, 2.0, 3.0],
    }
    for name, series in values.items():
        previous_hourly[name] = series
        previous_units[name] = "°C" if name.startswith("temperature") else "mm"
    previous = {
        "latitude": 18.5,
        "longitude": 73.75,
        "timezone": "GMT",
        "hourly": previous_hourly,
        "hourly_units": previous_units,
    }
    reference = {
        "latitude": 18.5,
        "longitude": 73.75,
        "timezone": "GMT",
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C", "precipitation": "mm"},
        "hourly": {
            "time": times,
            "temperature_2m": [10.0, 10.0, None],
            "precipitation": [0.0, 3.0, 8.0],
        },
    }
    return previous, reference


def _provider(handler) -> OpenMeteoForecastValidationProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenMeteoForecastValidationProvider(client=client)


def test_validation_calculates_lead_time_mae_against_era5_reanalysis():
    previous_payload, reference_payload = _payloads()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=previous_payload if "previous-runs-api" in request.url.host else reference_payload,
        )

    result = _provider(handler).get_historical_performance(18.52, 73.85)
    metrics = {(item.variable, item.forecast_horizon_hours): item for item in result.metrics}

    assert len(requests) == 2
    assert result.status == "AVAILABLE"
    assert result.title == "Historical forecast performance"
    assert result.reference_model == "ERA5 reanalysis"
    assert result.reference_data_type == "REANALYSIS"
    assert metrics[("temperature", 24)].mean_absolute_error == 3.5
    assert metrics[("temperature", 24)].sample_count == 2
    assert metrics[("precipitation", 24)].mean_absolute_error == pytest.approx(4 / 3)
    assert metrics[("precipitation", 24)].sample_count == 3
    assert metrics[("temperature", 48)].mean_absolute_error == 4.5
    assert metrics[("precipitation", 48)].mean_absolute_error == pytest.approx(7 / 3)
    assert "not a ground station observation" in result.limitations[0]
    assert "disaster prediction" in result.limitations[3]


def test_validation_preserves_small_nonzero_mae_instead_of_rounding_it_to_zero():
    previous_payload, reference_payload = _payloads()
    previous_payload["hourly"]["temperature_2m_previous_day1"] = [10.001, None, None]
    reference_payload["hourly"]["temperature_2m"] = [10.0, None, None]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=previous_payload if "previous-runs-api" in request.url.host else reference_payload,
        )

    result = _provider(handler).get_historical_performance(18.52, 73.85)
    metric = next(
        item for item in result.metrics
        if item.variable == "temperature" and item.forecast_horizon_hours == 24
    )

    assert metric.mean_absolute_error == pytest.approx(0.001)
    assert metric.sample_count == 1


def test_validation_rejects_invalid_units_and_keeps_empty_metrics_unavailable():
    previous_payload, reference_payload = _payloads()
    previous_payload["hourly_units"]["temperature_2m_previous_day1"] = "°F"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=previous_payload if "previous-runs-api" in request.url.host else reference_payload,
        )

    with pytest.raises(OpenMeteoValidationError, match="incompatible units"):
        _provider(handler).get_historical_performance(18.52, 73.85)


def test_validation_reports_provider_timeout_explicitly():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(OpenMeteoValidationError, match="timed out"):
        _provider(timeout).get_historical_performance(18.52, 73.85)


def test_validation_service_caches_report_for_same_grid_request():
    previous_payload, reference_payload = _payloads()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=previous_payload if "previous-runs-api" in request.url.host else reference_payload,
        )

    service = ForecastValidationService(_provider(handler))
    service.get_for_location(18.52, 73.85)
    service.get_for_location(18.52001, 73.85001)

    assert len(requests) == 2


def test_validation_service_force_refresh_bypasses_grid_cache():
    previous_payload, reference_payload = _payloads()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=previous_payload if "previous-runs-api" in request.url.host else reference_payload,
        )

    service = ForecastValidationService(_provider(handler))
    service.get_for_location(18.52, 73.85)
    service.get_for_location(18.52, 73.85, force_refresh=True)

    assert len(requests) == 4


def test_validation_route_is_real_mode_only(monkeypatch):
    previous_payload, reference_payload = _payloads()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=previous_payload if "previous-runs-api" in request.url.host else reference_payload,
        )

    monkeypatch.setattr(settings, "data_mode", "real")
    with TestClient(app) as client:
        app.state.forecast_validation_service = ForecastValidationService(_provider(handler))
        response = client.get(
            "/api/v1/weather/validation",
            params={"latitude": 18.52, "longitude": 73.85},
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Historical forecast performance"
    assert response.json()["metrics"][0]["forecast_horizon_hours"] == 24
