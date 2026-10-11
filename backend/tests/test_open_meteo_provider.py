from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from threading import Barrier

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-engine"))
sys.path.insert(0, str(ROOT / "ai"))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings
from app.main import app
from app.providers.demo import DemoDataStore, DemoRainfallProvider
from app.providers.open_meteo import OpenMeteoError, OpenMeteoRainfallProvider
from app.services.weather_evidence_service import WeatherEvidenceService


def _times(start: datetime | None = None, count: int = 26) -> list[str]:
    current = start or (
        datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        + timedelta(hours=2)
    )
    return [(current + timedelta(hours=index)).isoformat() for index in range(count)]


def _payload(
    *,
    times: list[str] | None = None,
    precipitation: list[float | None] | None = None,
    unit: str = "mm",
) -> dict:
    values = precipitation if precipitation is not None else [0.5] * 26
    return {
        "hourly_units": {"time": "iso8601", "precipitation": unit},
        "hourly": {
            "time": times if times is not None else _times(count=len(values)),
            "precipitation": values,
        },
    }


def _weather_payload(*, missing_variable: str | None = None) -> dict:
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    zone = timezone(timedelta(hours=13))
    times = [(start + timedelta(hours=index)).astimezone(zone).isoformat() for index in range(168)]
    hourly_variables = {
        "temperature_2m": ("°C", 24.0),
        "apparent_temperature": ("°C", 25.0),
        "relative_humidity_2m": ("%", 65.0),
        "precipitation": ("mm", 1.5),
        "rain": ("mm", 1.0),
        "showers": ("mm", 0.5),
        "precipitation_probability": ("%", 80.0),
        "wind_speed_10m": ("km/h", 12.0),
        "wind_gusts_10m": ("km/h", 20.0),
        "wind_direction_10m": ("°", 180.0),
        "cloud_cover": ("%", 55.0),
        "visibility": ("m", 10000.0),
        "weather_code": ("wmo code", 61),
        "wet_bulb_temperature_2m": ("°C", 20.0),
        "uv_index": ("", 4.0),
        "shortwave_radiation": ("W/m²", 120.0),
        "et0_fao_evapotranspiration": ("mm", 0.12),
        "soil_moisture_0_to_1cm": ("m³/m³", 0.24),
    }
    hourly = {"time": times}
    hourly_units = {"time": "iso8601"}
    for name, (unit, value) in hourly_variables.items():
        if name != missing_variable:
            hourly[name] = [value] * len(times)
            hourly_units[name] = unit
    daily_variables = {
        "temperature_2m_max": ("°C", 26.0),
        "temperature_2m_min": ("°C", 18.0),
        "apparent_temperature_max": ("°C", 27.0),
        "apparent_temperature_min": ("°C", 19.0),
        "precipitation_sum": ("mm", 18.0),
        "rain_sum": ("mm", 16.0),
        "showers_sum": ("mm", 2.0),
        "precipitation_probability_max": ("%", 90),
        "wind_speed_10m_max": ("km/h", 25.0),
        "wind_gusts_10m_max": ("km/h", 35.0),
        "wind_direction_10m_dominant": ("°", 220),
        "weather_code": ("wmo code", 61),
        "uv_index_max": ("", 7.0),
        "shortwave_radiation_sum": ("MJ/m²", 12.0),
        "et0_fao_evapotranspiration": ("mm", 3.0),
    }
    daily = {"time": [(datetime.now(timezone.utc).date() + timedelta(days=index)).isoformat() for index in range(7)]}
    daily_units = {"time": "iso8601"}
    for name, (unit, value) in daily_variables.items():
        daily[name] = [value] * 7
        daily_units[name] = unit
    return {
        "latitude": -36.85,
        "longitude": 174.76,
        "elevation": 14.0,
        "generationtime_ms": 1.25,
        "timezone": "Pacific/Auckland",
        "utc_offset_seconds": 46800,
        "hourly": hourly,
        "hourly_units": hourly_units,
        "daily": daily,
        "daily_units": daily_units,
    }


def _provider(handler) -> OpenMeteoRainfallProvider:
    store = DemoDataStore(ROOT / "data" / "demo")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenMeteoRainfallProvider(store, client=client)


