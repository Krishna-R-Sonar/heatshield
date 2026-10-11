from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strands import tool

from app.services.risk_service import RiskService


def _engine_metadata() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    package_file = root / "risk-engine" / "pyproject.toml"
    thresholds_file = root / "risk-engine" / "heatshield_risk" / "thresholds.yaml"
    package_version = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in package_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("version =")
    )
    thresholds_hash = hashlib.sha256(thresholds_file.read_bytes()).hexdigest()
    return {
        "engine_version": package_version,
        "thresholds_version": f"sha256:{thresholds_hash}",
    }


def _risk_summary(assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        "location": assessment["location"],
        "scenario": assessment["scenario"],
        "overall_risk_score": assessment["riskScore"],
        "overall_risk_level": assessment["riskLevel"],
        "flood_risk": assessment.get("riskDimensions", {}).get("overallFloodRisk"),
        "heat_risk": assessment.get("riskDimensions", {}).get("overallHeatRisk"),
        "risk_components": assessment.get("componentScores", {}),
        "contributing_factors": assessment.get("contributingFactors", []),
        "data_provenance": assessment.get("dataProvenance"),
        "simulation_active": assessment.get("simulationActive", False),
        **_engine_metadata(),
    }


def _normalize_environmental_evidence(
    assessment: dict[str, Any],
    rainfall_provider: Any,
) -> dict[str, Any]:
    providers = dict(assessment.get("providerObservations") or {})
    location_id = assessment["location"]["id"]
    try:
        providers["rainfall"] = rainfall_provider.get_rainfall_observations(location_id)
    except Exception as exc:
        providers["rainfall"] = {
            "source": None,
            "timestamp": None,
            "status": "unavailable",
            "dataQuality": f"Rainfall provider failed: {type(exc).__name__}.",
            "location": {"id": location_id},
            "values": {},
        }

    measurements: list[dict[str, Any]] = []
    for provider_name, provider in providers.items():
        provenance = provider.get("provenance") or {}
        for name, value in (provider.get("values") or {}).items():
            measurement_provenance = value.get("provenance") or provenance
            data_type = (
                measurement_provenance.get("data_type")
                or value.get("dataType")
                or provider.get("dataType")
                or provider.get("dataProvenance")
            )
            if isinstance(data_type, str):
                data_type = data_type.upper()
                if data_type == "LIVE":
                    data_type = "OBSERVATION"
            measurements.append(
                {
                    "name": name,
                    "provider": provider_name,
                    "source": measurement_provenance.get("source", provider.get("source")),
                    "source_url": measurement_provenance.get("source_url", provider.get("sourceUrl")),
                    "location_id": measurement_provenance.get(
                        "location_id", provider.get("location", {}).get("id", location_id)
                    ),
                    "latitude": measurement_provenance.get(
                        "latitude", provider.get("location", {}).get("lat")
                    ),
                    "longitude": measurement_provenance.get(
                        "longitude", provider.get("location", {}).get("lng")
                    ),
                    "data_type": data_type,
                    "observation_time": measurement_provenance.get("observation_time"),
                    "forecast_time": measurement_provenance.get(
                        "forecast_time", value.get("validTime")
                    )
                    if data_type == "FORECAST"
                    else None,
                    "retrieval_time": measurement_provenance.get(
                        "retrieval_time", provider.get("retrievedAt", provider.get("timestamp"))
                    ),
                    "value": value.get("value"),
                    "unit": value.get("unit"),
                    "quality": measurement_provenance.get(
                        "quality", provider.get("dataQuality")
                    ),
                    "status": value.get("status", provider.get("status")),
                }
            )
    return {
        "location": assessment["location"],
        "measurements": measurements,
        "providers": {
            name: {
                "source": result.get("source"),
                "status": result.get("status"),
                "quality": result.get("dataQuality"),
            }
            for name, result in providers.items()
        },
    }


