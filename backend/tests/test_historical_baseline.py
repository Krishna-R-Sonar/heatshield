from __future__ import annotations

import sys
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
from app.models.weather_evidence import (
    WeatherEvidence,
    WeatherLocationEvidence,
    WeatherProvenance,
    WeatherTimeSeries,
)
from app.providers.open_meteo_historical import (
    OpenMeteoHistoricalError,
    OpenMeteoHistoricalProvider,
)
from app.services.historical_baseline_service import HistoricalBaselineService
from app.services.weather_evidence_service import WeatherEvidenceService


def _historical_payload() -> dict:
    times = [
        "1991-01-01",
        "1991-01-02",
        "1991-01-03",
        "1991-02-01",
        "2020-01-01",
    ]
    return {
        "latitude": 18.5,
        "longitude": 73.75,
        "elevation": 561.0,
        "timezone": "GMT",
        "daily_units": {
            "time": "iso8601",
            "temperature_2m_max": "°C",
            "precipitation_sum": "mm",
            "rain_sum": "mm",
        },
        "daily": {
            "time": times,
            "temperature_2m_max": [10.0, 20.0, 30.0, 100.0, 40.0],
            "precipitation_sum": [0.0, 2.0, 4.0, 100.0, None],
            "rain_sum": [0.0, 1.0, 3.0, 50.0, None],
        },
    }


def _weather(*, temperature: list[float | None] | None = None) -> WeatherEvidence:
    return WeatherEvidence(
        status="AVAILABLE",
        location=WeatherLocationEvidence(
            name="Pune",
            state="Maharashtra",
            country="India",
            requested_latitude=18.52,
            requested_longitude=73.85,
            grid_latitude=18.5,
            grid_longitude=73.75,
            timezone="Asia/Kolkata",
        ),
        forecast={},
        hourly=WeatherTimeSeries(times=[], values={}, units={}),
        daily=WeatherTimeSeries(
            times=["2026-01-15", "2026-02-15"],
            values={
                "temperature_2m_max": temperature or [25.0, 110.0],
                "precipitation_sum": [3.0, 120.0],
                "rain_sum": [2.0, 60.0],
            },
            units={
                "temperature_2m_max": "°C",
                "precipitation_sum": "mm",
                "rain_sum": "mm",
            },
        ),
        provenance=WeatherProvenance(
            retrieved_at="2026-01-14T00:00:00+00:00",
            timezone="Asia/Kolkata",
            quality="test forecast",
            limitations=[],
        ),
    )


def _provider(handler) -> OpenMeteoHistoricalProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenMeteoHistoricalProvider(client=client)


def test_historical_provider_requests_fixed_era5_baseline_with_units():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_historical_payload())

    result = _provider(handler).get_daily_baseline(
        18.52,
        73.85,
        timezone_name="Asia/Kathmandu",
    )
    params = dict(requests[0].url.params)

    assert params["models"] == "era5"
    assert params["start_date"] == "1991-01-01"
    assert params["end_date"] == "2020-12-31"
    assert params["timezone"] == "Asia/Kathmandu"
    assert "temperature_2m_max" in params["daily"]
    assert result["units"]["rain_sum"] == "mm"
    assert result["series"]["precipitation_sum"][-1] is None
    assert result["latitude"] == 18.5


def test_historical_service_uses_same_calendar_month_and_reports_anomalies():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_historical_payload())

    service = HistoricalBaselineService(_provider(handler))
    report = service.calculate(_weather())

    january_temperature = next(
        item for item in report.anomalies
        if item.date == "2026-01-15" and item.indicator == "temperature_max"
    )
    january_precipitation = next(
        item for item in report.anomalies
        if item.date == "2026-01-15" and item.indicator == "precipitation"
    )
    january_rainfall = next(
        item for item in report.anomalies
        if item.date == "2026-01-15" and item.indicator == "rainfall"
    )
    february_temperature = next(
        item for item in report.anomalies
        if item.date == "2026-02-15" and item.indicator == "temperature_max"
    )

    assert report.status == "AVAILABLE"
    assert january_temperature.historical_baseline is not None
    assert january_temperature.historical_baseline.mean == 25.0
    assert january_temperature.historical_baseline.sample_count == 4
    assert january_temperature.anomaly == 0.0
    assert january_temperature.percentile == 50.0
    assert january_precipitation.historical_baseline is not None
    assert january_precipitation.historical_baseline.mean == 2.0
    assert january_precipitation.anomaly == 1.0
    assert january_rainfall.historical_baseline is not None
    assert january_rainfall.historical_baseline.mean == pytest.approx(4 / 3)
    assert january_rainfall.anomaly == pytest.approx(2 / 3)
    assert january_precipitation.percentile == pytest.approx(200 / 3)
    assert january_rainfall.percentile == pytest.approx(200 / 3)
    assert january_precipitation.current_value == 3.0
    assert january_rainfall.current_value == 2.0
    assert february_temperature.historical_baseline is not None
    assert february_temperature.historical_baseline.mean == 100.0
    assert report.baseline_period == "1991-2020"
    assert "same calendar month" in report.methodology
    assert "Asia/Kolkata calendar timezone" in report.methodology
    assert report.model == "ERA5"
    assert "REANALYSIS" == january_temperature.baseline_data_type
    assert "not a measured station observation" in report.limitations[0]
    assert len(requests) == 1


