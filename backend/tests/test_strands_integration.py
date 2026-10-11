from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-engine"))
sys.path.insert(0, str(ROOT / "ai"))
sys.path.insert(0, str(ROOT / "backend"))

from strands import Agent
from strands.models import OpenAIModel

from app.providers.demo import DemoDataStore, DemoRainfallProvider
from app.services.risk_service import RiskService
from app.config import DEFAULT_OPENROUTER_FALLBACK_MODELS, settings
from app.main import app
from heatshield_ai.agent import _include_strands_tool_results, explain_risk, fallback_explanation
from heatshield_ai.strands_agent import run_strands_workflow
from heatshield_ai.tools import build_heatshield_tools


def _services() -> tuple[RiskService, DemoRainfallProvider]:
    store = DemoDataStore(ROOT / "data" / "demo")
    return RiskService(store), DemoRainfallProvider(store)


def test_strands_agent_initializes_with_approved_heatshield_tools():
    risk_service, rainfall_provider = _services()
    tools, _ = build_heatshield_tools(risk_service, rainfall_provider)
    model = OpenAIModel(
        client_args={"api_key": "test-key", "base_url": "https://openrouter.ai/api/v1"},
        model_id="openai/gpt-4o-mini",
    )

    agent = Agent(
        model=model,
        tools=tools,
        system_prompt="Use only registered HeatShield tools.",
        callback_handler=None,
    )

    assert isinstance(agent, Agent)
    assert isinstance(model, OpenAIModel)
    assert {item.tool_name for item in tools} == {
        "get_risk_assessment",
        "get_environmental_evidence",
        "get_simulation_result",
        "get_data_quality",
    }
    assert all("aws" not in item.tool_name.lower() for item in tools)


def test_configured_model_order_uses_supplied_free_models_without_duplicates():
    configured = list(
        dict.fromkeys(
            [
                "liquid/lfm-2.5-2.6b:free",
                *DEFAULT_OPENROUTER_FALLBACK_MODELS.split(","),
            ]
        )
    )

    assert configured[0] == "liquid/lfm-2.5-2.6b:free"
    assert configured[-1] == "openrouter/free"
    assert len(configured) == 16
    assert set(configured) == {
        "apodex/apodex-1.1-mini:free",
        "cohere/north-mini-code:free",
        "dots-studio/dots-3-note-preview:free",
        "google/gemma-4-26b-a4b-it:free",
        "google/gemma-4-31b-it:free",
        "inclusionai/ling-3.1-flash",
        "liquid/lfm-2.5-2.6b:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-lightning:free",
        "openrouter/free",
        "poolside/laguna-s-2.1:free",
        "poolside/laguna-xs-2.1:free",
        "thinkingmachines/inkling:free",
        "thinkingmachines/inkling-small:free",
    }


def test_tools_return_risk_evidence_quality_and_engine_metadata():
    risk_service, rainfall_provider = _services()
    tools, _ = build_heatshield_tools(risk_service, rainfall_provider)
    by_name = {item.tool_name: item for item in tools}

    sikkim_score = risk_service.assess("sikkim")["riskScore"]
    delhi_score = risk_service.assess("delhi")["riskScore"]
    assessment = by_name["get_risk_assessment"](location_id="sikkim")
    heat_assessment = by_name["get_risk_assessment"](location_id="delhi")
    evidence = by_name["get_environmental_evidence"](location_id="sikkim")
    quality = by_name["get_data_quality"](location_id="sikkim")

    assert assessment["location"]["id"] == "sikkim"
    assert assessment["engine_version"] == "0.1.0"
    assert assessment["thresholds_version"].startswith("sha256:")
    assert heat_assessment["scenario"] == "extreme_heat"
    assert heat_assessment["heat_risk"]["level"] == heat_assessment["overall_risk_level"]
    rainfall = [item for item in evidence["measurements"] if item["provider"] == "rainfall"]
    assert rainfall
    assert all(item["data_type"] == "DEMO" for item in rainfall)
    assert all({"source", "retrieval_time", "value", "unit", "quality", "status"} <= item.keys()
               for item in rainfall)
    assert quality["status"] == "available"
    assert "DEMO" in quality["data_types"]
    assert risk_service.assess("sikkim")["riskScore"] == sikkim_score
    assert risk_service.assess("delhi")["riskScore"] == delhi_score


