from __future__ import annotations

import asyncio
import sys
import time
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-engine"))
sys.path.insert(0, str(ROOT / "ai"))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings
from app.main import app
from app.models.ensemble_evidence import EnsembleForecastEvidence, EnsembleProvenance
from app.models.flood_evidence import FloodEvidence, FloodLocationEvidence, FloodProvenance
from app.models.weather_evidence import (
    HistoricalAnomalyEvidence,
    WeatherEvidence,
    WeatherLocationEvidence,
    WeatherProvenance,
    WeatherTimeSeries,
)

from heatshield_ai.global_evidence import (
    build_global_evidence_tools,
    render_global_evidence_answer,
)
from heatshield_ai.openrouter_provider import chat_completion
from heatshield_ai.strands_agent import run_global_evidence_workflow
from heatshield_ai.strands_agent import run_openrouter_global_evidence
from app.services.weather_evidence_service import WeatherEvidenceService


LOCATION = {
    "name": "Auckland",
    "state": "Auckland Region",
    "country": "New Zealand",
    "lat": -36.85,
    "lng": 174.76,
}

MAP_POINT_LOCATION = {
    **LOCATION,
    "name": "Map point 28.1177, 85.4736",
    "state": "Selected map point",
    "selection_type": "map_point",
}


def _weather() -> WeatherEvidence:
    return WeatherEvidence(
        status="DEGRADED",
        location=WeatherLocationEvidence(
            name="Auckland",
            state="Auckland Region",
            country="New Zealand",
            requested_latitude=-36.85,
            requested_longitude=174.76,
            grid_latitude=-36.875,
            grid_longitude=174.75,
            elevation_m=14,
            timezone="Pacific/Auckland",
        ),
        forecast={
            "temperature": {
                "value": 29.0,
                "unit": "°C",
                "status": "AVAILABLE",
                "data_type": "FORECAST",
                "forecast_time": "2026-10-09T14:00:00+13:00",
                "provenance": {"source": "Open-Meteo"},
            },
            "precipitation": {
                "value": None,
                "unit": "mm",
                "status": "UNAVAILABLE",
                "data_type": "FORECAST",
                "forecast_time": None,
                "provenance": {"source": "Open-Meteo"},
            },
        },
        hourly=WeatherTimeSeries(times=[], values={}, units={}),
        daily=WeatherTimeSeries(times=[], values={}, units={}),
        provenance=WeatherProvenance(
            retrieved_at="2026-10-08T00:00:00+00:00",
            timezone="Pacific/Auckland",
            quality="Forecast quality incomplete.",
            limitations=["Model ID unavailable."],
        ),
    )


def _flood_unavailable() -> FloodEvidence:
    return FloodEvidence(
        status="UNAVAILABLE",
        location=FloodLocationEvidence(
            requested_latitude=-36.85,
            requested_longitude=174.76,
            river_association="UNVERIFIED",
        ),
        daily=[],
        provenance=FloodProvenance(
            retrieved_at="2026-10-08T00:00:00+00:00",
            requested_latitude=-36.85,
            requested_longitude=174.76,
            quality="No discharge returned.",
            limitations=["No local river evidence."],
        ),
    )


def _ensemble_unavailable() -> EnsembleForecastEvidence:
    return EnsembleForecastEvidence(
        status="UNAVAILABLE",
        member_count=0,
        daily=[],
        provenance=EnsembleProvenance(
            retrieved_at="2026-10-08T00:00:00+00:00",
            requested_latitude=-36.85,
            requested_longitude=174.76,
            classification_method="No member spread available.",
            limitations=["Ensemble unavailable."],
        ),
    )


class FakeWeatherProvider:
    def get_weather_evidence_for_location(self, _location):
        return _weather()


class FakeHistoricalService:
    def calculate(self, _weather):
        return HistoricalAnomalyEvidence(
            status="UNAVAILABLE",
            anomalies=[],
            methodology="ERA5 baseline unavailable.",
            retrieved_at="2026-10-08T00:00:00+00:00",
            quality="Archive unavailable.",
            limitations=["No historical values returned."],
        )


class FakeFloodService:
    def get_for_location(self, _latitude, _longitude):
        return _flood_unavailable()


class FakeEnsembleService:
    def get_for_location(self, _latitude, _longitude):
        return _ensemble_unavailable()


def _build_tools():
    return build_global_evidence_tools(
        location=LOCATION,
        weather_provider=FakeWeatherProvider(),
        flood_service=FakeFloodService(),
        historical_service=FakeHistoricalService(),
        ensemble_service=FakeEnsembleService(),
    )


