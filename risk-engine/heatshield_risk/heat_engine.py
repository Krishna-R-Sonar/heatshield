from __future__ import annotations

from heatshield_risk.config import RiskThresholds
from heatshield_risk.heat_utils import approximate_heat_index_c
from heatshield_risk.models import ContributingFactor, HeatInputs, ObservationEntry


def _tier_score(value: float, moderate: float, high: float, critical: float, cap: float) -> float:
    if value >= critical:
        return cap
    if value >= high:
        return cap * 0.72
    if value >= moderate:
        return cap * 0.44
    return cap * 0.12


def _severity_from_score(score: float, cap: float) -> str:
    if score >= cap * 0.65:
        return "high"
    if score >= cap * 0.35:
        return "moderate"
    return "low"


def assess_heat(
    inputs: HeatInputs,
    thresholds: RiskThresholds | None = None,
) -> tuple[float, dict[str, float], list[ContributingFactor], list[ObservationEntry], float, str, float | None]:
    th = thresholds or RiskThresholds.load()
    cfg = th.heat
    cap = float(cfg["component_max"])

    temp_score = 0.0
    if inputs.temperature_c is not None:
        temp_score = _tier_score(
            inputs.temperature_c,
            cfg["temperature_c"]["moderate"],
            cfg["temperature_c"]["high"],
            cfg["temperature_c"]["critical"],
            cap,
        )

    heat_index = inputs.heat_index_c
    hi_calculated = False
    if heat_index is None and inputs.temperature_c is not None and inputs.humidity_pct is not None:
        heat_index = approximate_heat_index_c(inputs.temperature_c, inputs.humidity_pct)
        hi_calculated = True

    hi_score = 0.0
    if heat_index is not None:
        hi_score = _tier_score(
            heat_index,
            cfg["heat_index_c"]["moderate"],
            cfg["heat_index_c"]["high"],
            cfg["heat_index_c"]["critical"],
            cap,
        )

    duration_score = 0.0
    if inputs.elevated_temp_duration_hours is not None:
        duration_score = _tier_score(
            inputs.elevated_temp_duration_hours,
            cfg["duration_hours"]["moderate"],
            cfg["duration_hours"]["high"],
            cfg["duration_hours"]["critical"],
            cap,
        )

    if inputs.population_exposure_index is None or inputs.infrastructure_exposure_index is None:
        exposure_score = 0.0
    else:
        blend = 0.6 * inputs.population_exposure_index + 0.4 * inputs.infrastructure_exposure_index
        exposure_score = min(cap, blend * cap)

    total = min(100.0, temp_score + hi_score + duration_score + exposure_score)
    components = {
        "temperature_score": round(temp_score, 2),
        "heat_index_score": round(hi_score, 2),
        "duration_score": round(duration_score, 2),
        "exposure_score": round(exposure_score, 2),
    }

    factors: list[ContributingFactor] = []
    if temp_score > 0:
        factors.append(
            ContributingFactor(
                id="temperature",
                label="Elevated temperature",
                severity=_severity_from_score(temp_score, cap),  # type: ignore[arg-type]
                kind="observed",
                detail="Air temperature exceeds configured heat thresholds.",
                score_contribution=temp_score,
            )
        )
    if hi_score > 0:
        factors.append(
            ContributingFactor(
                id="heat_index",
                label="High apparent temperature (heat index)",
                severity=_severity_from_score(hi_score, cap),  # type: ignore[arg-type]
                kind="calculated" if hi_calculated else "observed",
                detail="Heat index reflects combined temperature and humidity stress.",
                score_contribution=hi_score,
            )
        )
    if duration_score > 0:
        factors.append(
            ContributingFactor(
                id="duration",
                label="Prolonged heat exposure",
                severity=_severity_from_score(duration_score, cap),  # type: ignore[arg-type]
                kind="observed",
                detail="Duration of elevated temperatures increases heat-health risk.",
                score_contribution=duration_score,
            )
        )
    if exposure_score > 0:
        factors.append(
            ContributingFactor(
                id="exposure",
                label="Population exposure",
                severity=_severity_from_score(exposure_score, cap),  # type: ignore[arg-type]
                kind="calculated",
                detail="Demo exposure indices suggest elevated population vulnerability.",
                score_contribution=exposure_score,
            )
        )

    observations: list[ObservationEntry] = []
    if inputs.temperature_c is not None:
        observations.append(
            ObservationEntry(
                "Temperature",
                f"{inputs.temperature_c} °C ({inputs.data_provenance})",
                "observed",
            )
        )
    else:
        observations.append(ObservationEntry("Temperature", "Data unavailable.", "unavailable"))
    if inputs.humidity_pct is not None:
        observations.append(
            ObservationEntry("Humidity", f"{inputs.humidity_pct}% ({inputs.data_provenance})", "observed")
        )
    else:
        observations.append(ObservationEntry("Humidity", "Data unavailable.", "unavailable"))
    if heat_index is not None:
        observations.append(
            ObservationEntry(
                "Heat index",
                f"{heat_index} °C ({'calculated' if hi_calculated else inputs.data_provenance})",
                "calculated" if hi_calculated else "observed",
            )
        )
    else:
        observations.append(ObservationEntry("Heat index", "Data unavailable.", "unavailable"))
    if inputs.elevated_temp_duration_hours is not None:
        observations.append(
            ObservationEntry(
                "Duration above threshold",
                f"{inputs.elevated_temp_duration_hours} hours ({inputs.data_provenance})",
                "observed",
            )
        )
    else:
        observations.append(ObservationEntry("Duration above threshold", "Data unavailable.", "unavailable"))
    for label, value in [
        ("Population exposure index", inputs.population_exposure_index),
        ("Infrastructure exposure index", inputs.infrastructure_exposure_index),
    ]:
        observations.append(
            ObservationEntry(
                label,
                f"{value} (DEMO index)" if value is not None else "Data unavailable.",
                "calculated" if value is not None else "unavailable",
            )
        )

    missing = sum(
        1
        for value in [
            inputs.temperature_c,
            inputs.humidity_pct,
            inputs.population_exposure_index,
            inputs.infrastructure_exposure_index,
        ]
        if value is None
    )
    confidence = max(0.4, 0.88 - missing * 0.22)
    uncertainty = (
        "Moderate uncertainty: incomplete heat observations."
        if missing
        else "Lower uncertainty: core heat inputs present (demo dataset)."
    )

    return total, components, factors, observations, confidence, uncertainty, heat_index
