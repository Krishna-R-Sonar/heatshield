import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-engine"))
sys.path.insert(0, str(ROOT / "ai"))
sys.path.insert(0, str(ROOT / "backend"))

import pytest
from fastapi.testclient import TestClient

from heatshield_ai.agent import explain_risk, fallback_explanation
from app.config import settings
from app.main import app
from app.models.flood_evidence import FloodEvidence, FloodLocationEvidence, FloodProvenance
from app.models.weather_evidence import (
    WeatherEvidence,
    WeatherLocationEvidence,
    WeatherMeasurement,
    WeatherProvenance,
    WeatherTimeSeries,
)


@pytest.fixture(autouse=True)
def demo_data_mode(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "demo")


def test_health():
    with TestClient(app) as client:
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_locations_catalog_is_not_exposed_in_real_mode(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    with TestClient(app) as client:
        response = client.get("/api/v1/locations")

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "The demo location catalog is unavailable in real data mode; "
            "use live weather search."
        )
    }


def test_locations_catalog_remains_available_in_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "demo")
    with TestClient(app) as client:
        response = client.get("/api/v1/locations")

    assert response.status_code == 200
    assert response.json()["dataProvenance"] == "DEMO"
    assert response.json()["locations"]


def test_risk_sikkim():
    with TestClient(app) as client:
        r = client.get("/api/v1/risk/sikkim")
        assert r.status_code == 200
        body = r.json()
        assert body["location"]["id"] == "sikkim"
        assert "riskScore" in body
        assert body["riskLevel"] in ("low", "moderate", "high", "critical")
        assert body["dataProvenance"] == "DEMO"
        assert any(source["name"] == "HeatShield Demo Dataset" for source in body["dataSources"])


def test_simulation_stop_start():
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/simulation/start",
            json={"locationId": "sikkim", "preset": "river_rising"},
        )
        assert r.status_code == 200
        assert r.json()["assessment"]["simulationActive"] is True
        client.post("/api/v1/simulation/stop")


def test_provider_observations_have_provenance_and_measurement_metadata():
    with TestClient(app) as client:
        response = client.get("/api/v1/risk/sikkim")
        assert response.status_code == 200
        assessment = response.json()
        assert assessment["dataProvenance"] == "DEMO"
        assert assessment["riskDimensions"]["glacialLakeMonitoring"]["status"] == "watch"
        for provider in assessment["providerObservations"].values():
            assert provider["source"] == "HeatShield Demo Dataset"
            assert provider["timestamp"]
            assert provider["location"]["id"] == "sikkim"
            assert provider["dataQuality"].startswith("Synthetic")
            assert provider["status"] in ("available", "partial", "unavailable")
            assert all({"value", "unit", "status"} <= value.keys() for value in provider["values"].values())


def test_missing_exposure_is_not_scored_as_observed():
    with TestClient(app) as client:
        app.state.store.get_location("sikkim")["populationExposureIndex"] = None
        response = client.get("/api/v1/risk/sikkim")
        assert response.status_code == 200
        assessment = response.json()
        assert assessment["componentScores"]["exposure_score"] == 0
        exposure = assessment["providerObservations"]["exposure"]
        assert exposure["values"]["populationExposureIndex"]["status"] == "unavailable"
        assert "unavailable" in assessment["uncertainty"]
        assert any(
            observation["kind"] == "unavailable"
            for observation in assessment["observations"]
            if observation["label"] == "Population exposure index"
        )


def test_simulation_updates_risk_evidence_and_alerts():
    with TestClient(app) as client:
        normal = client.post(
            "/api/v1/simulation/start",
            json={"locationId": "sikkim", "preset": "normal"},
        ).json()["assessment"]
        heavy_rain = client.post(
            "/api/v1/simulation/start",
            json={"locationId": "sikkim", "preset": "heavy_rain"},
        ).json()["assessment"]
        river_rise = client.post(
            "/api/v1/simulation/start",
            json={"locationId": "sikkim", "preset": "river_rising"},
        ).json()["assessment"]

        assert normal["riskScore"] < heavy_rain["riskScore"]
        assert river_rise["componentScores"]["river_score"] > heavy_rain["componentScores"]["river_score"]
        assert river_rise["dataProvenance"] == "DEMO_SIMULATION"
        assert river_rise["providerObservations"]["river"]["values"]["riverLevelM"]["status"] == "simulated"
        river_change = next(
            change for change in river_rise["simulationComparison"]["changes"]
            if change["label"] == "River level"
        )
        assert river_change["delta"] > 0
        alerts = client.get("/api/v1/alerts").json()["alerts"]
        assert any(alert["locationId"] == "sikkim" and alert["simulationActive"] for alert in alerts)