def test_global_evidence_tools_are_read_only_and_report_unavailable_sources():
    tools, results = _build_tools()
    by_name = {item.tool_name: item for item in tools}

    weather = by_name["get_weather_evidence"]()
    flood = by_name["get_flood_evidence"]()
    historical = by_name["get_historical_baseline"]()
    uncertainty = by_name["get_forecast_uncertainty"]()
    provenance = by_name["get_data_provenance"]()
    projection = by_name["get_climate_projection"]()
    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results=results,
        question="Why is water evidence unavailable?",
    )

    assert weather["forecast"]["temperature"]["value"] == 29.0
    assert flood["status"] == "UNAVAILABLE"
    assert historical["status"] == "UNAVAILABLE"
    assert uncertainty["status"] == "UNAVAILABLE"
    assert provenance["weather"]["data_type"] == "FORECAST"
    assert projection["status"] == "UNAVAILABLE"
    assert "Unavailable in this result" in " ".join(answer["sections"]["interpreted"])
    assert any("FORECAST" in item for item in answer["sections"]["evidence"])
    assert "calculated" not in answer["sections"]
    assert answer["sections"]["recommended"]
    assert answer["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }
    assert "Not available in this result" in answer["ai_response"]
    assert "risk score" in answer["summary"]
    assert len(tools) == 6
    assert "verified local observations" in " ".join(answer["sections"]["recommended"])


def test_openrouter_global_evidence_uses_supplied_snapshot_without_starting_strands(monkeypatch):
    tools, tool_results = _build_tools()
    for item in tools:
        item()
    response = {
        "intent": "data",
        "location_summary": "The forecast shows 29 °C at 2026-10-09T14:00:00+13:00; local exposure information is unavailable.",
        "location_summary_evidence_refs": ["weather_forecast_temperature"],
        "key_findings": [{
            "finding": "The forecast temperature is 29 °C at 2026-10-09T14:00:00+13:00.",
            "evidence_refs": ["weather_forecast_temperature"],
        }],
        "environmental_concerns": [],
        "evidence_based_suggestions": [],
        "general_preventive_ideas": [],
        "community_opportunities": [],
        "missing_information": ["Local exposure information is not supplied."],
        "recommended_next_steps": [],
        "limitations": [],
    }
    calls = []

    async def fake_chat_completion(**kwargs):
        calls.append(kwargs)
        return json.dumps(response)

    monkeypatch.setattr("heatshield_ai.strands_agent.chat_completion", fake_chat_completion)
    intent, guidance = asyncio.run(
        run_openrouter_global_evidence(
            question="What does the temperature forecast show?",
            location=LOCATION,
            tool_results=tool_results,
            api_key="test-key",
            model_ids=["primary-model", "fallback-model"],
            base_url="https://openrouter.ai/api/v1",
        )
    )

    assert intent == "data"
    assert guidance["interpretation"] == response["location_summary"]
    assert len(calls) == 1
    assert calls[0]["model"] == "primary-model"
    assert calls[0]["timeout_seconds"] == 8.0
    assert calls[0]["max_tokens"] == 1400
    assert calls[0]["include_reasoning"] is False
    assert calls[0]["response_format"] is None
    assert len(calls[0]["system"]) < 4_000
    assert len(calls[0]["user"]) < 8_000
    assert '"question": "What does the temperature forecast show?"' in calls[0]["user"]
    assert '"value": 29.0' in calls[0]["user"]
    assert '"weather_forecast_temperature"' in calls[0]["user"]


def test_openrouter_global_evidence_uses_next_model_when_primary_fails(monkeypatch):
    tools, tool_results = _build_tools()
    for item in tools:
        item()

    response = {
        "intent": "data",
        "location_summary": "The forecast shows 29°C at 2026-10-09T14:00:00+13:00.",
        "location_summary_evidence_refs": ["weather_forecast_temperature"],
        "key_findings": [{
            "finding": "The forecast temperature is 29°C at 2026-10-09T14:00:00+13:00.",
            "evidence_refs": ["weather_forecast_temperature"],
        }],
        "environmental_concerns": [],
        "evidence_based_suggestions": [],
        "general_preventive_ideas": [],
        "community_opportunities": [],
        "missing_information": [],
        "recommended_next_steps": [],
        "limitations": [],
    }
    calls = []

    async def fail_then_succeed(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "primary-model":
            raise TimeoutError("primary timed out")
        return json.dumps(response)

    monkeypatch.setattr("heatshield_ai.strands_agent.chat_completion", fail_then_succeed)
    intent, guidance = asyncio.run(
        run_openrouter_global_evidence(
            question="What does the temperature forecast show?",
            location=LOCATION,
            tool_results=tool_results,
            api_key="test-key",
            model_ids=["primary-model", "fallback-model", "unused-model"],
            base_url="https://openrouter.ai/api/v1",
        )
    )

    assert intent == "data"
    assert guidance["interpretation"] == response["location_summary"]
    assert [call["model"] for call in calls] == ["primary-model", "fallback-model"]


def test_openrouter_completion_rejects_reasoning_only_response(monkeypatch):
    class EmptyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": None, "reasoning": "internal reasoning"}}]}

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return EmptyResponse()

    monkeypatch.setattr("heatshield_ai.openrouter_provider.httpx.AsyncClient", FakeAsyncClient)
    with pytest.raises(ValueError, match="did not include text content"):
        asyncio.run(
            chat_completion(
                api_key="test-key",
                model="test-model",
                base_url="https://openrouter.ai/api/v1",
                system="Return JSON.",
                user="Summarize this evidence.",
            )
        )


def test_openrouter_daily_free_model_limit_skips_remaining_attempts(monkeypatch):
    tools, tool_results = _build_tools()
    for item in tools:
        item()

    class RateLimitResponse:
        status_code = 429
        text = '{"error":{"message":"Rate limit exceeded: free-models-per-day"}}'

    class RateLimitError(Exception):
        response = RateLimitResponse()

    calls = []

    async def rate_limited_completion(**kwargs):
        calls.append(kwargs)
        raise RateLimitError()

    monkeypatch.setattr("heatshield_ai.strands_agent.chat_completion", rate_limited_completion)
    with pytest.raises(RuntimeError, match="All 1 attempted OpenRouter models failed"):
        asyncio.run(
            run_openrouter_global_evidence(
                question="What does the temperature forecast show?",
                location=LOCATION,
                tool_results=tool_results,
                api_key="test-key",
                model_ids=["primary-model", "fallback-model"],
                base_url="https://openrouter.ai/api/v1",
            )
        )

    assert len(calls) == 1
    assert calls[0]["model"] == "primary-model"


def test_daily_rain_and_unverified_discharge_do_not_trigger_flood_recommendations():
    weather = _weather().model_dump(mode="json")
    weather["daily"] = {
        "times": ["2026-10-09"],
        "values": {"precipitation_sum": [18.0], "rain_sum": [12.0]},
        "units": {"precipitation_sum": "mm", "rain_sum": "mm"},
    }
    flood = _flood_unavailable().model_dump(mode="json")
    flood["status"] = "AVAILABLE"
    flood["location"]["river_association"] = "UNVERIFIED"
    flood["daily"] = [{
        "time": "2026-10-09",
        "data_type": "FORECAST",
        "discharge": {
            "value": 42.0,
            "unit": "m³/s",
            "status": "AVAILABLE",
            "data_type": "FORECAST",
            "reference_time": "2026-10-09T00:00:00Z",
            "provenance": {},
        },
    }]

    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_flood_evidence": flood,
        },
        question="How should we prepare?",
    )

    assert answer["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }
    assert "Daily total precipitation forecast: 18.0 mm" in " ".join(answer["sections"]["evidence"])
    assert "Daily rain-only forecast: 12.0 mm" in " ".join(answer["sections"]["evidence"])
    assert "12 mm on Oct 9, 2026" in answer["ai_response"]
    assert "42 m³/s" in " ".join(answer["sections"]["interpreted"])
    assert "unverified" in " ".join(answer["sections"]["interpreted"]).lower()


def test_evidence_completeness_labels_are_separate_from_hazard_severity_and_report_unavailable_topics():
    weather = _weather().model_dump(mode="json")
    weather["daily"] = {
        "times": ["2026-10-09"],
        "values": {"precipitation_sum": [18.0], "rain_sum": [12.0]},
        "units": {"precipitation_sum": "mm", "rain_sum": "mm"},
    }

    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
        },
        question="What evidence is missing for drought?",
    )

    completeness = answer["environmental_assessment"]["evidence_completeness"]
    assert completeness["heat"]["status"] == "partial_evidence"
    assert completeness["rainfall"]["status"] == "partial_evidence"
    assert completeness["river_flood"]["status"] == "insufficient_evidence"
    assert completeness["glof"]["status"] == "insufficient_evidence"
    assert completeness["waterlogging"]["status"] == "insufficient_evidence"
    assert completeness["drought"]["status"] == "insufficient_evidence"
    assert "Long-term rainfall-deficit assessment" in " ".join(
        answer["environmental_assessment"]["missing_information"]
    )
    assert "not hazard severity or safety" in " ".join(
        answer["environmental_assessment"]["limitations"]
    )