def test_open_meteo_normalizes_forecast_and_uses_location_coordinates():
    payload = _payload(precipitation=[1.25] + [0.5] * 25)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    result = _provider(handler).get_rainfall_observations("sikkim")
    params = dict(requests[0].url.params)

    assert params["latitude"] == "27.533"
    assert params["longitude"] == "88.512"
    assert params["hourly"] == "precipitation"
    assert result["source"].startswith("Open-Meteo")
    assert result["dataProvenance"] == "LIVE"
    assert result["dataType"] == "forecast"
    assert result["timestamp"] == result["retrievedAt"]
    assert result["location"]["name"] == "Sikkim"
    assert result["status"] == "available"
    intensity_provenance = {
        "source": "Open-Meteo",
        "source_url": "https://open-meteo.com/",
        "location_id": "sikkim",
        "latitude": 27.533,
        "longitude": 88.512,
        "data_type": "FORECAST",
        "forecast_time": payload["hourly"]["time"][0],
        "retrieval_time": result["retrievedAt"],
        "value": 1.25,
        "unit": "mm/h",
        "quality": result["dataQuality"],
        "status": "forecast",
    }
    assert result["values"]["rainfallIntensityMmHr"] == {
        "value": 1.25,
        "unit": "mm/h",
        "status": "forecast",
        "dataType": "forecast",
        "validTime": payload["hourly"]["time"][0],
        "provenance": intensity_provenance,
    }
    assert result["values"]["cumulativeRainfallMm24h"]["value"] == 12.75
    assert result["values"]["cumulativeRainfallMm24h"]["unit"] == "mm/24h"
    assert result["values"]["cumulativeRainfallMm24h"]["validTime"] == payload["hourly"]["time"][0]
    assert "not a ground-based observation" in result["dataQuality"]
    assert result["provenance"]["source"] == "Open-Meteo"
    assert result["provenance"]["source_url"] == "https://open-meteo.com/"
    assert result["provenance"]["location_id"] == "sikkim"
    assert result["provenance"]["latitude"] == 27.533
    assert result["provenance"]["longitude"] == 88.512
    assert result["provenance"]["data_type"] == "FORECAST"
    assert result["provenance"]["retrieval_time"] == result["retrievedAt"]
    intensity_provenance = result["values"]["rainfallIntensityMmHr"]["provenance"]
    assert intensity_provenance["forecast_time"] == payload["hourly"]["time"][0]
    assert intensity_provenance["value"] == 1.25
    assert intensity_provenance["unit"] == "mm/h"
    assert intensity_provenance["quality"] == result["dataQuality"]
    assert result["measurements"][0] == intensity_provenance


def test_open_meteo_accepts_utc_timestamps_without_offset():
    times = [value.replace("+00:00", "") for value in _times()]
    result = _provider(
        lambda _request: httpx.Response(200, json=_payload(times=times))
    ).get_rainfall_observations("sikkim")

    assert result["status"] == "available"
    assert result["values"]["rainfallIntensityMmHr"]["validTime"].endswith("+00:00")


def test_open_meteo_searches_worldwide_locations():
    requests = []
    payload = {
        "results": [{
            "id": 123,
            "name": "Auckland",
            "latitude": -36.85,
            "longitude": 174.76,
            "admin1": "Auckland Region",
            "country": "New Zealand",
        }]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    locations = _provider(handler).search_locations("Auckland")

    assert dict(requests[0].url.params)["name"] == "Auckland"
    assert locations == [{
        "id": "open-meteo-123",
        "name": "Auckland",
        "state": "Auckland Region",
        "country": "New Zealand",
        "lat": -36.85,
        "lng": 174.76,
    }]


def test_open_meteo_fetches_forecast_for_arbitrary_coordinates():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_payload())

    location = {
        "id": "global-point",
        "name": "Selected point",
        "state": "Selected map point",
        "lat": -36.85,
        "lng": 174.76,
    }
    result = _provider(handler).get_rainfall_for_location(location)

    params = dict(requests[0].url.params)
    assert params["latitude"] == "-36.85"
    assert params["longitude"] == "174.76"
    assert result["status"] == "available"
    assert result["dataProvenance"] == "LIVE"
    assert result["location"]["name"] == "Selected point"
    assert result["provenance"]["latitude"] == -36.85


