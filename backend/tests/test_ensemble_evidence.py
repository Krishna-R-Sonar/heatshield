from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-engine"))
sys.path.insert(0, str(ROOT / "ai"))
sys.path.insert(0, str(ROOT / "backend"))

from app.providers.open_meteo_ensemble import (
    ENSEMBLE_CLASSIFICATION_METHOD,
    OpenMeteoEnsembleError,
    OpenMeteoEnsembleProvider,
)
from app.config import settings
from app.main import app
from app.services.ensemble_evidence_service import EnsembleEvidenceService


def _ensemble_payload(*, member_count: int = 10, temperature_members: bool = True) -> dict:
    start = datetime.combine(
        datetime.now(timezone.utc).date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    times = [(start + timedelta(hours=index)).replace(tzinfo=None).isoformat() for index in range(72)]
    hourly: dict[str, list[float] | list[str]] = {"time": times}
    units = {"time": "iso8601", "temperature_2m": "°C", "precipitation": "mm"}
    for member in range(member_count):
        suffix = f"member{member + 1:02d}"
        if temperature_members:
            key = f"temperature_2m_{suffix}"
            hourly[key] = [20.0 + member] * len(times)
            units[key] = "°C"
        key = f"precipitation_{suffix}"
        hourly[key] = [member / 10] * len(times)
        units[key] = "mm"
    return {
        "latitude": 18.5,
        "longitude": 73.75,
        "elevation": 561.0,
        "timezone": "GMT",
        "generationtime_ms": 1.1,
        "hourly": hourly,
        "hourly_units": units,
    }


def _provider(handler) -> OpenMeteoEnsembleProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenMeteoEnsembleProvider(client=client)


def test_ensemble_provider_summarizes_temperature_and_24h_precipitation():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_ensemble_payload())

    evidence = _provider(handler).get_evidence(18.52, 73.85)
    params = dict(requests[0].url.params)

    assert params["models"] == "ecmwf_ifs025"
    assert params["forecast_hours"] == "72"
    assert params["hourly"] == "temperature_2m,precipitation"
    assert evidence.status == "AVAILABLE"
    assert evidence.member_count == 10
    assert len(evidence.daily) == 3
    first_day = evidence.daily[0]
    assert first_day.temperature_max.member_count == 10
    assert first_day.temperature_max.median == 24.5
    assert first_day.temperature_max.p25 == 22.25
    assert first_day.temperature_max.p75 == 26.75
    assert first_day.temperature_max.spread_iqr == 4.5
    assert first_day.temperature_max.uncertainty == "MODERATE"
    assert first_day.precipitation_sum.median == 10.8
    assert first_day.precipitation_sum.p25 == 5.4
    assert first_day.precipitation_sum.p75 == pytest.approx(16.2)
    assert first_day.precipitation_sum.uncertainty == "HIGH"
    assert evidence.provenance.model_id == "ecmwf_ifs025"
    assert evidence.provenance.grid_latitude == 18.5
    assert "not hazard thresholds or probabilities of disaster" in evidence.provenance.classification_method


def test_ensemble_provider_marks_insufficient_member_spread_unavailable():
    evidence = _provider(
        lambda _request: httpx.Response(
            200,
            json=_ensemble_payload(member_count=1, temperature_members=False),
        )
    ).get_evidence(18.52, 73.85)

    assert evidence.status == "UNAVAILABLE"
    assert evidence.daily[0].temperature_max.status == "UNAVAILABLE"
    assert evidence.daily[0].precipitation_sum.status == "UNAVAILABLE"
    assert evidence.daily[0].precipitation_sum.uncertainty == "UNAVAILABLE"


def test_ensemble_provider_keeps_zero_rainfall_as_valid_and_failures_explicit():
    payload = _ensemble_payload(member_count=3, temperature_members=False)
    for member in range(1, 4):
        payload["hourly"][f"precipitation_member{member:02d}"] = [0.0] * 72
    evidence = _provider(lambda _request: httpx.Response(200, json=payload)).get_evidence(18.52, 73.85)

    assert evidence.daily[0].precipitation_sum.status == "AVAILABLE"
    assert evidence.daily[0].precipitation_sum.median == 0.0
    assert evidence.daily[0].precipitation_sum.uncertainty == "LOW"
    assert "No ensemble spread at two-decimal display precision" in (
        evidence.daily[0].precipitation_sum.uncertainty_reason
    )

    with pytest.raises(OpenMeteoEnsembleError, match="timed out"):
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timeout", request=request)
        _provider(timeout).get_evidence(18.52, 73.85)


def test_ensemble_provider_preserves_sub_display_precision_values():
    payload = _ensemble_payload(member_count=3)
    member_values = [0.001, 0.002, 0.004]
    for member, value in enumerate(member_values, start=1):
        payload["hourly"][f"temperature_2m_member{member:02d}"] = [value] * 72
        precipitation = [0.0] * 72
        precipitation[0] = value
        payload["hourly"][f"precipitation_member{member:02d}"] = precipitation

    evidence = _provider(lambda _request: httpx.Response(200, json=payload)).get_evidence(18.52, 73.85)
    temperature = evidence.daily[0].temperature_max
    precipitation = evidence.daily[0].precipitation_sum

    assert temperature.p25 == pytest.approx(0.0015)
    assert temperature.p75 == pytest.approx(0.003)
    assert temperature.spread_iqr == pytest.approx(0.0015)
    assert "rounds to 0.00" not in temperature.uncertainty_reason
    assert "IQR is 0.0015°C" in temperature.uncertainty_reason
    assert precipitation.p25 == pytest.approx(0.0015)
    assert precipitation.p75 == pytest.approx(0.003)
    assert precipitation.spread_iqr == pytest.approx(0.0015)
    assert "rounds to 0.00" not in precipitation.uncertainty_reason
    assert precipitation.uncertainty == "MODERATE"
    assert "(>0.25 to 0.75 display band)" in precipitation.uncertainty_reason


def test_uncertainty_classification_method_is_explicit_and_display_only():
    assert "display-only spread descriptors" in ENSEMBLE_CLASSIFICATION_METHOD
    assert "not hazard thresholds" in ENSEMBLE_CLASSIFICATION_METHOD


def test_ensemble_service_caches_same_coordinate_read():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_ensemble_payload())

    service = EnsembleEvidenceService(_provider(handler))
    service.get_for_location(18.5204, 73.8567)
    service.get_for_location(18.5204, 73.8567)

    assert len(requests) == 1


def test_ensemble_service_force_refresh_bypasses_coordinate_cache():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_ensemble_payload())

    service = EnsembleEvidenceService(_provider(handler))
    service.get_for_location(18.5204, 73.8567)
    service.get_for_location(18.5204, 73.8567, force_refresh=True)

    assert len(requests) == 2


def test_weather_ensemble_route_is_real_mode_only(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    with TestClient(app) as client:
        app.state.ensemble_evidence_service = EnsembleEvidenceService(
            _provider(lambda _request: httpx.Response(200, json=_ensemble_payload()))
        )
        response = client.get(
            "/api/v1/weather/ensemble",
            params={"latitude": 18.52, "longitude": 73.85},
        )

    assert response.status_code == 200
    assert response.json()["provenance"]["data_type"] == "ENSEMBLE_FORECAST"
    assert response.json()["daily"][0]["precipitation_sum"]["p25"] == 5.4


def test_weather_ensemble_route_is_unavailable_in_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "demo")
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/weather/ensemble",
            params={"latitude": 18.52, "longitude": 73.85},
        )

    assert response.status_code == 503
    assert "real data mode" in response.json()["detail"]