def build_heatshield_tools(
    risk_service: RiskService,
    rainfall_provider: Any,
) -> tuple[list[Any], dict[str, Any]]:
    """Build the only tools available to the HeatShield Copilot."""
    results: dict[str, Any] = {}

    @tool
    def get_risk_assessment(location_id: str) -> dict[str, Any]:
        """Retrieve HeatShield's deterministic assessment and engine metadata for a location."""
        try:
            result = _risk_summary(risk_service.assess(location_id))
        except KeyError:
            result = {"status": "unavailable", "reason": "Unknown location"}
        results["get_risk_assessment"] = result
        return result

    @tool
    def get_environmental_evidence(location_id: str) -> dict[str, Any]:
        """Retrieve structured provider evidence and provenance without changing risk scoring."""
        try:
            assessment = risk_service.assess(location_id)
        except KeyError:
            result = {"status": "unavailable", "reason": "Unknown location"}
        else:
            result = _normalize_environmental_evidence(assessment, rainfall_provider)
        results["get_environmental_evidence"] = result
        return result

    @tool
    def get_simulation_result(
        location_id: str,
        simulation_parameters: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Compare an active or supplied rainfall/river simulation using the existing risk service."""
        try:
            baseline, simulated = risk_service.assess_simulation(
                location_id,
                simulation_parameters,
            )
        except (KeyError, ValueError) as exc:
            result = {"status": "unavailable", "reason": str(exc)}
        else:
            result = {
                "status": "available",
                "location": simulated["location"],
                "baseline_assessment": _risk_summary(baseline),
                "simulated_assessment": _risk_summary(simulated),
                "changed_variables": simulated.get("simulationComparison", {}).get("changes", []),
                "score_delta": round(simulated["riskScore"] - baseline["riskScore"], 2),
                "risk_level_delta": {
                    "baseline": baseline["riskLevel"],
                    "simulated": simulated["riskLevel"],
                    "changed": baseline["riskLevel"] != simulated["riskLevel"],
                },
                "contributing_factors": simulated.get("contributingFactors", []),
            }
        results["get_simulation_result"] = result
        return result

    @tool
    def get_data_quality(location_id: str) -> dict[str, Any]:
        """Report missing, stale, unavailable, and forecast-versus-observation evidence."""
        try:
            assessment = risk_service.assess(location_id)
        except KeyError:
            result = {"status": "unavailable", "reason": "Unknown location"}
        else:
            evidence = _normalize_environmental_evidence(assessment, rainfall_provider)
            measurements = evidence["measurements"]
            missing = [item["name"] for item in measurements if item["value"] is None]
            unavailable = [
                name
                for name, provider in evidence["providers"].items()
                if provider["status"] == "unavailable"
            ]
            stale_providers = {
                name
                for name, provider in evidence["providers"].items()
                if "stale" in str(provider.get("quality") or "").lower()
            }
            stale = [
                item["name"]
                for item in measurements
                if item["provider"] in stale_providers
            ]
            stale.extend(
                name
                for name in stale_providers
                if not any(item["provider"] == name for item in measurements)
            )
            now = datetime.now(timezone.utc)
            for item in measurements:
                timestamp = item.get("forecast_time") or item.get("observation_time")
                if not timestamp:
                    continue
                try:
                    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                if parsed.tzinfo is not None and item.get("data_type") == "FORECAST":
                    valid_seconds = {
                        "rainfallIntensityMmHr": 3600,
                        "cumulativeRainfallMm24h": 24 * 3600,
                    }.get(item["name"])
                    if (
                        valid_seconds is not None
                        and parsed < now
                        and (now - parsed).total_seconds() > valid_seconds
                    ):
                        stale.append(item["name"])
            result = {
                "status": "available",
                "location": evidence["location"],
                "missing_data": missing,
                "stale_data": stale,
                "unavailable_providers": unavailable,
                "provider_status": evidence["providers"],
                "data_types": sorted(
                    {item["data_type"] for item in measurements if item.get("data_type")}
                ),
                "limitations": [
                    item["quality"]
                    for item in measurements
                    if item.get("quality")
                ],
            }
        results["get_data_quality"] = result
        return result

    return [
        get_risk_assessment,
        get_environmental_evidence,
        get_simulation_result,
        get_data_quality,
    ], results