def test_open_meteo_returns_structured_weather_evidence_and_metadata():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_weather_payload())

    result = _provider(handler).get_weather_evidence_for_location({
        "id": "auckland",
        "name": "Auckland",
        "state": "Auckland Region",
        "country": "New Zealand",
        "lat": -36.85,
        "lng": 174.76,
    })
    params = dict(requests[0].url.params)

    assert params["forecast_days"] == "7"
    assert "wet_bulb_temperature_2m" in params["hourly"]
    assert "soil_moisture_0_to_1cm" in params["hourly"]
    assert result.status == "AVAILABLE"
    assert result.location.timezone == "Pacific/Auckland"
    assert result.location.elevation_m == 14.0
    assert result.location.grid_latitude == -36.85
    assert result.forecast["temperature"].value == 24.0
    assert result.forecast["temperature"].unit == "°C"
    assert result.forecast["wet_bulb_temperature"].value == 20.0
    assert result.forecast["precipitation_probability"].value == 80.0
    assert result.hourly.values["precipitation"][0] == 1.5
    assert len(result.daily.times) == 7
    assert result.provenance.data_type == "FORECAST"
    assert result.provenance.model_id is None
    assert result.provenance.forecast_generated_at is None
    assert result.provenance.retrieved_at
    assert any("exact selected model ID" in value for value in result.provenance.limitations)


def test_open_meteo_weather_evidence_marks_missing_variables_unavailable():
    result = _provider(
        lambda _request: httpx.Response(
            200,
            json=_weather_payload(missing_variable="wet_bulb_temperature_2m"),
        )
    ).get_weather_evidence_for_location({
        "id": "auckland",
        "name": "Auckland",
        "lat": -36.85,
        "lng": 174.76,
    })

    assert result.status == "DEGRADED"
    assert result.forecast["wet_bulb_temperature"].status == "UNAVAILABLE"
    assert result.forecast["wet_bulb_temperature"].value is None
    assert result.hourly.values["wet_bulb_temperature_2m"] == [None] * 168


def test_weather_evidence_rejects_incompatible_units_and_marks_daily_gaps():
    payload = _weather_payload()
    payload["hourly_units"]["precipitation"] = "inch"
    payload["daily_units"]["precipitation_sum"] = "inch"

    result = _provider(
        lambda _request: httpx.Response(200, json=payload)
    ).get_weather_evidence_for_location({
        "id": "auckland",
        "name": "Auckland",
        "lat": -36.85,
        "lng": 174.76,
    })

    assert result.status == "DEGRADED"
    assert result.forecast["precipitation"].value is None
    assert result.hourly.values["precipitation"] == [None] * 168
    assert result.daily.values["precipitation_sum"] == [None] * 7
    assert result.daily.units["precipitation_sum"] == "inch"
    assert result.daily.values["rain_sum"] == [16.0] * 7


def test_weather_provider_rejects_local_timestamps_without_utc_offset():
    payload = _weather_payload()
    payload.pop("utc_offset_seconds")
    payload["hourly"]["time"] = [
        datetime.fromisoformat(value).replace(tzinfo=None).isoformat()
        for value in payload["hourly"]["time"]
    ]

    with pytest.raises(OpenMeteoError, match="missing a valid UTC offset"):
        _provider(
            lambda _request: httpx.Response(200, json=payload)
        ).get_weather_evidence_for_location({
            "id": "auckland",
            "name": "Auckland",
            "lat": -36.85,
            "lng": 174.76,
        })


def test_weather_evidence_cache_reuses_request_and_preserves_selected_place_label():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_weather_payload())

    service = WeatherEvidenceService(_provider(handler))
    auckland = {"id": "auckland", "name": "Auckland", "state": "Auckland Region", "country": "New Zealand", "lat": -36.85, "lng": 174.76}
    nearby_label = {**auckland, "name": "Selected point", "state": "Map point"}

    service.get_for_location(auckland)
    cached = service.get_for_location(nearby_label)

    assert len(requests) == 1
    assert cached.location.name == "Selected point"
    assert cached.location.requested_latitude == -36.85