def test_simulation_and_ai_inputs_are_validated():
    with TestClient(app) as client:
        bad_preset = client.post(
            "/api/v1/simulation/start",
            json={"locationId": "sikkim", "preset": "invented"},
        )
        empty_question = client.post(
            "/api/v1/ai/chat",
            json={"locationId": "sikkim", "message": " "},
        )
        oversized_question = client.post(
            "/api/v1/ai/explain",
            json={"locationId": "sikkim", "question": "x" * 2001},
        )
        assert bad_preset.status_code == 422
        assert empty_question.status_code == 422
        assert oversized_question.status_code == 422


def test_heat_simulation_changes_risk_level():
    with TestClient(app) as client:
        normal = client.post(
            "/api/v1/simulation/heat",
            json={"locationId": "delhi", "preset": "normal"},
        ).json()["assessment"]
        extreme = client.post(
            "/api/v1/simulation/heat",
            json={"locationId": "delhi", "preset": "extreme_heat"},
        ).json()["assessment"]
        assert extreme["riskScore"] > normal["riskScore"]
        assert extreme["riskLevel"] != normal["riskLevel"]
        assert extreme["providerObservations"]["weather"]["values"]["temperatureC"]["status"] == "simulated"


def test_custom_flood_parameters_update_independently():
    with TestClient(app) as client:
        rainfall_update = client.post(
            "/api/v1/simulation/parameters",
            json={
                "locationId": "sikkim",
                "rainfallIntensityMmHr": 80,
                "cumulativeRainfallMm24h": 320,
            },
        )
        rainfall = rainfall_update.json()["assessment"]
        river_update = client.post(
            "/api/v1/simulation/parameters",
            json={"locationId": "sikkim", "riverLevelM": 8},
        )
        river = river_update.json()["assessment"]

        assert rainfall_update.status_code == 200
        assert rainfall["componentScores"]["rainfall_score"] > 0
        assert river["componentScores"]["rainfall_score"] == rainfall["componentScores"]["rainfall_score"]
        assert river["componentScores"]["river_score"] > rainfall["componentScores"]["river_score"]
        assert river["riskScore"] > rainfall["riskScore"]
        assert river["dataProvenance"] == "DEMO_SIMULATION"
        assert river["providerObservations"]["river"]["values"]["riverLevelM"]["value"] == 8

        invalid = client.post(
            "/api/v1/simulation/parameters",
            json={"locationId": "sikkim", "riverLevelM": 51},
        )
        empty = client.post("/api/v1/simulation/parameters", json={"locationId": "sikkim"})
        assert invalid.status_code == 422
        assert empty.status_code == 422


def test_ai_fallback_uses_engine_scores_and_reports_missing_data():
    with TestClient(app) as client:
        app.state.store.get_location("sikkim")["populationExposureIndex"] = None
        assessment = client.get("/api/v1/risk/sikkim").json()
        explanation = fallback_explanation(assessment, "What information is missing?")
        calculated = explanation["sections"]["calculated"]
        observed = explanation["sections"]["observed"]
        assert any(line.startswith("Rainfall: +") for line in calculated)
        assert any("Data unavailable" in line and "Population exposure index" in line for line in observed)
        assert all("Population exposure index:" not in line for line in observed)
        assert any(
            "monitoring status alone does not establish an imminent glof" in line.lower()
            for line in explanation["sections"]["interpreted"]
        )
        hypothetical = fallback_explanation(assessment, "What happens if rainfall increases?")
        assert any("has not been scored" in line for line in hypothetical["sections"]["interpreted"])
        affected = fallback_explanation(assessment, "What areas are affected?")
        assert any("boundaries are unavailable" in line for line in affected["sections"]["interpreted"])


def test_model_answer_prose_is_never_returned(monkeypatch):
    async def fabricated_model_output(**_kwargs):
        return '{"intent":"factors","summary":"Rainfall is 999 mm and evacuate immediately."}'

    monkeypatch.setattr("heatshield_ai.agent.chat_completion", fabricated_model_output)
    with TestClient(app) as client:
        assessment = client.get("/api/v1/risk/sikkim").json()
        result = asyncio.run(
            explain_risk(
                assessment,
                question="Which factors contributed most?",
                api_key="test-key",
            )
        )

    serialized = str(result)
    assert "999" not in serialized
    assert "evacuate immediately" not in serialized
    assert result["source"] == "openrouter_intent_structured_response"
    assert any(line.startswith("Rainfall: +") for line in result["sections"]["calculated"])
    assert any(line.startswith("Population exposure index:") for line in result["sections"]["calculated"])
    assert all("Population exposure index:" not in line for line in result["sections"]["observed"])


