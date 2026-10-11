from __future__ import annotations

import json
import asyncio
from typing import Any

from heatshield_ai.openrouter_provider import chat_completion, parse_explanation_json
from heatshield_ai.prompts import INTENT_PROMPT

SUPPORTED_INTENTS = {
    "why",
    "changes",
    "factors",
    "data",
    "areas",
    "preparedness",
    "what_if",
    "missing",
    "other",
}

DISCLAIMER = (
    "HeatShield provides decision-support based on supplied data — not official emergency warnings."
)


def fallback_explanation(
    assessment: dict[str, Any],
    question: str | None = None,
    *,
    intent: str | None = None,
) -> dict[str, Any]:
    factors = assessment.get("contributingFactors") or []
    obs = assessment.get("observations") or []
    provenance = assessment.get("dataProvenance", "DEMO")
    input_label = (
        "Observed"
        if provenance == "LIVE"
        else "SIMULATED INPUT"
        if provenance == "DEMO_SIMULATION"
        else "DEMO INPUT"
    )
    observed_lines = [
        f"{input_label}: {o['label']}: {o['value']}"
        for o in obs
        if o.get("kind") == "observed"
    ]
    calculated_evidence = [
        f"{o['label']}: {o['value']}"
        for o in obs
        if o.get("kind") == "calculated"
    ]
    historical_evidence = [
        f"{o['label']}: {o['value']}"
        for o in obs
        if o.get("kind") == "historical"
    ]
    unavailable = [o["label"] for o in obs if o.get("kind") == "unavailable"]
    if unavailable:
        observed_lines.append("Data unavailable: " + ", ".join(unavailable))
    for name, provider in (assessment.get("providerObservations") or {}).items():
        observed_lines.append(
            f"{name} provider: {provider.get('source', 'Source unavailable')}; "
            f"status: {provider.get('status', 'unavailable')}; "
            f"retrieved: {provider.get('timestamp', 'timestamp unavailable')}; "
            f"quality: {provider.get('dataQuality', 'quality unavailable')}"
        )

    component_labels = {
        "rainfall_score": "Rainfall",
        "river_score": "River level",
        "exposure_score": "Exposure",
        "historical_score": "Historical context",
        "temperature_score": "Temperature",
        "heat_index_score": "Heat index",
        "duration_score": "Heat duration",
    }
    components = assessment.get("componentScores") or {}
    calculated = [
        f"Overall risk score: {assessment.get('riskScore')} ({assessment.get('riskLevel')})",
        *calculated_evidence,
    ]
    calculated.extend(
        f"{component_labels.get(name, name)}: +{score} points"
        for name, score in components.items()
        if score > 0
    )
    calculated.append(
        f"Confidence: {assessment.get('confidence')} — {assessment.get('uncertainty')}",
    )
    comparison = assessment.get("simulationComparison") or {}
    for change in comparison.get("changes", []):
        calculated.append(
            f"Simulation change in {change['label']}: {change['baseline']} to {change['current']} {change['unit']} ({change['delta']:+g} {change['unit']})."
        )
    interpreted = [
        f"Risk category: {assessment.get('riskCategory')}",
        "Contributing factors: "
        + (", ".join(f["label"] for f in factors) if factors else "None above threshold."),
    ]
    if historical_evidence:
        interpreted.append("Historical context: " + "; ".join(historical_evidence))
    lake_monitoring = (assessment.get("riskDimensions") or {}).get("glacialLakeMonitoring")
    if lake_monitoring:
        status = lake_monitoring.get("status", "unavailable")
        interpreted.append(
            f"Glacial lake monitoring: {status}. Monitoring status alone does not establish an imminent GLOF."
        )
    if question:
        normalized_question = question.lower()
        if intent == "areas" or "area" in normalized_question or "affected" in normalized_question:
            interpreted.append("Affected-area boundaries are unavailable; this is a location-level assessment.")
        if intent == "missing" or "missing" in normalized_question or "unavailable" in normalized_question:
            interpreted.append(
                "Unavailable fields: " + (", ".join(unavailable) if unavailable else "none marked unavailable in this assessment.")
            )
        if (intent == "changes" or "change" in normalized_question) and not comparison.get("changes"):
            interpreted.append("A baseline comparison is unavailable for this assessment.")
        if intent == "what_if" or (
            "rain" in normalized_question
            and any(term in normalized_question for term in ("increase", "higher", "what if"))
        ):
            if comparison.get("changes"):
                interpreted.append("This is the supplied simulation comparison, not a forecast of future conditions.")
            else:
                interpreted.append(
                    "A rainfall increase has not been scored. Adjust rainfall in Simulation Mode to calculate a scenario; no outcome is predicted here."
                )
        if intent == "factors" or "factor" in normalized_question or "contribut" in normalized_question:
            interpreted.append(
                "Largest calculated contributors: "
                + (
                    ", ".join(
                        f"{component_labels.get(name, name)} (+{score})"
                        for name, score in sorted(components.items(), key=lambda item: item[1], reverse=True)
                        if score > 0
                    )
                    or "none"
                )
                + "."
            )
        if intent == "preparedness" or "what should" in normalized_question or "consider doing" in normalized_question:
            interpreted.append("Preparedness information is general, not an official instruction.")

    return {
        "summary": f"{assessment['location']['name']} is assessed as {assessment.get('riskLevel', 'unknown')} based on supplied data and HeatShield calculations.",
        "sections": {
            "observed": observed_lines,
            "calculated": calculated,
            "interpreted": interpreted,
            "recommended": assessment.get("recommendedActions") or [],
        },
        "disclaimer": DISCLAIMER,
        "source": "deterministic_fallback",
    }