def test_ordinary_temperature_changes_are_reported_without_precautionary_recommendations():
    responses = []
    for value in (12.4, 24.8):
        weather = _weather().model_dump(mode="json")
        weather["forecast"]["temperature"]["value"] = value
        answer = render_global_evidence_answer(
            location=LOCATION,
            tool_results={
                "get_weather_evidence": weather,
                "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
            },
            question="What conditions should I consider?",
        )
        responses.append(answer)

    assert responses[0]["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }
    assert responses[1]["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }
    assert "12.4 °C" in responses[0]["ai_response"]
    assert "24.8 °C" in responses[1]["ai_response"]
    assert not responses[0]["sections"]["recommended"]


def test_historical_temperature_rank_does_not_trigger_heat_health_advice():
    for temperature in (7.7, 31.0):
        weather = _weather().model_dump(mode="json")
        weather["daily"] = {
            "times": ["2026-10-09"],
            "values": {"temperature_2m_max": [temperature]},
            "units": {"temperature_2m_max": "°C"},
        }
        weather["historical_anomalies"] = {
            "status": "AVAILABLE",
            "baseline_period": "1991-2020",
            "anomalies": [{
                "date": "2026-10-09",
                "indicator": "temperature_max",
                "current_value": temperature,
                "unit": "°C",
                "anomaly": 2.0,
                "percentile": 95.0,
                "historical_baseline": {"mean": temperature - 2.0},
                "status": "AVAILABLE",
            }],
        }
        answer = render_global_evidence_answer(
            location=LOCATION,
            tool_results={
                "get_weather_evidence": weather,
                "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
            },
            question="What should I consider?",
        )
        assert answer["sections"]["recommendations"] == {
            "residents_and_workers": [],
            "municipal_planners": [],
        }


def test_ai_guidance_is_location_specific_and_attached_only_to_supported_evidence():
    weather = _weather().model_dump(mode="json")
    weather["forecast"]["temperature"]["value"] = 7.7
    weather["forecast"]["apparent_temperature"] = {
        "value": 6.3,
        "unit": "°C",
        "status": "AVAILABLE",
        "data_type": "FORECAST",
        "forecast_time": "2026-10-09T19:00:00+13:00",
        "provenance": {"source": "Open-Meteo"},
    }
    weather["forecast"]["humidity"] = {
        "value": 81,
        "unit": "%",
        "status": "AVAILABLE",
        "data_type": "FORECAST",
        "forecast_time": "2026-10-09T19:00:00+13:00",
        "provenance": {"source": "Open-Meteo"},
    }
    weather["forecast"]["wet_bulb_temperature"] = {
        "value": 5.9,
        "unit": "°C",
        "status": "AVAILABLE",
        "data_type": "FORECAST",
        "forecast_time": "2026-10-09T19:00:00+13:00",
        "provenance": {"source": "Open-Meteo"},
    }
    weather["daily"] = {
        "times": ["2026-10-09"],
        "values": {"precipitation_sum": [0.4], "rain_sum": [0.3]},
        "units": {"precipitation_sum": "mm", "rain_sum": "mm"},
    }
    guidance = {
        "interpretation": (
            "This forecast is cool rather than hot; high humidity by itself does not establish "
            "heat stress. The river-model data cannot determine local flood likelihood."
        ),
        "recommendations": {
            "residents_and_workers": [
                {
                    "topic": "heat",
                    "recommendation": (
                        "Heat-specific precautions may not be the immediate concern in this cool "
                        "forecast; check local conditions when planning outdoor work."
                    ),
                },
                {
                    "topic": "flood",
                    "recommendation": "Check verified local gauges because modeled discharge is not a local river level.",
                },
            ],
            "municipal_planners": [
                {
                    "topic": "heat",
                    "recommendation": (
                        "Do not prioritize heat response from this forecast alone; compare it with "
                        "local observations and population-exposure data."
                    ),
                },
            ],
        },
    }

    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
        },
        question="What should people consider?",
        intent="preparedness",
        ai_guidance=guidance,
    )

    assert answer["source"] == "openrouter_ai_evidence_guidance"
    assert answer["ai_response"] == guidance["interpretation"]
    assert "cool rather than hot" in answer["ai_response"]
    assert answer["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }


def test_map_point_response_shows_weather_without_unverified_audience_guidance():
    weather = _weather().model_dump(mode="json")
    weather["forecast"]["temperature"]["value"] = -11.1
    weather["daily"] = {
        "times": ["2026-10-10"],
        "values": {"precipitation_sum": [2.2]},
        "units": {"precipitation_sum": "mm"},
    }
    unsafe_guidance = {
        "interpretation": "Residents should prepare for cold conditions in nearby settlements.",
        "recommendations": {
            "residents_and_workers": [{
                "topic": "heat",
                "recommendation": "Consider shade and cooling breaks.",
            }],
            "municipal_planners": [{
                "topic": "heat",
                "recommendation": "Prioritize neighborhood cooling.",
            }],
        },
    }

    answer = render_global_evidence_answer(
        location=MAP_POINT_LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
        },
        question="Summarize this point's weather.",
        intent="data",
        ai_guidance=unsafe_guidance,
    )

    assert any("-11.1 °C (FORECAST" in item for item in answer["sections"]["evidence"])
    assert any("2.2 mm" in item for item in answer["sections"]["evidence"])
    assert answer["ai_response"] is not None
    assert "residents" not in answer["ai_response"].lower()
    assert answer["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }
    assert answer["sections"]["recommended"] == [
        "Weather evidence is for the selected coordinates and provider grid cells. No nearby settlement or population-exposure context is verified for this pin."
    ]
    assert any("coordinate-only map selection" in item for item in answer["sections"]["interpreted"])


def test_strands_workflow_returns_validated_ai_interpretation_without_a_second_model_call(monkeypatch):
    tools, tool_results = _build_tools()
    calls = []
    expected = {
        "intent": "preparedness",
        "interpretation": "Cool forecast conditions do not indicate heat stress by themselves.",
        "recommendations": {
            "residents_and_workers": [{
                "topic": "heat",
                "recommendation": "Check local conditions before planning outdoor work.",
            }],
            "municipal_planners": [],
        },
    }

    class FakeModel:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    class FakeAgent:
        def __init__(self, *, model, tools, system_prompt, callback_handler):
            self.tools = tools
            assert system_prompt
            assert callback_handler is None

        def __call__(self, _prompt, **kwargs):
            assert kwargs["limits"] == {"turns": 1}
            next(item for item in self.tools if item.tool_name == "get_weather_evidence")()
            return SimpleNamespace(
                message={"content": [{"text": json.dumps(expected)}]},
            )

    monkeypatch.setattr("strands.Agent", FakeAgent)
    monkeypatch.setattr("strands.models.OpenAIModel", FakeModel)
    intent, guidance = run_global_evidence_workflow(
        question="What should outdoor workers consider?",
        location=LOCATION,
        tools=tools,
        tool_results=tool_results,
        api_key="test-key",
        model_ids=["test-model"],
        base_url="https://openrouter.ai/api/v1",
    )

    assert intent == "preparedness"
    assert guidance["interpretation"] == expected["interpretation"]
    assert guidance["recommendations"]["residents_and_workers"][0]["topic"] == "heat"
    assert len(calls) == 1
    assert calls[0]["params"]["max_tokens"] == 700


def test_strands_global_evidence_caps_model_attempts_and_agent_turns(monkeypatch):
    tools, tool_results = _build_tools()
    attempted_models = []
    invocation_limits = []

    class FakeModel:
        def __init__(self, *, model_id, **_kwargs):
            self.model_id = model_id

    class FakeAgent:
        def __init__(self, *, model, **_kwargs):
            attempted_models.append(model.model_id)

        def __call__(self, _prompt, **kwargs):
            invocation_limits.append(kwargs["limits"])
            raise RuntimeError("model failure")

    monkeypatch.setattr("strands.Agent", FakeAgent)
    monkeypatch.setattr("strands.models.OpenAIModel", FakeModel)

    with pytest.raises(RuntimeError, match="All 2 attempted OpenRouter models failed"):
        run_global_evidence_workflow(
            question="Compare the forecast with historical rainfall.",
            location=LOCATION,
            tools=tools,
            tool_results=tool_results,
            api_key="test-key",
            model_ids=["primary", "fallback-1", "fallback-2"],
            base_url="https://openrouter.ai/api/v1",
        )

    assert attempted_models == ["primary", "fallback-1"]
    assert invocation_limits == [{"turns": 1}, {"turns": 1}]


def test_strands_prompt_receives_complete_location_evidence_before_model_generation(monkeypatch):
    evidence = {
        "get_weather_evidence": {
            "status": "DEGRADED",
            "location": {
                "requested_latitude": 27.97,
                "requested_longitude": 86.92,
                "timezone": "Asia/Kathmandu",
                "grid_latitude": 28.0,
                "grid_longitude": 86.875,
            },
            "forecast": {
                "temperature": {"value": 9.4, "unit": "°C", "status": "AVAILABLE", "forecast_time": "2026-10-09T09:00:00+05:45"},
                "precipitation": {"value": 0.0, "unit": "mm", "status": "AVAILABLE", "forecast_time": "2026-10-09T09:00:00+05:45"},
                "precipitation_probability": {"value": 5, "unit": "%", "status": "AVAILABLE", "forecast_time": "2026-10-09T09:00:00+05:45"},
                "uv_index": {"value": 4.5, "unit": "", "status": "AVAILABLE", "forecast_time": "2026-10-09T09:00:00+05:45"},
            },
            "hourly": {
                "times": ["2026-10-09T09:00:00+05:45"],
                "values": {"precipitation": [0.0], "precipitation_probability": [5]},
                "units": {"precipitation": "mm", "precipitation_probability": "%"},
            },
            "daily": {
                "times": ["2026-10-09"],
                "values": {"precipitation_sum": [2.6], "rain_sum": [2.4], "uv_index_max": [7.0]},
                "units": {"precipitation_sum": "mm", "rain_sum": "mm", "uv_index_max": ""},
            },
            "historical_anomalies": {
                "status": "AVAILABLE",
                "baseline_period": "1991-2020",
                "anomalies": [{
                    "indicator": "temperature_max",
                    "date": "2026-10-09",
                    "status": "AVAILABLE",
                    "current_value": 16.0,
                    "anomaly": -3.74,
                    "percentile": 3.9,
                    "historical_baseline": {"mean": 19.74},
                }],
            },
            "provenance": {
                "source": "Open-Meteo",
                "retrieved_at": "2026-10-08T00:00:00Z",
                "quality": "Hourly precipitation probability is unavailable.",
                "limitations": ["Grid-cell forecast, not a local observation."],
            },
            "missing_fields": ["wet_bulb_temperature"],
        },
        "get_flood_evidence": {
            "status": "AVAILABLE",
            "location": {"river_association": "UNVERIFIED"},
            "daily": [{
                "data_type": "FORECAST",
                "time": "2026-10-09",
                "discharge": {"value": 31.2, "unit": "m³/s", "status": "AVAILABLE"},
            }],
            "provenance": {"source": "GloFAS", "retrieved_at": "2026-10-08T00:01:00Z"},
        },
        "get_historical_baseline": {
            "status": "AVAILABLE",
            "baseline_period": "1991-2020",
            "methodology": "ERA5 same-calendar-month; local calendar date.",
        },
        "get_forecast_uncertainty": {
            "status": "AVAILABLE",
            "daily": [{
                "date": "2026-10-09",
                "temperature_max": {
                    "status": "AVAILABLE", "median": 10.2, "p25": 9.8, "p75": 10.6,
                    "spread_iqr": 0.8, "unit": "°C",
                },
            }],
            "provenance": {"source": "Open-Meteo Ensemble API", "timezone": "UTC"},
        },
        "get_data_provenance": {
            "weather": {"source": "Open-Meteo", "data_type": "FORECAST"},
            "flood": {"source": "GloFAS", "data_type": "MODEL_EVIDENCE"},
            "ensemble": {"source": "Open-Meteo Ensemble API", "data_type": "ENSEMBLE_FORECAST"},
            "limitations": {"weather": "Some forecast fields unavailable."},
        },
    }
    tool_results = {}

    class EvidenceTool:
        def __init__(self, name, result):
            self.tool_name = name
            self.result = result

        def __call__(self):
            tool_results[self.tool_name] = self.result
            return self.result

    tools = [EvidenceTool(name, result) for name, result in evidence.items()]
    prompts = []
    expected = {
        "intent": "data",
        "interpretation": "The prompt receives the selected point's provider evidence.",
        "recommendations": {"residents_and_workers": [], "municipal_planners": []},
    }

    class FakeModel:
        def __init__(self, **_kwargs):
            pass

    class FakeAgent:
        def __init__(self, *, model, tools, system_prompt, callback_handler):
            prompt_rules = " ".join(system_prompt.split())
            assert "not a disaster probability" in prompt_rules
            assert "UTC date" in prompt_rules
            assert "never infer local flood likelihood" in prompt_rules
            assert "A higher discharge value does not establish higher local flood risk" in prompt_rules
            assert "Each recommendation must cite at least one exact available measurement" in prompt_rules
            assert "historical October mean" in prompt_rules
            assert "daily maximum UV index" in prompt_rules
            assert callback_handler is None

        def __call__(self, prompt, **_kwargs):
            prompts.append(json.loads(prompt))
            return SimpleNamespace(message={"content": [{"text": json.dumps(expected)}]})

    monkeypatch.setattr("strands.Agent", FakeAgent)
    monkeypatch.setattr("strands.models.OpenAIModel", FakeModel)
    run_global_evidence_workflow(
        question="Explain the evidence for this location.",
        location={**LOCATION, "lat": 27.97, "lng": 86.92},
        tools=tools,
        tool_results=tool_results,
        api_key="test-key",
        model_ids=["test-model"],
        base_url="https://openrouter.ai/api/v1",
    )

    prompt = prompts[0]
    snapshot = prompt["retrieved_evidence"]
    weather = snapshot["get_weather_evidence"]
    assert prompt["location"]["lat"] == 27.97
    assert weather["location"]["timezone"] == "Asia/Kathmandu"
    assert weather["forecast"]["temperature"]["value"] == 9.4
    assert weather["hourly"]["values"]["precipitation"] == [0.0]
    assert weather["daily"]["values"]["precipitation_sum"] == [2.6]
    assert weather["daily"]["values"]["rain_sum"] == [2.4]
    assert weather["daily"]["values"]["uv_index_max"] == [7.0]
    assert weather["forecast"]["uv_index"]["value"] == 4.5
    assert weather["historical_anomalies"]["anomalies"][0]["percentile"] == 3.9
    assert snapshot["get_flood_evidence"]["daily"][0]["discharge"]["value"] == 31.2
    assert snapshot["get_forecast_uncertainty"]["daily"][0]["temperature_max"]["spread_iqr"] == 0.8
    assert snapshot["get_data_provenance"]["limitations"]["weather"]
    assert weather["daily_period"] == {
        "timezone": "Asia/Kathmandu",
        "basis": "local calendar day",
    }
    assert weather["historical_period"] == {
        "timezone": "Asia/Kathmandu",
        "basis": "ERA5 daily values on forecast-location calendar dates",
    }
    assert snapshot["get_forecast_uncertainty"]["daily_period"] == {
        "timezone": "UTC",
        "basis": "UTC calendar date",
    }
    assert weather["missing_fields"] == ["wet_bulb_temperature"]
    assert prompt["data_quality"]["weather"]["status"] == "DEGRADED"
    assert prompt["data_quality"]["weather"]["unavailable_forecast_fields"] == []
    assert prompt["data_quality"]["weather"]["hourly_series"]["precipitation"] == "AVAILABLE"
    assert prompt["data_quality"]["weather"]["daily_series"]["rain_sum"] == "AVAILABLE"
    assert prompt["data_quality"]["flood"]["river_association"] == "UNVERIFIED"
    assert prompt["data_quality"]["ensemble"]["unavailable_statistics"] == [
        {"date": "2026-10-09", "variable": "precipitation_sum"},
    ]
    assert prompt["freshness"]["get_weather_evidence"]["retrieved_at"] == "2026-10-08T00:00:00Z"
    assert set(snapshot) == set(evidence)


def test_strands_map_point_workflow_discards_population_assumptions(monkeypatch):
    tools, tool_results = _build_tools()
    expected = {
        "intent": "data",
        "interpretation": "Residents should take precautions based on this mountain forecast.",
        "recommendations": {
            "residents_and_workers": [],
            "municipal_planners": [],
        },
    }
    prompts = []

    class FakeModel:
        def __init__(self, **_kwargs):
            pass

    class FakeAgent:
        def __init__(self, *, model, tools, system_prompt, callback_handler):
            self.tools = tools
            assert "arbitrary" in system_prompt
            assert callback_handler is None

        def __call__(self, prompt, **_kwargs):
            prompts.append(json.loads(prompt))
            next(item for item in self.tools if item.tool_name == "get_weather_evidence")()
            return SimpleNamespace(message={"content": [{"text": json.dumps(expected)}]})

    monkeypatch.setattr("strands.Agent", FakeAgent)
    monkeypatch.setattr("strands.models.OpenAIModel", FakeModel)
    intent, guidance = run_global_evidence_workflow(
        question="Summarize this point's weather.",
        location=MAP_POINT_LOCATION,
        tools=tools,
        tool_results=tool_results,
        api_key="test-key",
        model_ids=["test-model"],
        base_url="https://openrouter.ai/api/v1",
    )

    assert intent == "data"
    assert prompts[0]["location"]["selection_type"] == "map_point"
    assert guidance["interpretation"] == ""
    assert guidance["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }


def test_recommendations_do_not_use_invalid_temperature_or_zero_rainfall():
    weather = _weather().model_dump(mode="json")
    weather["forecast"]["temperature"]["unit"] = "°F"
    weather["daily"] = {
        "times": ["2026-10-09"],
        "values": {"precipitation_sum": [0.0]},
        "units": {"precipitation_sum": "mm"},
    }
    weather["historical_anomalies"] = {
        "status": "AVAILABLE",
        "baseline_period": "1991-2020",
        "anomalies": [{
            "date": "2026-10-09",
            "indicator": "precipitation",
            "current_value": 0.0,
            "unit": "mm",
            "anomaly": 0.0,
            "percentile": 99.0,
            "historical_baseline": {"mean": 0.0},
            "status": "AVAILABLE",
        }],
    }
    flood = _flood_unavailable().model_dump(mode="json")
    flood["daily"] = [{
        "time": "2026-10-09",
        "data_type": "FORECAST",
        "discharge": {
            "value": 42.0,
            "unit": "m",
            "status": "AVAILABLE",
            "data_type": "FORECAST",
            "reference_time": "2026-10-09T00:00:00Z",
            "provenance": {},
        },
    }]
    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={"get_weather_evidence": weather, "get_flood_evidence": flood},
        question="What is missing from the rainfall forecast?",
    )

    recommendations = answer["sections"]["recommendations"]
    assert recommendations["residents_and_workers"] == []
    assert recommendations["municipal_planners"] == []
    assert any("0.0 mm" in item and "precipitation" in item.lower() for item in answer["sections"]["evidence"])
    assert "daily rainfall" in " ".join(answer["sections"]["interpreted"])


def test_fallback_distinguishes_hourly_accumulation_daily_fields_and_historical_comparisons():
    weather = _weather().model_dump(mode="json")
    forecast_time = "2026-10-09T14:00:00+13:00"
    weather["forecast"]["precipitation"] = {
        "value": 0.0,
        "unit": "mm",
        "status": "AVAILABLE",
        "data_type": "FORECAST",
        "forecast_time": forecast_time,
        "provenance": {"source": "Open-Meteo"},
    }
    weather["forecast"]["precipitation_probability"] = {
        "value": 12.0,
        "unit": "%",
        "status": "AVAILABLE",
        "data_type": "FORECAST",
        "forecast_time": forecast_time,
        "provenance": {"source": "Open-Meteo"},
    }
    weather["daily"] = {
        "times": ["2026-10-09", "2026-10-10"],
        "values": {
            "temperature_2m_max": [20.0, 22.0],
            "precipitation_sum": [0.0, 8.0],
            "rain_sum": [0.0, 6.0],
        },
        "units": {
            "temperature_2m_max": "°C",
            "precipitation_sum": "mm",
            "rain_sum": "mm",
        },
    }
    weather["historical_anomalies"] = {
        "status": "AVAILABLE",
        "baseline_period": "1991-2020",
        "anomalies": [
            {
                "date": "2026-10-09",
                "indicator": indicator,
                "current_value": current,
                "unit": "°C" if indicator == "temperature_max" else "mm",
                "anomaly": anomaly,
                "percentile": percentile,
                "historical_baseline": {
                    "mean": mean,
                    "median": mean,
                    "p25": mean,
                    "p75": mean,
                    "minimum": mean,
                    "maximum": mean,
                    "sample_count": 30,
                },
                "status": "AVAILABLE",
            }
            for indicator, current, anomaly, percentile, mean in (
                ("temperature_max", 20.0, -5.0, 20.0, 25.0),
                ("precipitation", 0.0, -1.0, 10.0, 1.0),
                ("rainfall", 0.0, -0.5, 15.0, 0.5),
            )
        ],
    }

    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
        },
        question="Summarize the forecast and its historical context.",
    )

    evidence = answer["sections"]["evidence"]
    assert any("Selected-hour precipitation accumulation: 0.0 mm" in item for item in evidence)
    assert any("Selected-hour precipitation probability: 12.0 %" in item for item in evidence)
    assert any("Daily total precipitation forecast: 0.0 mm" in item for item in evidence)
    assert any("Daily rain-only forecast: 0.0 mm" in item for item in evidence)
    assert any("Daily total precipitation forecast for 2026-10-09: 0.0 mm" in item for item in evidence)
    assert any("Daily rain-only total forecast for 2026-10-09: 0.0 mm" in item for item in evidence)
    assert not any("Daily total precipitation forecast: 8.0 mm" in item for item in evidence)
    assert answer["source"] == "deterministic_evidence_fallback"
    assert "daily amounts, not the selected-hour accumulation" not in answer["ai_response"]
    assert "Rainfall totals alone do not establish" in " ".join(answer["sections"]["interpreted"])
    assert answer["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }
    assert "6.0 mm on 2026-10-10" not in answer["ai_response"]
    assert "Highest daily rain-only forecast: 6 mm on Oct 10, 2026" in " ".join(answer["sections"]["evidence"])
    assert len(answer["ai_response"].split(". ")) <= 2


def test_high_historical_percentile_and_rainfall_alone_do_not_trigger_recommendations():
    for indicator, current, percentile, expected_fact in (
        (
            "precipitation",
            12.0,
            95.0,
            "Daily total precipitation forecast for 2026-10-09: 12.0 mm",
        ),
        (
            "rainfall",
            10.0,
            94.0,
            "Daily rain-only total forecast for 2026-10-09: 10.0 mm",
        ),
    ):
        weather = _weather().model_dump(mode="json")
        weather["daily"] = {
            "times": ["2026-10-09", "2026-10-10"],
            "values": {
                "precipitation_sum": [12.0, 30.0],
                "rain_sum": [10.0, 25.0],
            },
            "units": {"precipitation_sum": "mm", "rain_sum": "mm"},
        }
        weather["historical_anomalies"] = {
            "status": "AVAILABLE",
            "baseline_period": "1991-2020",
            "anomalies": [{
                "date": "2026-10-09",
                "indicator": indicator,
                "current_value": current,
                "unit": "mm",
                "anomaly": current - 1.0,
                "percentile": percentile,
                "historical_baseline": {"mean": 1.0},
                "status": "AVAILABLE",
            }],
        }
        answer = render_global_evidence_answer(
            location=LOCATION,
            tool_results={
                "get_weather_evidence": weather,
                "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
            },
            question="Summarize the forecast and historical context.",
        )
        assert answer["sections"]["recommendations"] == {
            "residents_and_workers": [],
            "municipal_planners": [],
        }
        assert expected_fact in " ".join(answer["sections"]["evidence"])
        assert "2026-10-10" not in " ".join(answer["sections"]["evidence"])
        assert not answer["sections"]["recommended"]


def test_ai_recommendations_without_a_supported_action_are_not_displayed():
    weather = _weather().model_dump(mode="json")
    weather["daily"] = {
        "times": ["2026-10-09"],
        "values": {"precipitation_sum": [12.0], "rain_sum": [10.0]},
        "units": {"precipitation_sum": "mm", "rain_sum": "mm"},
    }
    historical = {
        "status": "AVAILABLE",
        "baseline_period": "1991-2020",
        "anomalies": [{
            "date": "2026-10-09",
            "indicator": "precipitation",
            "current_value": 12.0,
            "unit": "mm",
            "anomaly": 11.0,
            "percentile": 95.0,
            "historical_baseline": {"mean": 1.0},
            "status": "AVAILABLE",
        }],
    }
    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_historical_baseline": historical,
        },
        question="What should people consider?",
        intent="preparedness",
        ai_guidance={
            "interpretation": "The daily precipitation forecast is elevated against its October context.",
            "recommendations": {
                "residents_and_workers": [{
                    "topic": "rainfall",
                    "recommendation": "Check local forecast updates before making rainfall-dependent plans.",
                }],
                "municipal_planners": [{
                    "topic": "rainfall",
                    "recommendation": "Compare with verified local gauges and operating thresholds before drainage decisions.",
                }],
            },
        },
    )

    assert answer["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }


def test_interpretation_separates_mean_and_percentile_and_uses_coming_forecasts():
    weather = _weather().model_dump(mode="json")
    weather["daily"] = {
        "times": [
            "2026-10-11",
            "2026-10-12",
            "2026-10-13",
            "2026-10-14",
            "2026-10-15",
        ],
        "values": {
            "precipitation_sum": [2.0, 12.0, 0.0, 5.0, 0.0],
            "rain_sum": [1.8, 8.0, 0.0, 3.0, 0.0],
            "uv_index_max": [4.0, 7.0, 3.0, 2.0, 0.0],
        },
        "units": {
            "precipitation_sum": "mm",
            "rain_sum": "mm",
            "uv_index_max": "",
        },
    }
    weather["historical_anomalies"] = {
        "status": "AVAILABLE",
        "baseline_period": "1991-2020",
        "anomalies": [{
            "date": "2026-10-11",
            "indicator": "precipitation",
            "current_value": 2.0,
            "unit": "mm",
            "anomaly": -0.83,
            "percentile": 70.1,
            "historical_baseline": {"mean": 2.830645},
            "status": "AVAILABLE",
        }],
    }

    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
        },
        question="Compare the historical rainfall forecast.",
    )

    interpretation = answer["ai_response"]
    assert "ERA5 1991-2020" in interpretation
    assert "historical percentile" in " ".join(answer["sections"]["interpreted"])
    assert "not a probability of danger" in " ".join(answer["sections"]["interpreted"])
    assert "2026-10-12" not in interpretation
    assert "Daily total precipitation for that date" not in interpretation
    assert "daily maximum UV index forecast" not in interpretation
    assert len(interpretation.split(". ")) <= 2
    assert any("total precipitation that day is 12 mm" in item
               for item in answer["sections"]["evidence"])
    assert any("Highest daily rain-only forecast: 8 mm on Oct 12, 2026" in item
               for item in answer["sections"]["evidence"])
    assert answer["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }


def test_ai_date_specific_era5_mean_wording_uses_labeled_deterministic_fallback():
    weather = _weather().model_dump(mode="json")
    weather["daily"] = {
        "times": ["2026-10-09"],
        "values": {"precipitation_sum": [2.0], "rain_sum": [1.8]},
        "units": {"precipitation_sum": "mm", "rain_sum": "mm"},
    }
    weather["historical_anomalies"] = {
        "status": "AVAILABLE",
        "baseline_period": "1991-2020",
        "anomalies": [{
            "date": "2026-10-09",
            "indicator": "precipitation",
            "current_value": 2.0,
            "unit": "mm",
            "anomaly": -0.83,
            "percentile": 70.1,
            "historical_baseline": {"mean": 2.830645},
            "status": "AVAILABLE",
        }],
    }
    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_historical_baseline": weather["historical_anomalies"],
        },
        question="Compare the historical rainfall forecast.",
        intent="data",
        ai_guidance={
            "interpretation": (
                "The rainfall forecast is below the historical mean for this date."
            ),
            "recommendations": {},
        },
    )

    assert answer["source"] == "deterministic_evidence_fallback"
    assert "2.8 mm; ERA5 1991-2020" in answer["ai_response"]
    assert "historical mean for this date" not in answer["ai_response"]


