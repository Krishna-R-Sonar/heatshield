from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from strands import tool
from heatshield_ai.strands_agent import is_map_point_interpretation_safe
from app.models.weather_evidence import HistoricalAnomalyEvidence


DISCLAIMER = (
    "HeatShield evidence is decision support only, not an official warning or disaster prediction. "
    "Forecast and reanalysis values are model data, not station observations."
)
INACCURATE_ERA5_BASELINE_WORDING = re.compile(
    r"\bhistorical\s+mean\s+(?:for|on)\s+(?:(?:this|the)\s+)?"
    r"(?:exact\s+)?(?:forecast\s+)?date\b",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)


def _available_measurement(forecast: dict[str, Any], name: str, unit: str) -> dict[str, Any] | None:
    measurement = forecast.get(name)
    if not isinstance(measurement, dict):
        return None
    value = measurement.get("value")
    if (
        measurement.get("status") != "AVAILABLE"
        or measurement.get("data_type") != "FORECAST"
        or measurement.get("unit") != unit
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        return None
    return measurement


def _daily_measurements(
    weather: dict[str, Any],
    variable: str,
    unit: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    daily = weather.get("daily") or {}
    values = daily.get("values") or {}
    dates = daily.get("times") or []
    measurements = values.get(variable)
    units = daily.get("units") or {}
    provenance = weather.get("provenance") or {}
    if (
        provenance.get("data_type") != "FORECAST"
        or units.get(variable) != unit
        or not isinstance(measurements, list)
        or not isinstance(dates, list)
    ):
        return []
    result = []
    for period, value in zip(dates[:limit], measurements[:limit]):
        if (
            not isinstance(period, str)
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value < 0
        ):
            continue
        result.append({"value": value, "unit": unit, "period": period, "variable": variable})
    return result


def _daily_measurement(
    weather: dict[str, Any],
    variable: str,
) -> dict[str, Any] | None:
    measurements = _daily_measurements(weather, variable, "mm", limit=1)
    return measurements[0] if measurements else None


def _calendar_month_name(date_value: str) -> str:
    try:
        month = datetime.strptime(date_value[:10], "%Y-%m-%d").month
    except (TypeError, ValueError):
        return "same-calendar-month"
    return (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )[month - 1]


def _display_number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return "unavailable"
    if value == 0:
        return "0"
    for precision in (1, 2, 3, 4, 6):
        formatted = f"{value:.{precision}f}"
        if float(formatted) != 0:
            return formatted.rstrip("0").rstrip(".")
    return "<0.000001" if value > 0 else "-<0.000001"


def _display_date(date_value: Any) -> str:
    if not isinstance(date_value, str):
        return "an unavailable date"
    try:
        parsed = datetime.strptime(date_value[:10], "%Y-%m-%d")
    except ValueError:
        return date_value
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def _display_datetime(datetime_value: Any) -> str:
    if not isinstance(datetime_value, str):
        return "an unavailable time"
    try:
        parsed = datetime.fromisoformat(datetime_value.replace("Z", "+00:00"))
    except ValueError:
        return datetime_value
    offset = parsed.strftime("%z")
    timezone_label = f" (UTC{offset[:3]}:{offset[3:]})" if offset else " local time"
    return f"{_display_date(datetime_value)} at {parsed.strftime('%I:%M %p').lstrip('0')}{timezone_label}"


def _plain_missing_evidence(items: list[str]) -> list[str]:
    labels = {
        "Temperature forecast": "temperature forecast",
        "Apparent temperature forecast": "feels-like temperature",
        "Relative-humidity forecast": "humidity",
        "Wet-bulb temperature forecast": "wet-bulb temperature",
        "Selected-hour precipitation accumulation": "precipitation for the selected hour",
        "Selected-hour precipitation probability": "precipitation chance for the selected hour",
        "Daily total precipitation forecast for the first forecast date": "daily total precipitation",
        "Daily rain-only forecast for the first forecast date": "daily rainfall",
        "GloFAS modeled river-discharge evidence": "verified local river-level information",
        "Weather ensemble spread": "how much the weather forecasts differ",
        "Temperature spread": "how much the temperature forecasts differ",
        "Precipitation spread": "how much the precipitation forecasts differ",
        "ERA5 historical baseline": "historical weather comparison",
    }
    result = []
    for item in dict.fromkeys(items):
        if item in labels:
            result.append(labels[item])
        elif item.startswith("ERA5 comparison for "):
            result.append("historical comparison for the forecast period")
        else:
            result.append(item)
    return result


def _prioritize_missing_evidence(items: list[str], question: str) -> list[str]:
    query = question.casefold()
    unique_items = list(dict.fromkeys(items))
    if "drought" in query or "water scarcity" in query:
        priority = ("long-term rainfall-deficit", "reservoir", "groundwater")
    elif "waterlogging" in query or "water logging" in query:
        priority = ("waterlogging", "drainage", "historical")
    elif any(word in query for word in ("glof", "glacial lake", "glacier lake")):
        priority = ("glacial-lake", "downstream", "catchment")
    elif any(word in query for word in ("official", "warning", "advisory", "alert")):
        priority = ("official", "advisory", "warning")
    elif any(word in query for word in ("local flood", "river level", "flood threshold")):
        priority = ("local river", "gauge", "threshold")
    elif any(word in query for word in ("heat", "temperature", "hot", "warm")):
        priority = ("temperature", "apparent", "wet-bulb", "humidity", "duration")
    elif any(word in query for word in ("rain", "water", "flood", "river", "precipitation", "monsoon")):
        priority = ("rain-only", "daily rain", "precipitation", "river", "glofas")
    else:
        priority = ("temperature", "daily rain", "precipitation", "river", "glofas")
    return sorted(
        unique_items,
        key=lambda item: next(
            (index for index, keyword in enumerate(priority) if keyword in item.casefold()),
            len(priority),
        ),
    )


def _format_copilot_interpretation(text: str) -> str:
    iso_datetime = re.compile(
        r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?\b"
    )
    iso_date = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
    decimal = re.compile(r"(?<![\w.])-?\d+\.\d+(?![\w.])")
    text = iso_datetime.sub(lambda match: _display_datetime(match.group()), text)
    text = iso_date.sub(lambda match: _display_date(match.group()), text)
    return decimal.sub(
        lambda match: _display_number(float(match.group())),
        text,
    )


def _daily_temperature_measurements(
    weather: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    daily = weather.get("daily") or {}
    values = daily.get("values") or {}
    dates = daily.get("times") or []
    temperatures = values.get("temperature_2m_max")
    units = daily.get("units") or {}
    provenance = weather.get("provenance") or {}
    if (
        provenance.get("data_type") != "FORECAST"
        or units.get("temperature_2m_max") != "°C"
        or not isinstance(temperatures, list)
        or not isinstance(dates, list)
    ):
        return []
    return [
        {"value": value, "period": period}
        for period, value in zip(dates[:limit], temperatures[:limit])
        if (
            isinstance(period, str)
            and not isinstance(value, bool)
            and isinstance(value, (int, float))
            and isfinite(value)
        )
    ]


def _fallback_evidence_interpretation(
    *,
    question: str,
    intent: str,
    temperature: dict[str, Any] | None,
    daily_temperature_outlook: list[dict[str, Any]],
    daily_rain_outlook: list[dict[str, Any]],
    daily_total_outlook: list[dict[str, Any]],
    daily_uv_outlook: list[dict[str, Any]],
    uv_index: dict[str, Any] | None,
    available_anomalies: list[dict[str, Any]],
    baseline_period: str | None,
    missing: list[str],
    river: dict[str, Any],
    weather_source: str,
    weather_retrieved_at: str | None,
    weather_status: str,
) -> tuple[str, list[str], list[str]]:
    query = question.casefold()
    asks_heat = not any(
        word in query for word in ("historical", "history", "usual", "average", "unusual", "compare")
    ) and any(
        word in query for word in ("heat", "temperature", "hot", "warm", "feels like")
    )
    asks_water = not any(
        word in query for word in ("historical", "history", "usual", "average", "unusual", "compare")
    ) and (
        any(word in query for word in ("rain", "water", "flood", "river", "precipitation", "monsoon"))
        or ("prepare" in query and bool(daily_rain_outlook))
    )
    asks_history = any(
        word in query for word in ("historical", "history", "usual", "average", "unusual", "compare")
    )
    asks_uv = any(word in query for word in ("uv", "sun index", "ultraviolet"))
    asks_source = any(word in query for word in ("source", "retrieved", "provider"))
    asks_missing = intent == "missing" or any(
        word in query for word in ("missing", "unavailable", "data quality", "quality", "reliable", "stale")
    )
    highlights: list[str] = []
    meaning: list[str] = []
    next_steps: list[str] = []

    if asks_source:
        source_status = {
            "AVAILABLE": "returned",
            "DEGRADED": "incomplete",
            "UNAVAILABLE": "unavailable",
        }.get(weather_status, "status unknown")
        highlights.append(
            f"{weather_source} supplied the weather forecast; it was {source_status} when retrieved"
            + (
                f" on {_display_datetime(weather_retrieved_at)}."
                if weather_retrieved_at
                else "."
            )
        )
        if weather_status != "AVAILABLE":
            meaning.append("Some weather fields may be missing; see the detailed evidence for provider limitations.")
    elif asks_missing:
        relevant_missing = missing
        if asks_heat:
            relevant_missing = [
                item for item in missing
                if any(word in item.lower() for word in ("temperature", "weather", "heat", "exposure", "duration"))
            ]
        elif asks_water:
            relevant_missing = [
                item for item in missing
                if any(word in item.lower() for word in (
                    "rain",
                    "precipitation",
                    "river",
                    "flood",
                    "waterlog",
                    "drought",
                    "glof",
                    "glacial",
                    "advisory",
                    "warning",
                    "gauge",
                    "threshold",
                ))
            ]
        if relevant_missing:
            highlights.append(
                "Not available in this result: "
                + "; ".join(_plain_missing_evidence(
                    _prioritize_missing_evidence(relevant_missing, question)
                )[:2])
                + "."
            )
        else:
            highlights.append("The requested evidence is available in the retrieved result; no missing field was identified for this question.")
        next_steps.append("For a local assessment, compare this forecast with current official updates and verified local observations.")
    elif asks_heat:
        daily_high = max(daily_temperature_outlook, key=lambda item: item["value"], default=None)
        if daily_high:
            date = _display_date(daily_high["period"])
            highlights.append(
                f"The forecast high is {_display_number(daily_high['value'])} °C on {date}."
            )
            matching_anomaly = next(
                (
                    anomaly for anomaly in available_anomalies
                    if anomaly.get("indicator") == "temperature_max"
                    and anomaly.get("date") == daily_high["period"]
                ),
                None,
            )
            if matching_anomaly and asks_history:
                month = _calendar_month_name(matching_anomaly["date"])
                highlights[-1] = (
                    f"The forecast high is {_display_number(daily_high['value'])} °C on {date}, about "
                    f"{_display_number(abs(matching_anomaly['anomaly']))} °C "
                    f"{'above' if matching_anomaly['anomaly'] > 0 else 'below' if matching_anomaly['anomaly'] < 0 else 'equal to'} "
                    f"the historical {month} average."
                )
                meaning.append(
                    f"The comparison uses ERA5 daily values from {baseline_period or 'the available historical period'}; it is historical context, not a probability of harm."
                )
        elif temperature:
            highlights.append(
                f"The selected-hour temperature forecast is {_display_number(temperature['value'])} °C "
                f"for {_display_datetime(temperature.get('forecast_time'))}."
            )
        else:
            highlights.append("A usable temperature forecast is not available for this location.")
        meaning.append(
            "We can show the weather forecast, but we do not yet have enough information to assess local heat risk or exposure."
        )
        if daily_high or temperature:
            next_steps.append(
                "Check the hourly forecast and locally issued heat guidance for the time and place that matter to you."
            )
    elif asks_water:
        peak_rain = max(daily_rain_outlook, key=lambda item: item["value"], default=None)
        if peak_rain:
            date = _display_date(peak_rain["period"])
            highlights.append(
                f"The rain-only forecast is highest at {_display_number(peak_rain['value'])} mm on {date}."
            )
            total = next(
                (item for item in daily_total_outlook if item["period"] == peak_rain["period"]),
                None,
            )
            if total and total["value"] != peak_rain["value"]:
                highlights[-1] += (
                    f" Total precipitation for that day is {_display_number(total['value'])} mm."
                )
        else:
            highlights.append("A daily rainfall forecast is not available for this location.")
            if temperature:
                highlights.append(
                    f"The selected-hour temperature forecast is {_display_number(temperature['value'])} °C "
                    f"for {_display_datetime(temperature.get('forecast_time'))}."
                )
        meaning.append(
            "A rainfall forecast does not show whether a particular street or river will flood; local drainage, exposure, and verified flood thresholds are not assessed."
        )
        if river.get("value") is not None:
            meaning.append(
                f"GloFAS shows modeled river flow of {_display_number(river['value'])} m³/s, but its connection to a river at this location is unverified; it is not a local river-level reading."
            )
        else:
            meaning.append("A verified local river level is not available in this result.")
        if peak_rain:
            next_steps.append(
                "Check the latest local forecast and official local flood information; use a verified nearby gauge for river conditions."
            )
    elif asks_uv:
        peak_uv = max(daily_uv_outlook, key=lambda item: item["value"], default=None)
        if peak_uv:
            highlights.append(
                f"The daily high UV index forecast is {_display_number(peak_uv['value'])} on "
                f"{_display_date(peak_uv['period'])}."
            )
        elif uv_index:
            highlights.append(
                f"The selected-hour UV index forecast is {_display_number(uv_index['value'])} for "
                f"{_display_datetime(uv_index.get('forecast_time'))}."
            )
        else:
            highlights.append("A UV index forecast is not available for this location.")
        next_steps.append("Check locally applicable UV guidance for the forecast period.")
    elif asks_history:
        preferred_indicator = (
            "temperature_max"
            if any(word in query for word in ("heat", "temperature", "hot"))
            else "rainfall"
            if "rain" in query
            else "precipitation"
            if any(word in query for word in ("flood", "water", "precipitation"))
            else None
        )
        historical_anomaly = next(
            (
                item for item in available_anomalies
                if item.get("indicator") == preferred_indicator
            ),
            available_anomalies[0] if available_anomalies else None,
        )
        if historical_anomaly:
            indicator_label = (
                "forecast high"
                if historical_anomaly.get("indicator") == "temperature_max"
                else "rainfall forecast"
                if historical_anomaly.get("indicator") == "rainfall"
                else "precipitation forecast"
            )
            month = _calendar_month_name(historical_anomaly["date"])
            highlights.append(
                f"The {indicator_label} for {_display_date(historical_anomaly['date'])} is "
                f"{_display_number(historical_anomaly['current_value'])} {historical_anomaly['unit']}, "
                f"about {_display_number(abs(historical_anomaly['anomaly']))} "
                f"{historical_anomaly['unit']} "
                f"{'above' if historical_anomaly['anomaly'] > 0 else 'below' if historical_anomaly['anomaly'] < 0 else 'equal to'} "
                f"the historical {month} average "
                f"({_display_number(historical_anomaly['historical_baseline']['mean'])} "
                f"{historical_anomaly['unit']}; ERA5 {baseline_period or 'baseline period'})."
            )
            meaning.append(
                f"Its historical percentile is {_display_number(historical_anomaly['percentile'])}%, a rank among ERA5 {month} days—not a probability of danger."
            )
            if (
                historical_anomaly["anomaly"] < 0 and historical_anomaly["percentile"] > 50
            ) or (
                historical_anomaly["anomaly"] > 0 and historical_anomaly["percentile"] < 50
            ):
                meaning.append("The average and percentile rank describe different aspects of the historical record.")
        else:
            highlights.append("A matching historical comparison is not available for the forecast period.")
    else:
        daily_high = max(daily_temperature_outlook, key=lambda item: item["value"], default=None)
        peak_rain = max(daily_rain_outlook, key=lambda item: item["value"], default=None)
        if daily_high:
            highlights.append(
                f"The forecast high is {_display_number(daily_high['value'])} °C on "
                f"{_display_date(daily_high['period'])}."
            )
        elif temperature:
            highlights.append(
                f"The selected-hour temperature forecast is {_display_number(temperature['value'])} °C "
                f"for {_display_datetime(temperature.get('forecast_time'))}."
            )
        if peak_rain:
            highlights.append(
                f"Rain-only forecast is highest at {_display_number(peak_rain['value'])} mm on "
                f"{_display_date(peak_rain['period'])}."
            )
        if not highlights:
            highlights.append("A usable weather forecast is not available for this location.")
        if daily_rain_outlook:
            meaning.append(
                "Rainfall amounts are forecast evidence, not a local flood assessment or warning."
            )
        if daily_temperature_outlook:
            meaning.append(
                "Heat risk cannot be assessed from temperature alone; local exposure and vulnerability are not included."
            )
        if intent in {"why", "factors"}:
            meaning.append(
                "This forecast describes expected conditions; it does not identify what caused them."
            )

    if not highlights:
        highlights.append("A concise finding cannot be produced from the available evidence.")
    if len(highlights) > 2:
        highlights = highlights[:2]
    return " ".join(highlights), meaning, next_steps


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {"status": "unavailable", "reason": "Provider returned an unsupported response."}


def build_global_evidence_tools(
    *,
    location: dict[str, Any],
    weather_provider: Any,
    flood_service: Any,
    historical_service: Any,
    ensemble_service: Any,
) -> tuple[list[Any], dict[str, Any]]:
    """Create read-only tools scoped to one selected global map location."""
    results: dict[str, Any] = {}
    cache: dict[str, dict[str, Any]] = {}
    in_flight: dict[str, threading.Event] = {}
    lock = threading.Lock()
    latitude = float(location["lat"])
    longitude = float(location["lng"])

    def cached(name: str, operation) -> dict[str, Any]:
        with lock:
            if name in cache:
                return cache[name]
            event = in_flight.get(name)
            if event is None:
                event = threading.Event()
                in_flight[name] = event
                is_owner = True
            else:
                is_owner = False
        if not is_owner:
            event.wait()
            with lock:
                return cache[name]
        try:
            result = _dump(operation())
        except Exception as exc:
            result = {
                "status": "UNAVAILABLE",
                "reason": f"Provider unavailable: {type(exc).__name__}.",
            }
        with lock:
            cache[name] = result
            in_flight.pop(name, None)
            event.set()
            return result

    def weather_result() -> dict[str, Any]:
        def load_weather() -> Any:
            weather = weather_provider.get_weather_evidence_for_location(location)
            try:
                historical = historical_service.calculate(weather)
            except Exception as exc:
                historical = HistoricalAnomalyEvidence(
                    status="UNAVAILABLE",
                    anomalies=[],
                    baseline_period="1991-2020",
                    methodology="ERA5 same-calendar-month baseline; no historical result was returned.",
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    quality=f"Historical provider unavailable: {type(exc).__name__}.",
                    limitations=["Historical provider failure does not invalidate available weather forecasts."],
                )
            return weather.model_copy(update={"historical_anomalies": historical})

        return cached("weather", load_weather)

    @tool
    def get_weather_evidence() -> dict[str, Any]:
        """Retrieve current and forecast weather evidence with units, timestamps, and source provenance."""
        result = weather_result()
        results["get_weather_evidence"] = result
        return result

    @tool
    def get_flood_evidence() -> dict[str, Any]:
        """Retrieve GloFAS modeled river-discharge evidence; values are not local river levels or warnings."""
        result = cached("flood", lambda: flood_service.get_for_location(latitude, longitude))
        results["get_flood_evidence"] = result
        return result

    @tool
    def get_historical_baseline() -> dict[str, Any]:
        """Retrieve ERA5 calendar-month historical anomaly evidence and its baseline method."""
        weather = weather_result()
        historical = weather.get("historical_anomalies")
        result = historical if isinstance(historical, dict) else {
            "status": "UNAVAILABLE",
            "reason": weather.get("reason", "Historical baseline is unavailable."),
        }
        results["get_historical_baseline"] = result
        return result

    @tool
    def get_forecast_uncertainty() -> dict[str, Any]:
        """Retrieve ensemble median, quartiles, range, spread, and display-only uncertainty categories."""
        result = cached("ensemble", lambda: ensemble_service.get_for_location(latitude, longitude))
        results["get_forecast_uncertainty"] = result
        return result

    @tool
    def get_data_provenance() -> dict[str, Any]:
        """Retrieve source, model, data type, timestamps, grid resolution, quality, and limitations."""
        weather = weather_result()
        flood = cached("flood", lambda: flood_service.get_for_location(latitude, longitude))
        ensemble = cached("ensemble", lambda: ensemble_service.get_for_location(latitude, longitude))
        result = {
            "weather": weather.get("provenance"),
            "historical": (weather.get("historical_anomalies") or {}).get("source"),
            "flood": flood.get("provenance"),
            "ensemble": ensemble.get("provenance"),
            "limitations": {
                name: data.get("reason") or data.get("quality")
                for name, data in (
                    ("weather", weather),
                    ("flood", flood),
                    ("ensemble", ensemble),
                )
            },
        }
        results["get_data_provenance"] = result
        return result

    @tool
    def get_climate_projection() -> dict[str, Any]:
        """Report whether a separate long-term climate projection is available; never substitute a weather forecast."""
        result = {
            "status": "UNAVAILABLE",
            "reason": "Climate projections are not integrated in this phase.",
            "data_type": "PROJECTION",
        }
        results["get_climate_projection"] = result
        return result

    return [
        get_weather_evidence,
        get_flood_evidence,
        get_historical_baseline,
        get_forecast_uncertainty,
        get_data_provenance,
        get_climate_projection,
    ], results


def render_global_evidence_answer(
    *,
    location: dict[str, Any],
    tool_results: dict[str, Any],
    question: str,
    intent: str = "other",
    ai_guidance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    weather = tool_results.get("get_weather_evidence") or {}
    flood = tool_results.get("get_flood_evidence") or {}
    uncertainty = tool_results.get("get_forecast_uncertainty") or {}
    historical = tool_results.get("get_historical_baseline") or weather.get("historical_anomalies") or {}

    facts: list[str] = []
    missing: list[str] = []
    weather_forecast = weather.get("forecast") or {}
    temperature = _available_measurement(weather_forecast, "temperature", "°C")
    daily_precipitation = _daily_measurement(weather, "precipitation_sum")
    daily_rainfall = _daily_measurement(weather, "rain_sum")
    daily_rain_outlook = _daily_measurements(
        weather,
        "rain_sum",
        "mm",
        limit=5,
    )
    daily_total_outlook = _daily_measurements(
        weather,
        "precipitation_sum",
        "mm",
        limit=5,
    )
    daily_uv_outlook = _daily_measurements(
        weather,
        "uv_index_max",
        "",
        limit=5,
    )
    daily_temperature_outlook = _daily_temperature_measurements(weather, limit=5)
    uv_index = _available_measurement(weather_forecast, "uv_index", "")
    weather_source = (weather.get("provenance") or {}).get("source") or "Weather provider"
    for key, label, unit in (
        ("temperature", "Temperature forecast", "°C"),
        ("apparent_temperature", "Apparent temperature forecast", "°C"),
        ("humidity", "Relative-humidity forecast", "%"),
        ("wet_bulb_temperature", "Wet-bulb temperature forecast", "°C"),
        ("precipitation", "Selected-hour precipitation accumulation", "mm"),
        ("precipitation_probability", "Selected-hour precipitation probability", "%"),
    ):
        measurement = _available_measurement(weather_forecast, key, unit)
        if measurement is None:
            missing.append(label)
        else:
            facts.append(
                f"{label}: {measurement['value']} {unit} "
                f"(FORECAST; valid {measurement.get('forecast_time') or 'time unavailable'}; "
                f"{weather_source})."
            )
    if daily_precipitation:
        facts.append(
            f"Daily total precipitation forecast: {daily_precipitation['value']} mm "
            f"(FORECAST; local calendar date {daily_precipitation['period'] or 'unavailable'}; "
            f"{weather_source}; may include solid-precipitation water equivalent)."
        )
    else:
        missing.append("Daily total precipitation forecast for the first forecast date")
    if daily_rainfall:
        facts.append(
            f"Daily rain-only forecast: {daily_rainfall['value']} mm "
            f"(FORECAST; local calendar date {daily_rainfall['period'] or 'unavailable'}; "
            f"{weather_source})."
        )
    else:
        missing.append("Daily rain-only forecast for the first forecast date")
    if daily_temperature_outlook:
        peak_temperature = max(daily_temperature_outlook, key=lambda item: item["value"])
        facts.append(
            f"Warmest daily-high forecast: {_display_number(peak_temperature['value'])} °C "
            f"on {_display_date(peak_temperature['period'])} "
            f"(local calendar date; {weather_source})."
        )
    if daily_rain_outlook:
        peak_rain = max(daily_rain_outlook, key=lambda item: item["value"])
        peak_total = next(
            (item for item in daily_total_outlook if item["period"] == peak_rain["period"]),
            None,
        )
        facts.append(
            f"Highest daily rain-only forecast: {_display_number(peak_rain['value'])} mm "
            f"on {_display_date(peak_rain['period'])}"
            + (
                f"; total precipitation that day is {_display_number(peak_total['value'])} mm"
                if peak_total
                else ""
            )
            + f" (local calendar date; {weather_source})."
        )
    if weather.get("status") == "UNAVAILABLE":
        missing.append("Weather forecast")

    forecast_points = [
        point for point in flood.get("daily", [])
        if point.get("data_type") == "FORECAST"
    ]
    river = forecast_points[0].get("discharge", {}) if forecast_points else {}
    river_value = river.get("value")
    if (
        river.get("status") != "AVAILABLE"
        or river.get("data_type") != "FORECAST"
        or river.get("unit") != "m³/s"
        or isinstance(river_value, bool)
        or not isinstance(river_value, (int, float))
        or not isfinite(river_value)
    ):
        river = {}
    if river.get("value") is None:
        missing.append("GloFAS modeled river-discharge evidence")
    else:
        facts.append(
            f"Modeled river-discharge evidence: {river['value']} {river.get('unit') or 'm³/s'} "
            f"(GloFAS forecast for {river.get('reference_time')}; not an actual local river level)."
        )
        p25 = forecast_points[0].get("p25") or {}
        p75 = forecast_points[0].get("p75") or {}
        spread = forecast_points[0].get("discharge_spread_iqr")
        if p25.get("value") is not None and p75.get("value") is not None:
            facts.append(
                f"GloFAS modeled-discharge ensemble P25–P75 range: "
                f"{p25['value']}–{p75['value']} m³/s"
                f"{f' (IQR {spread} m³/s)' if isinstance(spread, (int, float)) else ''}; "
                "member disagreement only, not local river level, flood probability, or safety."
            )

    question_terms = question.casefold()
    if "drought" in question_terms or "water scarcity" in question_terms:
        missing.append(
            "Long-term rainfall-deficit assessment and verified reservoir or groundwater data are not integrated."
        )
    if "waterlogging" in question_terms or "water logging" in question_terms:
        missing.append(
            "Location-specific drainage and verified historical waterlogging data are not integrated."
        )
    if any(term in question_terms for term in ("glof", "glacial lake", "glacier lake")):
        missing.append(
            "Real-time glacial-lake monitoring and verified downstream catchment evidence are not integrated."
        )
    if "official" in question_terms and any(
        term in question_terms for term in ("warning", "advisory", "alert")
    ):
        missing.append(
            "Official heat and flood advisory feeds are not integrated into this evidence result."
        )
    if any(term in question_terms for term in ("local flood", "river level", "flood threshold")):
        missing.append(
            "A verified local river gauge, catchment association, and location-specific flood threshold are not available."
        )

    ensemble_daily = uncertainty.get("daily") or []
    if not ensemble_daily:
        missing.append("Weather ensemble spread")
    else:
        day = ensemble_daily[0]
        for key, label in (("temperature_max", "Temperature spread"), ("precipitation_sum", "Precipitation spread")):
            stat = day.get(key) or {}
            if stat.get("status") == "AVAILABLE":
                facts.append(
                    f"{label} for {day.get('date', 'date unavailable')}: "
                    f"median {stat.get('median')} {stat.get('unit')}; "
                    f"P25–P75 range {stat.get('p25')}–{stat.get('p75')} {stat.get('unit')}; "
                    f"IQR {stat.get('spread_iqr')} {stat.get('unit')}; "
                    f"spread descriptor {stat.get('uncertainty')} (UTC date; model-member disagreement only, "
                    "not disaster probability, impact, or safety)."
                )
            else:
                missing.append(label)

    anomaly_items = historical.get("anomalies") or []
    first_forecast_date = (weather.get("daily") or {}).get("times") or []
    first_forecast_date = first_forecast_date[0] if first_forecast_date else None
    anomaly_labels = {
        "temperature_max": "Daily maximum temperature",
        "precipitation": "Daily total precipitation",
        "rainfall": "Daily rain-only total",
    }
    available_anomalies = []
    for indicator, label in anomaly_labels.items():
        anomaly = next(
            (
                item for item in anomaly_items
                if item.get("indicator") == indicator
                and item.get("date") == first_forecast_date
                and item.get("status") == "AVAILABLE"
                and item.get("anomaly") is not None
                and item.get("current_value") is not None
                and (item.get("historical_baseline") or {}).get("mean") is not None
                and item.get("percentile") is not None
            ),
            None,
        )
        if anomaly:
            available_anomalies.append((label, anomaly))
            direction = "above" if anomaly["anomaly"] > 0 else "below" if anomaly["anomaly"] < 0 else "equal to"
            facts.append(
                f"{label} forecast for {anomaly['date']}: {anomaly['current_value']} {anomaly['unit']}; "
                f"{abs(anomaly['anomaly'])} {anomaly['unit']} {direction} the ERA5 "
                f"{historical.get('baseline_period', '1991-2020')} "
                f"{_calendar_month_name(anomaly['date'])} mean "
                f"({anomaly['historical_baseline']['mean']} {anomaly['unit']}; historical empirical "
                f"midrank percentile {anomaly['percentile']}% among ERA5 "
                f"{_calendar_month_name(anomaly['date'])} daily values; forecast-location calendar date; "
                "REANALYSIS comparison, not a hazard score)."
            )
        elif historical.get("status") != "UNAVAILABLE" and first_forecast_date:
            missing.append(f"ERA5 comparison for {label.lower()} on {first_forecast_date}")
    if historical.get("status") == "UNAVAILABLE":
        missing.append("ERA5 historical baseline")

    has_rain_forecast = bool(
        _available_measurement(weather_forecast, "precipitation", "mm")
        or daily_rain_outlook
        or daily_total_outlook
    )
    evidence_completeness = {
        "heat": {
            "status": "partial_evidence" if temperature else "insufficient_evidence",
            "rationale": (
                "A weather forecast is available, but validated heat-stress duration, exposure, and local official heat criteria are not integrated."
                if temperature
                else "A usable temperature forecast is unavailable."
            ),
        },
        "rainfall": {
            "status": "partial_evidence" if has_rain_forecast else "insufficient_evidence",
            "rationale": (
                "Forecast rainfall evidence is available; it does not establish local flood or waterlogging impacts."
                if has_rain_forecast
                else "Usable precipitation forecast evidence is unavailable."
            ),
        },
        "river_flood": {
            "status": "partial_evidence" if river.get("value") is not None else "insufficient_evidence",
            "rationale": (
                "Modeled discharge is available, but its local river association, gauge observations, catchment relevance, and flood threshold are unverified."
                if river.get("value") is not None
                else "No usable modeled discharge evidence is available; verified local gauges and flood thresholds are not integrated."
            ),
        },
        "glof": {
            "status": "insufficient_evidence",
            "rationale": "Real-time glacial-lake monitoring and downstream catchment evidence are not integrated in this live evidence view.",
        },
        "waterlogging": {
            "status": "insufficient_evidence",
            "rationale": "Verified drainage conditions and historical street-level waterlogging records are not integrated.",
        },
        "drought": {
            "status": "insufficient_evidence",
            "rationale": "No long-window rainfall-deficit assessment or verified reservoir or groundwater evidence is integrated.",
        },
        "official_advisories": {
            "status": "insufficient_evidence",
            "rationale": "Official heat and flood advisory feeds are not integrated; HeatShield does not issue official warnings.",
        },
    }

    is_map_point = location.get("selection_type") == "map_point"
    if is_map_point:
        recommended = [
            "Weather evidence is for the selected coordinates and provider grid cells. No nearby settlement or population-exposure context is verified for this pin."
        ]
    else:
        recommended = []

    resident_recommendations: list[dict[str, str | None]] = []
    planner_recommendations: list[dict[str, str | None]] = []

    if is_map_point:
        resident_recommendations = []
        planner_recommendations = []

    candidate_interpretation = (
        ai_guidance.get("interpretation")
        if isinstance(ai_guidance, dict)
        else None
    )
    if (
        isinstance(candidate_interpretation, str)
        and INACCURATE_ERA5_BASELINE_WORDING.search(candidate_interpretation)
    ):
        logger.warning("Discarding AI guidance with date-specific ERA5 baseline wording")
        ai_guidance = None
        intent = "other"

    ai_response = None
    ai_interpretation_applied = False
    ai_recommendations_applied = False
    environmental_assessment = None
    if ai_guidance:
        candidate_interpretation = ai_guidance.get("interpretation")
        if (
            isinstance(candidate_interpretation, str)
            and (
                not is_map_point
                or is_map_point_interpretation_safe(candidate_interpretation)
            )
        ):
            ai_response = candidate_interpretation or None
            if ai_response:
                ai_response = _format_copilot_interpretation(ai_response)
            ai_interpretation_applied = bool(ai_response)
            structured_assessment = ai_guidance.get("environmental_assessment")
            if ai_interpretation_applied and isinstance(structured_assessment, dict):
                deterministic_missing = _plain_missing_evidence(
                    _prioritize_missing_evidence(missing, question)
                )
                environmental_assessment = {
                    **structured_assessment,
                    "status": "AI_GENERATED",
                    "evidence_completeness": evidence_completeness,
                    "evidence_references": ai_guidance.get("evidence_references", []),
                    "missing_information": list(dict.fromkeys([
                        *structured_assessment.get("missing_information", []),
                        *deterministic_missing,
                    ]))[:5],
                    "limitations": list(dict.fromkeys([
                        *structured_assessment.get("limitations", []),
                        "Weather forecasts and historical reanalysis are not local station observations.",
                        "No validated real-world HeatShield risk score was calculated from this evidence.",
                        "Evidence-completeness labels describe data coverage, not hazard severity or safety.",
                    ]))[:5],
                }
                ai_recommendations_applied = any(
                    structured_assessment.get(name)
                    for name in (
                        "evidence_based_suggestions",
                        "general_preventive_ideas",
                        "community_opportunities",
                    )
                )
    else:
        environmental_assessment = None
    fallback_meaning: list[str] = []
    fallback_next_steps: list[str] = []
    if ai_response is None:
        ai_response, fallback_meaning, fallback_next_steps = _fallback_evidence_interpretation(
            question=question,
            intent=intent,
            temperature=temperature,
            daily_temperature_outlook=daily_temperature_outlook,
            daily_rain_outlook=daily_rain_outlook,
            daily_total_outlook=daily_total_outlook,
            daily_uv_outlook=daily_uv_outlook,
            uv_index=uv_index,
            available_anomalies=[item for _, item in available_anomalies],
            baseline_period=historical.get("baseline_period"),
            missing=list(dict.fromkeys(missing)),
            river=river,
            weather_source=weather_source,
            weather_retrieved_at=(weather.get("provenance") or {}).get("retrieved_at"),
            weather_status=weather.get("status", "UNAVAILABLE"),
        )
        if not is_map_point:
            recommended.extend(fallback_next_steps)

    if not facts:
        summary = f"Real environmental evidence is unavailable for {location.get('name', 'this location')} right now."
    else:
        summary = (
            f"Retrieved {len(facts)} structured forecast, reanalysis, or modeled-evidence findings for "
            f"{location.get('name', 'this location')}. No HeatShield risk score was calculated from this evidence."
        )
    interpreted = [
        *fallback_meaning,
        "Forecasts and reanalysis estimates are not local station observations.",
    ]
    if daily_rain_outlook and not any("flood" in item.lower() for item in fallback_meaning):
        interpreted.append(
            "Rainfall totals alone do not establish whether flooding will occur at a particular location."
        )
    if river.get("value") is not None and not any("GloFAS" in item for item in fallback_meaning):
        interpreted.append(
            "GloFAS river flow is modeled evidence, not a measured local river level; its connection to this location is unverified."
        )
    if is_map_point:
        interpreted.append(
            "This is a coordinate-only map selection. Nearby settlement, population, and neighborhood-exposure data are not available."
        )
    if missing:
        interpreted.append(
            "Unavailable in this result: "
            + ", ".join(
                _plain_missing_evidence(
                    _prioritize_missing_evidence(missing, question)
                )[:3]
            )
            + "."
        )
    if environmental_assessment is None:
        environmental_assessment = {
            "status": "DETERMINISTIC_FALLBACK",
            "evidence_completeness": evidence_completeness,
            "location_summary": ai_response or summary,
            "location_summary_evidence_refs": [],
            "key_findings": [],
            "environmental_concerns": [],
            "evidence_based_suggestions": [],
            "general_preventive_ideas": [],
            "community_opportunities": [],
            "missing_information": _plain_missing_evidence(
                _prioritize_missing_evidence(missing, question)
            )[:5],
            "recommended_next_steps": recommended[:5],
            "limitations": [
                "AI environmental suggestions are unavailable; this section contains only deterministic evidence summaries.",
                "Weather forecasts and historical reanalysis are not local station observations.",
                "No validated real-world HeatShield risk score was calculated from this evidence.",
                "Evidence-completeness labels describe data coverage, not hazard severity or safety.",
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_used": None,
            "evidence_references": [],
        }
    return {
        "summary": summary,
        "sections": {
            "evidence": facts,
            "interpreted": interpreted,
            "recommended": recommended,
            "recommendations": {
                "residents_and_workers": resident_recommendations,
                "municipal_planners": planner_recommendations,
            },
        },
        "environmental_assessment": environmental_assessment,
        "ai_response": ai_response,
        "disclaimer": DISCLAIMER,
        "source": (
            "openrouter_ai_evidence_guidance"
            if ai_interpretation_applied or ai_recommendations_applied
            else "strands_tools_deterministic_evidence_response"
            if intent != "other"
            else "deterministic_evidence_fallback"
        ),
    }