def test_historical_precipitation_and_rainfall_use_their_own_october_series():
    payload = _historical_payload()
    payload["daily"]["time"] = [
        "1991-10-01",
        "1992-10-01",
        "1993-10-01",
        "1994-10-01",
    ]
    payload["daily"]["temperature_2m_max"] = [10.0, 20.0, 30.0, 40.0]
    payload["daily"]["precipitation_sum"] = [1.0, 5.0, None, 7.0]
    payload["daily"]["rain_sum"] = [0.5, 2.0, 6.0, None]

    weather = _weather().model_copy(update={
        "daily": WeatherTimeSeries(
            times=["2026-10-11"],
            values={"precipitation_sum": [4.0], "rain_sum": [2.5]},
            units={"precipitation_sum": "mm", "rain_sum": "mm"},
        ),
    })
    service = HistoricalBaselineService(_provider(
        lambda _request: httpx.Response(200, json=payload),
    ))
    report = service.calculate(weather)

    precipitation = next(
        item for item in report.anomalies if item.indicator == "precipitation"
    )
    rainfall = next(
        item for item in report.anomalies if item.indicator == "rainfall"
    )
    assert precipitation.historical_baseline is not None
    assert precipitation.historical_baseline.mean == pytest.approx(13 / 3)
    assert precipitation.historical_baseline.sample_count == 3
    assert precipitation.anomaly == pytest.approx(-1 / 3)
    assert rainfall.historical_baseline is not None
    assert rainfall.historical_baseline.mean == pytest.approx(8.5 / 3)
    assert rainfall.historical_baseline.sample_count == 3
    assert rainfall.anomaly == pytest.approx(-1 / 3)


def test_historical_service_caches_fixed_reanalysis_by_location():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_historical_payload())

    service = HistoricalBaselineService(_provider(handler))
    service.calculate(_weather())
    service.calculate(_weather())

    assert len(requests) == 1


def test_historical_service_caches_distinct_forecast_timezones_separately():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_historical_payload())

    service = HistoricalBaselineService(_provider(handler))
    pune = _weather()
    kathmandu = pune.model_copy(update={
        "location": pune.location.model_copy(update={"timezone": "Asia/Kathmandu"}),
    })

    service.calculate(pune)
    service.calculate(kathmandu)

    assert len(requests) == 2
    assert [request.url.params["timezone"] for request in requests] == [
        "Asia/Kolkata",
        "Asia/Kathmandu",
    ]


def test_historical_service_does_not_compare_daily_values_without_forecast_timezone():
    weather = _weather().model_copy(update={
        "location": _weather().location.model_copy(update={"timezone": None}),
    })
    service = HistoricalBaselineService(_provider(
        lambda _request: pytest.fail("Historical provider must not be called without timezone"),
    ))

    report = service.calculate(weather)

    assert report.status == "UNAVAILABLE"
    assert "timezone unavailable" in report.quality


def test_historical_service_force_refresh_bypasses_baseline_cache():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_historical_payload())

    service = HistoricalBaselineService(_provider(handler))
    weather = _weather()
    service.calculate(weather)
    service.calculate(weather, force_refresh=True)

    assert len(requests) == 2


def test_historical_provider_does_not_hide_failures_or_incompatible_units():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    try:
        _provider(timeout).get_daily_baseline(18.52, 73.85)
    except OpenMeteoHistoricalError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("Expected historical timeout to remain explicit")

    payload = _historical_payload()
    payload["daily_units"]["temperature_2m_max"] = "°F"
    try:
        _provider(lambda _request: httpx.Response(200, json=payload)).get_daily_baseline(18.52, 73.85)
    except OpenMeteoHistoricalError as exc:
        assert "incompatible units" in str(exc)
    else:
        raise AssertionError("Expected incompatible units to be rejected")


def test_weather_evidence_route_keeps_weather_when_historical_provider_fails(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")

    def historical_failure(_weather):
        raise OpenMeteoHistoricalError("archive offline")

    class FailedHistoricalService:
        calculate = staticmethod(historical_failure)

    class FakeWeatherProvider:
        def get_weather_evidence_for_location(self, _location):
            return _weather()

    with TestClient(app) as client:
        app.state.weather_evidence_service = WeatherEvidenceService(FakeWeatherProvider())
        app.state.historical_baseline_service = FailedHistoricalService()
        response = client.get(
            "/api/v1/weather/evidence",
            params={"latitude": 18.52, "longitude": 73.85, "name": "Pune"},
        )

    assert response.status_code == 200
    assert response.json()["historical_anomalies"]["status"] == "UNAVAILABLE"
    assert "Open-Meteo" in response.json()["provenance"]["source"]