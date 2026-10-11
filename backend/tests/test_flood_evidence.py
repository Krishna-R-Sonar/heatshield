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
from app.providers.open_meteo_flood import (
    FLOOD_VARIABLES,
    OpenMeteoFloodError,
    OpenMeteoFloodProvider,
)
from app.services.flood_evidence_service import FloodEvidenceService


def _flood_payload(*, discharge: list[float | None] | None = None) -> dict:
    today = datetime.now(timezone.utc).date()
    times = [
        "2022-07-30",
        "2022-07-31",
        "2022-08-01",
        (today - timedelta(days=1)).isoformat(),
        today.isoformat(),
        (today + timedelta(days=1)).isoformat(),
    ]
    values: dict[str, list[float | None]] = {
        "river_discharge": discharge or [4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
        "river_discharge_mean": [None, None, None, None, 8.2, 9.2],
        "river_discharge_median": [None, None, None, None, 8.0, 9.0],
        "river_discharge_min": [None, None, None, None, 6.0, 7.0],
        "river_discharge_max": [None, None, None, None, 11.0, 12.0],
        "river_discharge_p25": [None, None, None, None, 7.0, 8.0],
        "river_discharge_p75": [None, None, None, None, 9.0, 10.0],
    }
    return {
        "latitude": 18.525,
        "longitude": 73.875,
        "elevation": 561.0,
        "generationtime_ms": 0.8,
        "timezone": "GMT",
        "daily_units": {"time": "iso8601", **{name: "m³/s" for name in FLOOD_VARIABLES}},
        "daily": {"time": times, **values},
    }


def _provider(handler) -> OpenMeteoFloodProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenMeteoFloodProvider(client=client)


def test_flood_provider_normalizes_glofas_grid_and_ensemble_statistics():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_flood_payload())

    evidence = _provider(handler).get_evidence(18.5204, 73.8567)
    params = dict(requests[0].url.params)
    historical = evidence.daily[0]
    forecast = evidence.daily[4]

    assert params["latitude"] == "18.5204"
    assert params["longitude"] == "73.8567"
    assert params["past_days"] == "7"
    assert params["forecast_days"] == "30"
    assert params["cell_selection"] == "nearest"
    assert "ensemble" not in params
    assert evidence.status == "DEGRADED"
    assert evidence.location.requested_latitude == 18.5204
    assert evidence.location.grid_latitude == 18.525
    assert evidence.location.grid_longitude == 73.875
    assert evidence.location.river_association == "UNVERIFIED"
    assert historical.data_type == "REANALYSIS"
    assert historical.discharge.value == 4.0
    assert historical.p25.status == "UNAVAILABLE"
    assert forecast.data_type == "FORECAST"
    assert forecast.discharge.value == 8.0
    assert forecast.mean.value == 8.2
    assert forecast.median.value == 8.0
    assert forecast.minimum.value == 6.0
    assert forecast.maximum.value == 11.0
    assert forecast.p25.value == 7.0
    assert forecast.p75.value == 9.0
    assert forecast.discharge.unit == "m³/s"
    assert evidence.provenance.model.startswith("GloFAS v4")
    assert evidence.provenance.model_version_in_payload is False
    assert evidence.provenance.returned_historical_period
    assert evidence.provenance.returned_forecast_period
    assert evidence.provenance.forecast_horizon_days == 2
    post_reanalysis_past = next(point for point in evidence.daily if point.time == "2022-08-01")
    assert post_reanalysis_past.data_type == "UNAVAILABLE"
    assert post_reanalysis_past.discharge.value is None
    assert evidence.provenance.retrieved_at
    assert any("not an actual river level" in item for item in evidence.provenance.limitations)


def test_flood_provider_keeps_no_river_values_unavailable_not_zero():
    evidence = _provider(
        lambda _request: httpx.Response(
            200,
            json=_flood_payload(discharge=[None] * 6),
        )
    ).get_evidence(0.0, 0.0)

    assert evidence.status == "UNAVAILABLE"
    assert all(point.discharge.value is None for point in evidence.daily)
    assert all(point.discharge.status == "UNAVAILABLE" for point in evidence.daily)
    assert "no local river evidence" in evidence.provenance.quality