def test_simulation_tool_uses_risk_service_without_mutating_active_state():
    risk_service, rainfall_provider = _services()
    tools, _ = build_heatshield_tools(risk_service, rainfall_provider)
    simulation_tool = next(item for item in tools if item.tool_name == "get_simulation_result")

    result = simulation_tool(
        location_id="sikkim",
        simulation_parameters={"rainfallIntensityMmHr": 80.0, "riverLevelM": 8.0},
    )

    assert result["status"] == "available"
    assert result["baseline_assessment"]["overall_risk_score"] != result["simulated_assessment"]["overall_risk_score"]
    assert result["score_delta"] > 0
    assert result["changed_variables"]
    assert risk_service.store.simulation is None


def test_tools_validate_unknown_locations_and_simulation_inputs():
    risk_service, rainfall_provider = _services()
    tools, _ = build_heatshield_tools(risk_service, rainfall_provider)
    by_name = {item.tool_name: item for item in tools}

    assert by_name["get_risk_assessment"](location_id="unknown")["status"] == "unavailable"
    invalid = by_name["get_simulation_result"](
        location_id="sikkim",
        simulation_parameters={"rainfallIntensityMmHr": 501.0},
    )
    assert invalid["status"] == "unavailable"


def test_data_quality_reports_missing_stale_and_unavailable_rainfall():
    risk_service, _ = _services()
    stale_time = "2020-01-01T00:00:00+00:00"

    class StaleRainfallProvider:
        def get_rainfall_observations(self, location_id: str):
            return {
                "source": "Open-Meteo",
                "sourceUrl": "https://open-meteo.com/",
                "timestamp": stale_time,
                "retrievedAt": stale_time,
                "dataType": "forecast",
                "location": {"id": location_id, "lat": 27.533, "lng": 88.512},
                "status": "unavailable",
                "dataQuality": "Forecast is stale; no valid current forecast is available.",
                "values": {
                    "rainfallIntensityMmHr": {
                        "value": None,
                        "unit": "mm/h",
                        "status": "unavailable",
                        "dataType": "FORECAST",
                        "validTime": None,
                    }
                },
            }

    tools, _ = build_heatshield_tools(risk_service, StaleRainfallProvider())
    evidence = next(item for item in tools if item.tool_name == "get_environmental_evidence")(
        location_id="sikkim"
    )
    quality = next(item for item in tools if item.tool_name == "get_data_quality")(
        location_id="sikkim"
    )

    rainfall = next(item for item in evidence["measurements"] if item["provider"] == "rainfall")
    assert rainfall["data_type"] == "FORECAST"
    assert rainfall["value"] is None
    assert "rainfallIntensityMmHr" in quality["missing_data"]
    assert "rainfallIntensityMmHr" in quality["stale_data"]
    assert "rainfall" in quality["unavailable_providers"]