def test_real_mode_risk_and_simulation_score_endpoints_are_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    score_requests = [
        ("get", "/api/v1/risk", None),
        ("get", "/api/v1/risk/sikkim", None),
        (
            "post",
            "/api/v1/simulation/start",
            {"locationId": "sikkim", "preset": "river_rising"},
        ),
        (
            "post",
            "/api/v1/simulation/heat",
            {"locationId": "delhi", "preset": "extreme_heat"},
        ),
        (
            "post",
            "/api/v1/simulation/parameters",
            {"locationId": "sikkim", "rainfallIntensityMmHr": 80},
        ),
        ("get", "/api/v1/alerts", None),
        ("post", "/api/v1/ai/explain", {"locationId": "sikkim"}),
        ("post", "/api/v1/ai/chat", {"locationId": "sikkim", "message": "Explain the score"}),
    ]
    expected_detail = (
        "Risk assessment unavailable in real data mode; real environmental evidence "
        "has not been validated for deterministic scoring."
    )

    with TestClient(app) as client:
        for method, path, body in score_requests:
            response = (
                getattr(client, method)(path, json=body)
                if body is not None
                else getattr(client, method)(path)
            )
            assert response.status_code == 503, path
            assert response.json() == {"detail": expected_detail}, path
            assert "riskScore" not in response.json(), path

        assert app.state.store.simulation is None
        simulation_status = client.get("/api/v1/simulation")
        stop_simulation = client.post("/api/v1/simulation/stop")
        assert simulation_status.status_code == 200
        assert stop_simulation.status_code == 200
        assert "riskScore" not in simulation_status.json()
        assert "riskScore" not in stop_simulation.json()