async def explain_risk(
    assessment: dict[str, Any],
    *,
    question: str | None = None,
    api_key: str = "",
    model: str = "openai/gpt-4o-mini",
    fallback_models: list[str] | None = None,
    base_url: str = "https://openrouter.ai/api/v1",
    agent_enabled: bool = False,
    risk_service: Any | None = None,
    rainfall_provider: Any | None = None,
) -> dict[str, Any]:
    if agent_enabled and api_key and risk_service is not None and rainfall_provider is not None:
        try:
            from heatshield_ai.strands_agent import run_strands_workflow

            intent, tool_results = await asyncio.to_thread(
                run_strands_workflow,
                question=question or "Why is this risk level assigned?",
                location_id=assessment["location"]["id"],
                risk_service=risk_service,
                rainfall_provider=rainfall_provider,
                api_key=api_key,
                model_ids=_ordered_model_ids(model, fallback_models),
                base_url=base_url,
            )
            response = fallback_explanation(assessment, question, intent=intent)
            _include_strands_tool_results(response, tool_results)
            response["source"] = "strands_tools_deterministic_response"
            return response
        except Exception:
            response = fallback_explanation(assessment, question)
            response["source"] = "deterministic_fallback_after_strands_error"
            return response

    if not api_key:
        return fallback_explanation(assessment, question)

    user_payload = {
        "task": "classify_question_intent",
        "question": question or "Why is this risk level assigned?",
        "assessment": assessment,
    }
    for model_id in _ordered_model_ids(model, fallback_models):
        try:
            raw = await chat_completion(
                api_key=api_key,
                model=model_id,
                base_url=base_url,
                system=INTENT_PROMPT,
                user=json.dumps(user_payload, indent=2),
            )
            parsed = parse_explanation_json(raw)
            intent = parsed.get("intent")
            if intent not in SUPPORTED_INTENTS:
                raise ValueError("Model returned an unsupported intent")
            response = fallback_explanation(assessment, question, intent=intent)
            response["source"] = "openrouter_intent_structured_response"
            return response
        except Exception:
            continue

    if api_key:
        fb = fallback_explanation(assessment, question)
        fb["source"] = "deterministic_fallback_after_error"
        return fb
    return fallback_explanation(assessment, question)


async def chat_about_risk(
    assessment: dict[str, Any],
    message: str,
    *,
    api_key: str = "",
    model: str = "openai/gpt-4o-mini",
    fallback_models: list[str] | None = None,
    base_url: str = "https://openrouter.ai/api/v1",
    agent_enabled: bool = False,
    risk_service: Any | None = None,
    rainfall_provider: Any | None = None,
) -> dict[str, Any]:
    result = await explain_risk(
        assessment,
        question=message,
        api_key=api_key,
        model=model,
        fallback_models=fallback_models,
        base_url=base_url,
        agent_enabled=agent_enabled,
        risk_service=risk_service,
        rainfall_provider=rainfall_provider,
    )
    result["answer"] = result.get("summary", "")
    return result


def _ordered_model_ids(model: str, fallback_models: list[str] | None) -> list[str]:
    candidates = [model, *(fallback_models or [])]
    return list(dict.fromkeys(candidate.strip() for candidate in candidates if candidate.strip()))


def _include_strands_tool_results(
    response: dict[str, Any],
    tool_results: dict[str, Any],
) -> None:
    sections = response["sections"]
    evidence = tool_results.get("get_environmental_evidence")
    if isinstance(evidence, dict):
        for item in evidence.get("measurements", []):
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            rendered = (
                "unavailable"
                if value is None
                else f"{value} {item.get('unit') or ''}".strip()
            )
            data_type = item.get("data_type") or "type unavailable"
            source = item.get("source") or "source unavailable"
            timestamp = item.get("forecast_time") or item.get("observation_time")
            time_label = (
                "forecast time"
                if item.get("data_type") == "FORECAST"
                else "observation time"
            )
            time_detail = f"; {time_label}: {timestamp}" if timestamp else ""
            sections["observed"].append(
                f"{item.get('name', 'Environmental evidence')}: {rendered} "
                f"({data_type}; {source}{time_detail}; status: {item.get('status', 'unavailable')})."
            )
        for name, provider in (evidence.get("providers") or {}).items():
            if isinstance(provider, dict) and provider.get("status") == "unavailable":
                sections["observed"].append(
                    f"{name} environmental evidence is unavailable; no measurement is reported."
                )

    quality = tool_results.get("get_data_quality")
    if isinstance(quality, dict) and quality.get("status") == "available":
        for field, label in (
            ("missing_data", "Missing evidence"),
            ("stale_data", "Stale evidence"),
            ("unavailable_providers", "Unavailable providers"),
        ):
            values = quality.get(field)
            if values:
                sections["interpreted"].append(f"{label}: {', '.join(values)}.")

    simulation = tool_results.get("get_simulation_result")
    if isinstance(simulation, dict) and simulation.get("status") == "available":
        change = simulation.get("score_delta")
        baseline = simulation.get("baseline_assessment", {})
        simulated = simulation.get("simulated_assessment", {})
        sections["calculated"].append(
            f"Simulation result from the risk engine: score {baseline.get('overall_risk_score')} "
            f"({baseline.get('overall_risk_level')}) to {simulated.get('overall_risk_score')} "
            f"({simulated.get('overall_risk_level')}); change {change:+g}."
        )
        for item in simulation.get("changed_variables", []):
            sections["calculated"].append(
                f"{item['label']}: {item['baseline']} to {item['current']} {item['unit']} "
                f"({item['delta']:+g} {item['unit']})."
            )
