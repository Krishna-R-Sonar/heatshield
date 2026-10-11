from __future__ import annotations

from datetime import datetime, timezone

from heatshield_risk.config import RiskThresholds
from heatshield_risk.flood_engine import assess_flood
from heatshield_risk.heat_engine import assess_heat
from heatshield_risk.models import (
    FloodInputs,
    HeatInputs,
    LocationInfo,
    RiskAssessmentResult,
    RiskLevel,
    Scenario,
    DataSourceRef,
)


def _level_enum(name: str) -> RiskLevel:
    return RiskLevel(name)


def _flood_actions(level: RiskLevel) -> list[str]:
    base = [
        "Monitor local official warnings (IMD, state disaster management).",
        "Avoid vulnerable low-lying areas if heavy rain continues.",
        "Prepare emergency kit and identify local evacuation routes from official sources.",
    ]
    if level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        base.insert(0, "Increase monitoring frequency; verify river and landslide advisories locally.")
    return base


def _heat_actions(level: RiskLevel) -> list[str]:
    base = [
        "Follow national/state heat-health advisories when issued.",
        "Stay hydrated; limit outdoor exertion during peak afternoon hours.",
        "Check on elderly neighbors and outdoor workers.",
    ]
    if level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        base.insert(0, "Use cooling centers where available; recognize heat exhaustion symptoms.")
    return base


def assess_location(
    location: LocationInfo,
    scenario: Scenario,
    flood: FloodInputs | None,
    heat: HeatInputs | None,
    *,
    thresholds: RiskThresholds | None = None,
    simulation_active: bool = False,
    timestamp: str | None = None,
) -> RiskAssessmentResult:
    th = thresholds or RiskThresholds.load()
    ts = timestamp or datetime.now(timezone.utc).isoformat()

    demo_sources = [
        DataSourceRef("HeatShield Demo Dataset", "Synthetic values for hackathon MVP — not live measurements."),
        DataSourceRef(
            "Planned: IMD / CWC / NHP",
            "Future integration for rainfall, heat, and river levels.",
        ),
    ]

    if scenario == "flood_glof":
        if flood is None:
            raise ValueError("flood inputs required for flood_glof scenario")
        total, components, factors, observations, confidence, uncertainty = assess_flood(
            location.name, location.state, location.lat, location.lng, flood, th
        )
        level_name = th.score_to_level(total)
        level = _level_enum(level_name)
        category = "Flood risk (multi-causal assessment)"
        actions = _flood_actions(level)
    else:
        if heat is None:
            raise ValueError("heat inputs required for extreme_heat scenario")
        total, components, factors, observations, confidence, uncertainty, _ = assess_heat(heat, th)
        level_name = th.score_to_level(total)
        level = _level_enum(level_name)
        category = "Extreme heat / heat-health stress"
        actions = _heat_actions(level)

    return RiskAssessmentResult(
        location=location,
        scenario=scenario,
        risk_score=total,
        risk_level=level,
        risk_category=category,
        confidence=confidence,
        uncertainty=uncertainty,
        contributing_factors=factors,
        observations=observations,
        recommended_actions=actions,
        data_sources=demo_sources,
        timestamp=ts,
        simulation_active=simulation_active,
        component_scores=components,
    )