def test_real_mode_heat_route_returns_incomplete_forecast_without_demo_scoring(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    now = datetime.now(timezone.utc)
    retrieved_at = now.isoformat()
    forecast_time = (now + timedelta(minutes=30)).isoformat()
    requested_coordinates = {"latitude": 28.704, "longitude": 77.102}

    def make_measurement(value, unit):
        return WeatherMeasurement(
            value=value,
            unit=unit,
            status="AVAILABLE",
            data_type="FORECAST",
            forecast_time=forecast_time,
            provenance={
                "source": "Open-Meteo",
                "data_type": "FORECAST",
                "forecast_time": forecast_time,
                "retrieval_time": retrieved_at,
                "requested_latitude": requested_coordinates["latitude"],
                "requested_longitude": requested_coordinates["longitude"],
            },
        )

    weather = WeatherEvidence(
        status="AVAILABLE",
        location=WeatherLocationEvidence(
            name="Delhi",
            state="Delhi",
            requested_latitude=requested_coordinates["latitude"],
            requested_longitude=requested_coordinates["longitude"],
            grid_latitude=28.75,
            grid_longitude=77.0,
            timezone="Asia/Kolkata",
        ),
        forecast={
            "temperature": make_measurement(38.0, "°C"),
            "humidity": make_measurement(55.0, "%"),
        },
        hourly=WeatherTimeSeries(times=[], values={}, units={}),
        daily=WeatherTimeSeries(times=[], values={}, units={}),
        provenance=WeatherProvenance(
            retrieved_at=retrieved_at,
            quality="Mock Open-Meteo forecast evidence.",
            limitations=["Forecast values are model output, not ground observations."],
        ),
    )

    def return_weather(location):
        assert location["id"] == "delhi"
        return weather

    def reject_demo_assessment(location_id):
        assert location_id == "delhi"
        pytest.fail("Real heat assessment must not use demo RiskService")

    with TestClient(app) as client:
        monkeypatch.setattr(
            app.state.weather_evidence_service,
            "get_for_location",
            return_weather,
        )
        monkeypatch.setattr(
            app.state.risk_service,
            "assess",
            reject_demo_assessment,
        )
        response = client.get("/api/v1/risk/delhi")

    assert response.status_code == 200
    result = response.json()
    assert result["assessmentType"] == "FORECAST_HEAT"
    assert result["status"] == "INCOMPLETE"
    assert result["scoreStatus"] == "NOT_CALCULATED"
    assert result["riskScore"] is None
    assert result["riskLevel"] is None
    assert result["dataProvenance"] == "FORECAST"
    assert result["assessmentWindow"]["forecastTime"] == forecast_time
    assert result["provenance"]["source"] == "Open-Meteo"
    assert result["provenance"]["locationAssociation"] == "REQUESTED_COORDINATES_MATCH"
    assert "population_exposure_index" in result["missingInputs"]


def test_real_mode_heat_provider_failure_returns_unavailable_without_score(monkeypatch):
    from app.providers.open_meteo import OpenMeteoError

    monkeypatch.setattr(settings, "data_mode", "real")

    def raise_provider_error(location):
        assert location["id"] == "delhi"
        raise OpenMeteoError("Open-Meteo request timed out.")

    with TestClient(app) as client:
        monkeypatch.setattr(
            app.state.weather_evidence_service,
            "get_for_location",
            raise_provider_error,
        )
        response = client.get("/api/v1/risk/delhi")

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "UNAVAILABLE"
    assert result["riskScore"] is None
    assert result["riskLevel"] is None
    assert any(
        reason["code"] == "WEATHER_PROVIDER_UNAVAILABLE"
        for reason in result["reasons"]
    )


def test_real_mode_selected_location_heat_assessment_is_score_free(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    received = {}

    class HeatAssessmentService:
        def assess(self, location):
            received.update(location)
            return {
                "assessmentType": "FORECAST_HEAT",
                "status": "INCOMPLETE",
                "scoreStatus": "NOT_CALCULATED",
                "riskScore": None,
                "riskLevel": None,
                "missingInputs": ["heat_index_c", "population_exposure_index"],
                "reasons": [],
            }

    with TestClient(app) as client:
        app.state.real_heat_assessment_service = HeatAssessmentService()
        response = client.get(
            "/api/v1/risk/heat-forecast",
            params={
                "latitude": -36.85,
                "longitude": 174.76,
                "name": "Auckland",
                "admin1": "Auckland Region",
                "country": "New Zealand",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "INCOMPLETE"
    assert response.json()["riskScore"] is None
    assert received["name"] == "Auckland"
    assert received["lat"] == -36.85
    assert received["lng"] == 174.76


def test_real_mode_weather_and_flood_evidence_endpoints_remain_available(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    retrieved_at = "2026-10-09T00:00:00+00:00"
    weather = WeatherEvidence(
        status="AVAILABLE",
        location=WeatherLocationEvidence(
            name="Test location",
            requested_latitude=1.0,
            requested_longitude=2.0,
        ),
        forecast={},
        hourly=WeatherTimeSeries(times=[], values={}, units={}),
        daily=WeatherTimeSeries(times=[], values={}, units={}),
        provenance=WeatherProvenance(
            retrieved_at=retrieved_at,
            quality="Mocked forecast evidence.",
            limitations=[],
        ),
    )
    flood = FloodEvidence(
        status="AVAILABLE",
        location=FloodLocationEvidence(
            requested_latitude=1.0,
            requested_longitude=2.0,
        ),
        daily=[],
        provenance=FloodProvenance(
            retrieved_at=retrieved_at,
            requested_latitude=1.0,
            requested_longitude=2.0,
            quality="Mocked modeled discharge evidence.",
            limitations=[],
        ),
    )

    refresh_calls = []

    def get_weather(_location, *, force_refresh=False):
        refresh_calls.append(("weather", force_refresh))
        return weather

    def get_flood(_latitude, _longitude, *, force_refresh=False):
        refresh_calls.append(("flood", force_refresh))
        return flood

    with TestClient(app) as client:
        app.state.weather_evidence_service.get_for_location = get_weather
        app.state.historical_baseline_service = None
        app.state.flood_evidence_service.get_for_location = get_flood

        weather_response = client.get(
            "/api/v1/weather/evidence",
            params={"latitude": 1, "longitude": 2, "name": "Test location", "refresh": "true"},
        )
        flood_response = client.get(
            "/api/v1/flood/evidence",
            params={"latitude": 1, "longitude": 2, "refresh": "true"},
        )

    assert weather_response.status_code == 200
    assert weather_response.json()["status"] == "AVAILABLE"
    assert flood_response.status_code == 200
    assert flood_response.json()["status"] == "AVAILABLE"
    assert refresh_calls == [("weather", True), ("flood", True)]