def test_flood_spread_preserves_precision_and_is_not_a_risk_interpretation():
    tiny_spread = OpenMeteoFloodProvider._spread_summary(
        "FORECAST",
        median=100.0,
        p25=50.0,
        p75=50.004,
    )
    zero_median = OpenMeteoFloodProvider._spread_summary(
        "FORECAST",
        median=0.0,
        p25=0.0,
        p75=0.004,
    )
    exact_boundary = OpenMeteoFloodProvider._spread_summary(
        "FORECAST",
        median=0.002,
        p25=0.0015,
        p75=0.003,
    )

    assert tiny_spread["discharge_spread_iqr"] == pytest.approx(0.004)
    assert "rounds to 0.00" not in tiny_spread["uncertainty_reason"]
    assert tiny_spread["uncertainty"] == "LOW"
    assert zero_median["uncertainty"] == "HIGH"
    assert "nonzero IQR" in zero_median["uncertainty_reason"]
    assert zero_median["discharge_spread_iqr"] == pytest.approx(0.004)
    assert exact_boundary["uncertainty"] == "MODERATE"
    assert "IQR/median is 0.75" in exact_boundary["uncertainty_reason"]


def test_flood_provider_rejects_incompatible_units_and_invalid_coordinates():
    payload = _flood_payload()
    payload["daily_units"]["river_discharge"] = "km³/s"
    incompatible = _provider(lambda _request: httpx.Response(200, json=payload))
    assert incompatible.get_evidence(10.0, 10.0).status == "UNAVAILABLE"

    with pytest.raises(OpenMeteoFloodError, match="coordinates"):
        incompatible.get_evidence(91.0, 0.0)


def test_flood_provider_handles_timeout_and_upstream_failures():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(OpenMeteoFloodError, match="timed out"):
        _provider(timeout).get_evidence(10.0, 10.0)
    with pytest.raises(OpenMeteoFloodError, match="HTTP 429"):
        _provider(lambda _request: httpx.Response(429)).get_evidence(10.0, 10.0)


def test_flood_service_caches_coordinate_query():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_flood_payload())

    service = FloodEvidenceService(_provider(handler))
    service.get_for_location(18.5204, 73.8567)
    service.get_for_location(18.5204, 73.8567)

    assert len(requests) == 1


def test_flood_service_force_refresh_bypasses_coordinate_cache():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_flood_payload())

    service = FloodEvidenceService(_provider(handler))
    service.get_for_location(18.5204, 73.8567)
    service.get_for_location(18.5204, 73.8567, force_refresh=True)

    assert len(requests) == 2


def test_flood_evidence_route_returns_modelled_data_and_provenance(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    with TestClient(app) as client:
        app.state.flood_evidence_service = FloodEvidenceService(
            _provider(lambda _request: httpx.Response(200, json=_flood_payload()))
        )
        response = client.get(
            "/api/v1/flood/evidence",
            params={"latitude": 18.5204, "longitude": 73.8567},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DEGRADED"
    assert body["daily"][4]["p25"]["value"] == 7.0
    assert body["daily"][0]["data_type"] == "REANALYSIS"
    assert body["daily"][3]["data_type"] == "UNAVAILABLE"
    assert body["daily"][3]["discharge"]["value"] is None
    assert body["daily"][4]["data_type"] == "FORECAST"
    assert body["provenance"]["dataset"] == "Global Flood Awareness System (GloFAS)"
    assert "not an actual river level" in " ".join(body["provenance"]["limitations"])


def test_flood_route_is_unavailable_in_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "demo")
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/flood/evidence",
            params={"latitude": 18.5204, "longitude": 73.8567},
        )

    assert response.status_code == 503
    assert "real data mode" in response.json()["detail"]