def test_forecast_evidence_is_not_relabelled_as_observed_and_provider_failure_is_explicit():
    risk_service, _ = _services()

    class ForecastProvider:
        def get_rainfall_observations(self, location_id: str):
            forecast_time = "2030-01-01T00:00:00+00:00"
            provenance = {
                "source": "Open-Meteo",
                "source_url": "https://open-meteo.com/",
                "location_id": location_id,
                "latitude": 27.533,
                "longitude": 88.512,
                "data_type": "FORECAST",
                "forecast_time": forecast_time,
                "retrieval_time": "2029-12-31T23:00:00+00:00",
                "quality": "Model forecast; not a ground observation.",
                "status": "forecast",
                "value": 12.4,
                "unit": "mm/h",
            }
            return {
                "source": "Open-Meteo",
                "sourceUrl": "https://open-meteo.com/",
                "timestamp": provenance["retrieval_time"],
                "retrievedAt": provenance["retrieval_time"],
                "dataType": "forecast",
                "location": {"id": location_id, "lat": 27.533, "lng": 88.512},
                "status": "available",
                "dataQuality": provenance["quality"],
                "values": {
                    "rainfallIntensityMmHr": {
                        "value": 12.4,
                        "unit": "mm/h",
                        "status": "forecast",
                        "dataType": "FORECAST",
                        "validTime": forecast_time,
                        "provenance": provenance,
                    }
                },
            }

    tools, _ = build_heatshield_tools(risk_service, ForecastProvider())
    evidence_tool = next(item for item in tools if item.tool_name == "get_environmental_evidence")
    evidence = evidence_tool(location_id="sikkim")
    rainfall = next(item for item in evidence["measurements"] if item["provider"] == "rainfall")

    assert rainfall["data_type"] == "FORECAST"
    assert rainfall["forecast_time"] == "2030-01-01T00:00:00+00:00"
    assert rainfall["observation_time"] is None
    assert rainfall["value"] == 12.4

    class FailedProvider:
        def get_rainfall_observations(self, _location_id: str):
            raise RuntimeError("secret details must not escape")

    failed_tools, _ = build_heatshield_tools(risk_service, FailedProvider())
    failed = next(item for item in failed_tools if item.tool_name == "get_environmental_evidence")(
        location_id="sikkim"
    )
    assert failed["providers"]["rainfall"]["status"] == "unavailable"
    assert "secret details" not in str(failed)
    response = fallback_explanation(risk_service.assess("sikkim"))
    _include_strands_tool_results(
        response,
        {"get_environmental_evidence": failed},
    )
    assert any(
        "rainfall environmental evidence is unavailable" in line
        for line in response["sections"]["observed"]
    )


def test_ai_endpoint_uses_strands_without_changing_response_contract(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "data_mode", "demo")
    monkeypatch.setattr(settings, "ai_agent_enabled", True)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    def fake_workflow(**kwargs):
        assert kwargs["location_id"] == "sikkim"
        return "data", {}

    monkeypatch.setattr("heatshield_ai.strands_agent.run_strands_workflow", fake_workflow)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/chat",
            json={"locationId": "sikkim", "message": "Is rainfall observed?"},
        )

    assert response.status_code == 200
    assert response.json()["source"] == "strands_tools_deterministic_response"
    assert response.json()["answer"]
    assert "sections" in response.json()


def test_strands_workflow_uses_tools_and_ignores_model_authored_facts(monkeypatch):
    risk_service, rainfall_provider = _services()
    calls: list[str] = []

    class FakeAgent:
        def __init__(self, *, model, tools, system_prompt, callback_handler):
            assert isinstance(model, OpenAIModel)
            assert "Never invent measurements" in system_prompt
            assert callback_handler is None
            self.tools = tools

        def __call__(self, _prompt):
            evidence_tool = next(
                item for item in self.tools if item.tool_name == "get_environmental_evidence"
            )
            result = evidence_tool(location_id="sikkim")
            calls.extend(item["data_type"] for item in result["measurements"])
            return SimpleNamespace(
                message={
                    "content": [
                        {"text": '{"intent":"data","summary":"999 mm observed; evacuate immediately"}'}
                    ]
                }
            )

    monkeypatch.setattr("strands.Agent", FakeAgent)
    intent, results = run_strands_workflow(
        question="Is rainfall observed?",
        location_id="sikkim",
        risk_service=risk_service,
        rainfall_provider=rainfall_provider,
        api_key="test-key",
        model_ids=["google/gemma-4-31b-it:free"],
        base_url="https://openrouter.ai/api/v1",
    )
    response = fallback_explanation(
        risk_service.assess("sikkim"),
        "Is rainfall observed?",
        intent=intent,
    )
    _include_strands_tool_results(response, results)
    text = str(response)

    assert intent == "data"
    assert calls and all(value == "DEMO" for value in calls)
    assert "999 mm" not in text
    assert "evacuate immediately" not in text
    assert "DEMO" in text