def test_cold_forecast_fallback_does_not_recommend_shade_or_cooling():
    weather = _weather().model_dump(mode="json")
    weather["forecast"]["temperature"]["value"] = 7.7
    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
        },
        question="What should people consider?",
    )

    recommendations = answer["sections"]["recommendations"]
    assert all(
        "shade" not in item["recommendation"].lower()
        and "cooling" not in item["recommendation"].lower()
        for items in recommendations.values()
        for item in items
    )
    assert "7.7 °C" in answer["ai_response"]


def test_copilot_fallback_is_concise_rounded_and_uses_readable_local_dates():
    weather = _weather().model_dump(mode="json")
    weather["daily"] = {
        "times": ["2026-10-09", "2026-10-10"],
        "values": {
            "temperature_2m_max": [30.26, 31.27],
            "rain_sum": [0.04, 5.678],
            "precipitation_sum": [0.06, 7.89],
        },
        "units": {
            "temperature_2m_max": "°C",
            "rain_sum": "mm",
            "precipitation_sum": "mm",
        },
    }
    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": weather,
            "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
        },
        question="What heat conditions are forecast?",
    )

    assert answer["ai_response"] == "The forecast high is 31.3 °C on Oct 10, 2026."
    assert "2026-10-10" not in answer["ai_response"]
    assert "31.27" not in answer["ai_response"]
    assert "not yet have enough information to assess local heat risk" in " ".join(
        answer["sections"]["interpreted"]
    )
    assert any("Highest daily rain-only forecast: 5.7 mm on Oct 10, 2026" in item
               for item in answer["sections"]["evidence"])
    assert "0.04 mm" in " ".join(answer["sections"]["evidence"])