def test_weather_evidence_cache_refreshes_when_selected_forecast_hour_passes():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_weather_payload())

    service = WeatherEvidenceService(_provider(handler))
    location = {
        "id": "auckland",
        "name": "Auckland",
        "state": "Auckland Region",
        "country": "New Zealand",
        "lat": -36.85,
        "lng": 174.76,
    }
    evidence = service.get_for_location(location)
    stale_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    stale_forecast = {
        key: measurement.model_copy(update={
            "forecast_time": stale_time,
            "provenance": {**measurement.provenance, "forecast_time": stale_time},
        })
        if key in {"temperature", "humidity"}
        else measurement
        for key, measurement in evidence.forecast.items()
    }
    stale_evidence = evidence.model_copy(update={"forecast": stale_forecast})
    service._cache[(-36.85, 174.76)] = (datetime.now(timezone.utc), stale_evidence)

    refreshed = service.get_for_location(location)

    assert len(requests) == 2
    assert refreshed.forecast["temperature"].forecast_time != stale_time
    assert datetime.fromisoformat(
        refreshed.forecast["temperature"].forecast_time
    ) > datetime.now(timezone.utc)


def test_weather_evidence_force_refresh_bypasses_coordinate_cache():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_weather_payload())

    service = WeatherEvidenceService(_provider(handler))
    location = {"id": "auckland", "name": "Auckland", "state": "Auckland Region", "country": "New Zealand", "lat": -36.85, "lng": 174.76}
    service.get_for_location(location)
    service.get_for_location(location, force_refresh=True)

    assert len(requests) == 2