def test_strands_falls_back_to_next_configured_model(monkeypatch):
    risk_service, rainfall_provider = _services()
    attempted_models: list[str] = []

    class FakeAgent:
        def __init__(self, *, model, tools, system_prompt, callback_handler):
            self.model = model
            self.tools = tools

        def __call__(self, _prompt):
            attempted_models.append(self.model.config["model_id"])
            if len(attempted_models) == 1:
                raise RuntimeError("model unavailable")
            risk_tool = next(item for item in self.tools if item.tool_name == "get_risk_assessment")
            risk_tool(location_id="sikkim")
            return SimpleNamespace(
                message={"content": [{"text": '{"intent":"why"}'}]}
            )

    monkeypatch.setattr("strands.Agent", FakeAgent)
    intent, tool_results = run_strands_workflow(
        question="Why is Sikkim high risk?",
        location_id="sikkim",
        risk_service=risk_service,
        rainfall_provider=rainfall_provider,
        api_key="test-key",
        model_ids=["model-one:free", "model-two:free"],
        base_url="https://openrouter.ai/api/v1",
    )

    assert attempted_models == ["model-one:free", "model-two:free"]
    assert intent == "why"
    assert "get_risk_assessment" in tool_results


def test_missing_tool_values_are_reported_without_substituting_zero():
    response = fallback_explanation({"location": {"name": "Sikkim"}, "riskLevel": "high"})
    _include_strands_tool_results(
        response,
        {
            "get_environmental_evidence": {
                "measurements": [
                    {
                        "name": "rainfallIntensityMmHr",
                        "value": None,
                        "unit": "mm/h",
                        "data_type": "FORECAST",
                        "source": "Open-Meteo",
                        "status": "unavailable",
                    }
                ]
            }
        },
    )

    assert any("unavailable (FORECAST; Open-Meteo" in line for line in response["sections"]["observed"])
    assert all("0 mm/h" not in line for line in response["sections"]["observed"])


def test_missing_key_and_strands_failure_use_deterministic_response(monkeypatch):
    risk_service, rainfall_provider = _services()
    assessment = risk_service.assess("sikkim")

    no_key = asyncio.run(
        explain_risk(
            assessment,
            question="Why is Sikkim high risk?",
            agent_enabled=True,
            risk_service=risk_service,
            rainfall_provider=rainfall_provider,
        )
    )
    assert no_key["source"] == "deterministic_fallback"

    def fail_agent(**_kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("heatshield_ai.strands_agent.run_strands_workflow", fail_agent)
    failed = asyncio.run(
        explain_risk(
            assessment,
            question="Why is Sikkim high risk?",
            api_key="test-key",
            agent_enabled=True,
            risk_service=risk_service,
            rainfall_provider=rainfall_provider,
        )
    )
    assert failed["source"] == "deterministic_fallback_after_strands_error"
    assert failed["sections"]["calculated"] == fallback_explanation(assessment)["sections"]["calculated"]


def test_openrouter_failure_keeps_existing_deterministic_fallback(monkeypatch):
    async def fail_request(**_kwargs):
        raise RuntimeError("OpenRouter unavailable")

    monkeypatch.setattr("heatshield_ai.agent.chat_completion", fail_request)
    risk_service, _ = _services()

    response = asyncio.run(
        explain_risk(
            risk_service.assess("delhi"),
            question="Why is Delhi high risk?",
            api_key="test-key",
        )
    )

    assert response["source"] == "deterministic_fallback_after_error"
    assert "Delhi" in response["summary"]


def test_legacy_openrouter_path_retries_models_in_configured_order(monkeypatch):
    attempted: list[str] = []

    async def classify(*, model, **_kwargs):
        attempted.append(model)
        if model == "first:free":
            raise RuntimeError("first model unavailable")
        return '{"intent":"factors"}'

    monkeypatch.setattr("heatshield_ai.agent.chat_completion", classify)
    risk_service, _ = _services()

    response = asyncio.run(
        explain_risk(
            risk_service.assess("sikkim"),
            question="Which factors contributed?",
            api_key="test-key",
            model="first:free",
            fallback_models=["second:free", "third:free"],
        )
    )

    assert attempted == ["first:free", "second:free"]
    assert response["source"] == "openrouter_intent_structured_response"
    assert any("Largest calculated contributors" in line for line in response["sections"]["interpreted"])