def test_ai_interpretation_rounds_numbers_and_formats_iso_timestamps():
    answer = render_global_evidence_answer(
        location=LOCATION,
        tool_results={
            "get_weather_evidence": _weather().model_dump(mode="json"),
            "get_flood_evidence": _flood_unavailable().model_dump(mode="json"),
        },
        question="What conditions are forecast?",
        ai_guidance={
            "interpretation": (
                "The forecast is 29.067 °C at 2026-10-09T14:00:00+13:00."
            ),
            "recommendations": {},
        },
    )

    assert answer["ai_response"] == "The forecast is 29.1 °C at Oct 9, 2026 at 2:00 PM (UTC+13:00)."


def test_global_evidence_tool_deduplicates_concurrent_provider_reads():
    class CountingWeatherProvider(FakeWeatherProvider):
        calls = 0

        def get_weather_evidence_for_location(self, selected_location):
            self.calls += 1
            time.sleep(0.05)
            return _weather()

    provider = CountingWeatherProvider()
    tools, _ = build_global_evidence_tools(
        location=LOCATION,
        weather_provider=provider,
        flood_service=FakeFloodService(),
        historical_service=FakeHistoricalService(),
        ensemble_service=FakeEnsembleService(),
    )
    weather_tool = next(item for item in tools if item.tool_name == "get_weather_evidence")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: weather_tool(), range(4)))

    assert provider.calls == 1
    assert all(result["status"] == "DEGRADED" for result in results)