class StaticWeatherEvidenceProvider:
    def __init__(
        self,
        provider_name: str,
        evidence=None,
        error: Exception | None = None,
        barrier: Barrier | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.evidence = evidence
        self.error = error
        self.barrier = barrier

    def get_weather_evidence_for_location(self, _location):
        if self.barrier is not None:
            self.barrier.wait(timeout=3)
        if self.error is not None:
            raise self.error
        return self.evidence


def test_enabled_weather_providers_are_requested_concurrently():
    location = {
        "id": "auckland",
        "name": "Auckland",
        "state": "Auckland Region",
        "country": "New Zealand",
        "lat": -36.85,
        "lng": 174.76,
    }
    evidence = _provider(
        lambda _request: httpx.Response(200, json=_weather_payload())
    ).get_weather_evidence_for_location(location)
    barrier = Barrier(2)
    service = WeatherEvidenceService(
        StaticWeatherEvidenceProvider("Open-Meteo", evidence, barrier=barrier),
        secondary_provider=StaticWeatherEvidenceProvider(
            "OpenWeather",
            evidence,
            barrier=barrier,
        ),
    )

    result = service.get_for_location(location)

    assert [item.provider for item in result.provider_status] == ["Open-Meteo", "OpenWeather"]
    assert result.provider_status[0].status == "AVAILABLE"
    assert result.provider_status[1].status == "AVAILABLE"
    assert result.provider_status[1].fields
    assert not result.provider_status[1].used_fields


def test_secondary_weather_provider_remains_usable_when_primary_fails():
    location = {
        "id": "auckland",
        "name": "Auckland",
        "state": "Auckland Region",
        "country": "New Zealand",
        "lat": -36.85,
        "lng": 174.76,
    }
    evidence = _provider(
        lambda _request: httpx.Response(200, json=_weather_payload())
    ).get_weather_evidence_for_location(location)
    secondary_evidence = evidence.model_copy(update={
        "provenance": evidence.provenance.model_copy(update={"source": "OpenWeather"}),
    })
    service = WeatherEvidenceService(
        StaticWeatherEvidenceProvider("Open-Meteo", error=OpenMeteoError("primary unavailable")),
        secondary_provider=StaticWeatherEvidenceProvider("OpenWeather", secondary_evidence),
    )

    result = service.get_for_location(location)

    assert result.status == "DEGRADED"
    assert result.provenance.source == "OpenWeather"
    assert result.provider_status[0].status == "UNAVAILABLE"
    assert result.provider_status[1].used_fields
    assert result.forecast["temperature"].value is not None


def test_weather_evidence_uses_cached_forecast_when_both_refreshes_fail():
    location = {
        "id": "auckland",
        "name": "Auckland",
        "state": "Auckland Region",
        "country": "New Zealand",
        "lat": -36.85,
        "lng": 174.76,
    }
    evidence = _provider(
        lambda _request: httpx.Response(200, json=_weather_payload())
    ).get_weather_evidence_for_location(location)

    class FailAfterFirstRequest:
        provider_name = "Open-Meteo"

        def __init__(self):
            self.calls = 0

        def get_weather_evidence_for_location(self, _location):
            self.calls += 1
            if self.calls == 1:
                return evidence
            raise OpenMeteoError("provider unavailable")

    primary = FailAfterFirstRequest()
    secondary = StaticWeatherEvidenceProvider(
        "OpenWeather",
        error=OpenMeteoError("provider unavailable"),
    )
    service = WeatherEvidenceService(primary, secondary_provider=secondary)
    service.get_for_location(location)

    result = service.get_for_location(location, force_refresh=True)

    assert result.status == "DEGRADED"
    assert result.forecast["temperature"].value == evidence.forecast["temperature"].value
    assert result.provenance.retrieved_at == evidence.provenance.retrieved_at
    assert "showing previously retrieved forecast evidence" in result.provenance.quality.lower()
    assert [item.status for item in result.provider_status] == ["UNAVAILABLE", "UNAVAILABLE"]


def test_global_weather_evidence_route_returns_structured_forecast(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    with TestClient(app) as client:
        weather_provider = _provider(
            lambda _request: httpx.Response(200, json=_weather_payload())
        )
        app.state.rainfall_provider = weather_provider
        app.state.weather_evidence_service = WeatherEvidenceService(weather_provider)
        app.state.historical_baseline_service = None
        response = client.get(
            "/api/v1/weather/evidence",
            params={
                "latitude": -36.85,
                "longitude": 174.76,
                "name": "Auckland",
                "admin1": "Auckland Region",
                "country": "New Zealand",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "AVAILABLE"
    assert body["forecast"]["temperature"]["value"] == 24.0
    assert body["forecast"]["temperature"]["data_type"] == "FORECAST"
    assert body["location"]["country"] == "New Zealand"
    assert body["provenance"]["source"] == "Open-Meteo"
    assert body["historical_anomalies"]["status"] == "UNAVAILABLE"


def test_global_weather_routes_use_live_provider(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    with TestClient(app) as client:
        app.state.rainfall_provider = _provider(
            lambda _request: httpx.Response(200, json={"results": [{
                "id": 123,
                "name": "Auckland",
                "latitude": -36.85,
                "longitude": 174.76,
                "admin1": "Auckland Region",
                "country": "New Zealand",
            }]})
        )
        search = client.get("/api/v1/weather/search", params={"query": "Auckland"})
        forecast_provider = _provider(
            lambda _request: httpx.Response(200, json=_payload())
        )
        app.state.rainfall_provider = forecast_provider
        forecast = client.get(
            "/api/v1/weather/forecast",
            params={
                "latitude": -36.85,
                "longitude": 174.76,
                "name": "Auckland",
                "admin1": "Auckland Region",
                "country": "New Zealand",
            },
        )

    assert search.status_code == 200
    assert search.json()["locations"][0]["country"] == "New Zealand"
    assert forecast.status_code == 200
    assert forecast.json()["dataProvenance"] == "LIVE"
    assert forecast.json()["location"]["name"] == "Auckland"


def test_missing_hourly_values_are_unavailable_not_zero():
    payload = _payload(precipitation=[None] + [None] * 25)
    result = _provider(lambda _request: httpx.Response(200, json=payload)).get_rainfall_observations(
        "sikkim"
    )

    assert result["status"] == "unavailable"
    assert result["values"]["rainfallIntensityMmHr"]["value"] is None
    assert result["values"]["cumulativeRainfallMm24h"]["value"] is None
    assert result["measurements"][0]["value"] is None
    assert result["measurements"][0]["status"] == "unavailable"


def test_cumulative_rainfall_retains_calculation_precision():
    result = _provider(
        lambda _request: httpx.Response(
            200,
            json=_payload(precipitation=[0.123] * 24 + [0.0] * 2),
        )
    ).get_rainfall_observations("sikkim")

    assert result["values"]["cumulativeRainfallMm24h"]["value"] == pytest.approx(2.952)


def test_provider_rejects_stale_and_invalid_units():
    old_times = _times(
        datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        - timedelta(days=2),
        count=26,
    )
    stale = _provider(lambda _request: httpx.Response(200, json=_payload(times=old_times)))
    incompatible = _provider(
        lambda _request: httpx.Response(200, json=_payload(unit="inches"))
    )

    assert "stale" in stale.get_rainfall_observations("sikkim")["dataQuality"]
    assert incompatible.get_rainfall_observations("sikkim")["status"] == "unavailable"


def test_provider_rejects_invalid_timestamps_and_out_of_range_values():
    invalid_time = _times()
    invalid_time[0] = "not-a-timestamp"
    invalid = _provider(
        lambda _request: httpx.Response(
            200, json=_payload(times=invalid_time)
        )
    ).get_rainfall_observations("sikkim")
    out_of_range = _provider(
        lambda _request: httpx.Response(
            200, json=_payload(precipitation=[600.0] + [50.0] * 25)
        )
    ).get_rainfall_observations("sikkim")

    assert invalid["status"] == "unavailable"
    assert out_of_range["values"]["rainfallIntensityMmHr"]["value"] is None
    assert out_of_range["values"]["cumulativeRainfallMm24h"]["value"] is None


def test_provider_handles_rate_limit_timeout_and_malformed_json():
    limited = _provider(
        lambda _request: httpx.Response(429, json={"detail": "private server content"})
    ).get_rainfall_observations("sikkim")

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    timed_out = _provider(timeout).get_rainfall_observations("sikkim")
    malformed = _provider(
        lambda _request: httpx.Response(200, content=b"{", headers={"content-type": "application/json"})
    ).get_rainfall_observations("sikkim")

    assert limited["status"] == "unavailable"
    assert "rate limit" in limited["dataQuality"]
    assert "private server content" not in limited["dataQuality"]
    assert "timed out" in timed_out["dataQuality"]
    assert "malformed JSON" in malformed["dataQuality"]


def test_real_mode_endpoint_uses_forecast_and_fails_without_fallback(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    with TestClient(app) as client:
        assert isinstance(app.state.rainfall_provider, OpenMeteoRainfallProvider)
        risk_before = client.get("/api/v1/risk/sikkim")
        assert risk_before.status_code == 503
        assert "riskScore" not in risk_before.json()
        app.state.rainfall_provider = _provider(
            lambda _request: httpx.Response(200, json=_payload())
        )
        response = client.get("/api/v1/data/rainfall/sikkim")
        risk_after = client.get("/api/v1/risk/sikkim")
        assert response.status_code == 200
        assert response.json()["dataType"] == "forecast"
        assert response.json()["values"]["rainfallIntensityMmHr"]["status"] == "forecast"
        assert response.json()["provenance"]["data_type"] == "FORECAST"
        assert response.json()["provenance"]["retrieval_time"]
        assert risk_after.status_code == 503
        assert risk_after.json() == risk_before.json()

        app.state.rainfall_provider = _provider(
            lambda _request: httpx.Response(503, json={"detail": "upstream unavailable"})
        )
        unavailable = client.get("/api/v1/data/rainfall/sikkim")
        risk_after_provider_failure = client.get("/api/v1/risk/sikkim")

    assert unavailable.status_code == 503
    assert "demo" not in unavailable.json()["detail"].lower()
    assert risk_after_provider_failure.status_code == 503
    assert risk_after_provider_failure.json() == risk_before.json()
    monkeypatch.setattr(settings, "data_mode", "demo")


def test_demo_mode_endpoint_keeps_demo_provider(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "demo")
    with TestClient(app) as client:
        assert isinstance(app.state.rainfall_provider, DemoRainfallProvider)
        response = client.get("/api/v1/data/rainfall/sikkim")

    assert response.status_code == 200
    assert response.json()["dataProvenance"] == "DEMO"
    assert response.json()["provenance"]["data_type"] == "DEMO"
    assert response.json()["provenance"]["location_id"] == "sikkim"
    assert all(
        measurement["data_type"] == "DEMO"
        for measurement in response.json()["measurements"]
    )


def test_real_provider_failure_returns_503_without_demo_fallback(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    with TestClient(app) as client:
        app.state.rainfall_provider = _provider(
            lambda _request: httpx.Response(429, json={"detail": "rate limited"})
        )
        response = client.get("/api/v1/data/rainfall/delhi")

    assert response.status_code == 503
    assert "demo" not in response.text.lower()