def test_global_weather_tool_preserves_weather_when_history_raises():
    class RaisingHistoricalService:
        def calculate(self, _weather):
            raise RuntimeError("archive failure")

    tools, _ = build_global_evidence_tools(
        location=LOCATION,
        weather_provider=FakeWeatherProvider(),
        flood_service=FakeFloodService(),
        historical_service=RaisingHistoricalService(),
        ensemble_service=FakeEnsembleService(),
    )
    weather_tool = next(item for item in tools if item.tool_name == "get_weather_evidence")

    result = weather_tool()

    assert result["forecast"]["temperature"]["value"] == 29.0
    assert result["historical_anomalies"]["status"] == "UNAVAILABLE"
    assert "does not invalidate available weather" in result["historical_anomalies"]["limitations"][0]


def test_global_evidence_endpoint_uses_deterministic_structured_fallback(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    with TestClient(app) as client:
        app.state.rainfall_provider = FakeWeatherProvider()
        app.state.weather_evidence_service = WeatherEvidenceService(FakeWeatherProvider())
        app.state.flood_evidence_service = FakeFloodService()
        app.state.historical_baseline_service = FakeHistoricalService()
        app.state.ensemble_evidence_service = FakeEnsembleService()
        response = client.post(
            "/api/v1/ai/evidence",
            json={
                "latitude": LOCATION["lat"],
                "longitude": LOCATION["lng"],
                "name": LOCATION["name"],
                "admin1": LOCATION["state"],
                "country": LOCATION["country"],
                "question": "What evidence is available?",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "deterministic_evidence_fallback"
    assert body["ai_response"] is not None
    assert "selected-hour temperature forecast" in body["ai_response"]
    assert any("29.0 °C (FORECAST" in item for item in body["sections"]["evidence"])
    assert any("Unavailable in this result" in item for item in body["sections"]["interpreted"])
    assert body["sections"]["recommended"] == []
    assert body["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }


def test_global_evidence_endpoint_returns_generated_guidance_from_openrouter(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    monkeypatch.setattr(settings, "ai_agent_enabled", True)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    guidance = {
        "interpretation": "This forecast is cool; humidity alone does not establish heat stress.",
        "recommendations": {
            "residents_and_workers": [{
                "topic": "heat",
                "recommendation": "Use the forecast and local observations together when planning outdoor work.",
            }],
            "municipal_planners": [{
                "topic": "heat",
                "recommendation": "Compare this forecast with local observations before allocating heat-response resources.",
            }],
        },
    }

    async def fake_workflow(**_kwargs):
        return "preparedness", guidance

    monkeypatch.setattr("app.api.routes.run_openrouter_global_evidence", fake_workflow)
    with TestClient(app) as client:
        app.state.rainfall_provider = FakeWeatherProvider()
        app.state.weather_evidence_service = WeatherEvidenceService(FakeWeatherProvider())
        app.state.flood_evidence_service = FakeFloodService()
        app.state.historical_baseline_service = FakeHistoricalService()
        app.state.ensemble_evidence_service = FakeEnsembleService()
        response = client.post(
            "/api/v1/ai/evidence",
            json={
                "latitude": LOCATION["lat"],
                "longitude": LOCATION["lng"],
                "name": LOCATION["name"],
                "admin1": LOCATION["state"],
                "country": LOCATION["country"],
                "question": "What should people consider?",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "openrouter_ai_evidence_guidance"
    assert body["ai_response"] == guidance["interpretation"]
    assert body["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }


def test_global_evidence_endpoint_returns_deterministic_fallback_after_ai_failure(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    monkeypatch.setattr(settings, "ai_agent_enabled", True)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    async def failed_workflow(**_kwargs):
        raise RuntimeError("model attempts failed")

    monkeypatch.setattr("app.api.routes.run_openrouter_global_evidence", failed_workflow)
    with TestClient(app) as client:
        app.state.rainfall_provider = FakeWeatherProvider()
        app.state.weather_evidence_service = WeatherEvidenceService(FakeWeatherProvider())
        app.state.flood_evidence_service = FakeFloodService()
        app.state.historical_baseline_service = FakeHistoricalService()
        app.state.ensemble_evidence_service = FakeEnsembleService()
        response = client.post(
            "/api/v1/ai/evidence",
            json={
                "latitude": LOCATION["lat"],
                "longitude": LOCATION["lng"],
                "name": LOCATION["name"],
                "admin1": LOCATION["state"],
                "country": LOCATION["country"],
                "question": "What should people consider?",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "deterministic_evidence_fallback"
    assert body["ai_response"]


def test_global_evidence_endpoint_returns_fallback_when_ai_workflow_times_out(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    monkeypatch.setattr(settings, "ai_agent_enabled", True)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr("app.api.routes.GLOBAL_EVIDENCE_AI_TIMEOUT_SECONDS", 0.01)
    workflow_started = Event()
    workflow_finished = Event()

    async def slow_workflow(**_kwargs):
        workflow_started.set()
        try:
            await asyncio.sleep(2)
            return "preparedness", {
                "interpretation": "This late result must not replace the deterministic response.",
                "recommendations": {
                    "residents_and_workers": [],
                    "municipal_planners": [],
                },
            }
        finally:
            workflow_finished.set()

    monkeypatch.setattr("app.api.routes.run_openrouter_global_evidence", slow_workflow)
    with TestClient(app) as client:
        app.state.rainfall_provider = FakeWeatherProvider()
        app.state.weather_evidence_service = WeatherEvidenceService(FakeWeatherProvider())
        app.state.flood_evidence_service = FakeFloodService()
        app.state.historical_baseline_service = FakeHistoricalService()
        app.state.ensemble_evidence_service = FakeEnsembleService()
        response = client.post(
            "/api/v1/ai/evidence",
            json={
                "latitude": LOCATION["lat"],
                "longitude": LOCATION["lng"],
                "name": LOCATION["name"],
                "admin1": LOCATION["state"],
                "country": LOCATION["country"],
                "question": "What should people consider?",
            },
        )

    assert workflow_started.is_set()
    assert workflow_finished.is_set()
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "deterministic_evidence_fallback"
    assert body["ai_response"]


def test_global_evidence_endpoint_marks_slow_provider_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    monkeypatch.setattr(settings, "ai_agent_enabled", True)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr("app.api.routes.GLOBAL_EVIDENCE_TOOLS_TIMEOUT_SECONDS", 0.05)
    provider_started = Event()
    release_provider = Event()
    workflow_called = Event()

    async def slow_workflow(**_kwargs):
        workflow_called.set()
        return "other", {}

    monkeypatch.setattr("app.api.routes.run_openrouter_global_evidence", slow_workflow)
    weather_service = WeatherEvidenceService(FakeWeatherProvider())
    original_get_weather = weather_service.get_weather_evidence_for_location

    def slow_get_weather(location):
        provider_started.set()
        release_provider.wait(timeout=2)
        return original_get_weather(location)

    monkeypatch.setattr(weather_service, "get_weather_evidence_for_location", slow_get_weather)
    try:
        with TestClient(app) as client:
            app.state.rainfall_provider = FakeWeatherProvider()
            app.state.weather_evidence_service = weather_service
            app.state.flood_evidence_service = FakeFloodService()
            app.state.historical_baseline_service = FakeHistoricalService()
            app.state.ensemble_evidence_service = FakeEnsembleService()
            response = client.post(
                "/api/v1/ai/evidence",
                json={
                    "latitude": LOCATION["lat"],
                    "longitude": LOCATION["lng"],
                    "name": LOCATION["name"],
                    "admin1": LOCATION["state"],
                    "country": LOCATION["country"],
                    "question": "What should people consider?",
                },
            )

        assert provider_started.wait(timeout=1)
        assert response.status_code == 200
        assert response.json()["source"] == "deterministic_evidence_fallback"
        assert not workflow_called.is_set()
    finally:
        release_provider.set()


def test_global_evidence_endpoint_keeps_map_point_advice_population_neutral(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "real")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    with TestClient(app) as client:
        app.state.rainfall_provider = FakeWeatherProvider()
        app.state.weather_evidence_service = WeatherEvidenceService(FakeWeatherProvider())
        app.state.flood_evidence_service = FakeFloodService()
        app.state.historical_baseline_service = FakeHistoricalService()
        app.state.ensemble_evidence_service = FakeEnsembleService()
        response = client.post(
            "/api/v1/ai/evidence",
            json={
                "latitude": MAP_POINT_LOCATION["lat"],
                "longitude": MAP_POINT_LOCATION["lng"],
                "name": MAP_POINT_LOCATION["name"],
                "admin1": MAP_POINT_LOCATION["state"],
                "country": MAP_POINT_LOCATION["country"],
                "selection_type": "map_point",
                "question": "Summarize the weather at this point.",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "deterministic_evidence_fallback"
    assert body["sections"]["recommendations"] == {
        "residents_and_workers": [],
        "municipal_planners": [],
    }
    assert body["sections"]["recommended"] == [
        "Weather evidence is for the selected coordinates and provider grid cells. No nearby settlement or population-exposure context is verified for this pin."
    ]
    assert any("coordinate-only map selection" in item for item in body["sections"]["interpreted"])


def test_global_evidence_endpoint_rejects_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "data_mode", "demo")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/evidence",
            json={
                "latitude": LOCATION["lat"],
                "longitude": LOCATION["lng"],
                "name": LOCATION["name"],
                "admin1": LOCATION["state"],
                "country": LOCATION["country"],
                "question": "Explain these values.",
            },
        )

    assert response.status_code == 503
    assert "real data mode" in response.json()["detail"]