from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Literal

import httpx

from app.models.flood_evidence import (
    FloodDailyEvidence,
    FloodEvidence,
    FloodLocationEvidence,
    FloodMeasurement,
    FloodProvenance,
)

OPEN_METEO_FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
GLOFAS_REANALYSIS_END_DATE = date(2022, 7, 31)
FLOOD_VARIABLES = {
    "river_discharge": "discharge",
    "river_discharge_mean": "mean",
    "river_discharge_median": "median",
    "river_discharge_min": "minimum",
    "river_discharge_max": "maximum",
    "river_discharge_p25": "p25",
    "river_discharge_p75": "p75",
}
FLOOD_LIMITATIONS = [
    "This is modeled river-discharge evidence, not an actual river level at this location.",
    "The approximately 5 km grid may select the largest or nearest modeled river; the API does not return a river name or verify local association.",
    "Discharge is not converted into an official flood warning or a HeatShield risk score.",
    "Discharge spread bands are display-only: IQR/median <=0.25 LOW, >0.25 to 0.75 MODERATE, >0.75 HIGH; zero median with nonzero IQR is HIGH. They are not hazard thresholds.",
    "Spread describes model-member disagreement only; no or low spread does not imply zero flood risk, impact, or safety.",
    "The API response does not include an explicit forecast initialization time or model version; GloFAS v4 is the documented Open-Meteo default.",
    "GloFAS v4 reanalysis coverage ends in July 2022; later past dates are not labeled reanalysis by this integration.",
]


class OpenMeteoFloodError(RuntimeError):
    pass


class OpenMeteoFloodProvider:
    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client

    @staticmethod
    def _number(values: Any, index: int, expected_length: int) -> float | None:
        if not isinstance(values, list) or len(values) != expected_length:
            return None
        value = values[index]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or value < 0
        ):
            return None
        return float(value)

    def get_evidence(
        self,
        latitude: float,
        longitude: float,
    ) -> FloodEvidence:
        if (
            not math.isfinite(latitude)
            or not -90 <= latitude <= 90
            or not math.isfinite(longitude)
            or not -180 <= longitude <= 180
        ):
            raise OpenMeteoFloodError("Location coordinates are missing or invalid.")

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": ",".join(FLOOD_VARIABLES),
            "past_days": 7,
            "forecast_days": 30,
            "cell_selection": "nearest",
        }
        try:
            if self.client is None:
                response = httpx.get(
                    OPEN_METEO_FLOOD_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.client.get(
                    OPEN_METEO_FLOOD_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise OpenMeteoFloodError("Open-Meteo GloFAS request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise OpenMeteoFloodError(
                f"Open-Meteo GloFAS returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise OpenMeteoFloodError("Open-Meteo GloFAS could not be reached.") from exc
        except ValueError as exc:
            raise OpenMeteoFloodError("Open-Meteo GloFAS returned malformed JSON.") from exc

        daily = payload.get("daily") if isinstance(payload, dict) else None
        units = payload.get("daily_units") if isinstance(payload, dict) else None
        times = daily.get("time") if isinstance(daily, dict) else None
        if not isinstance(daily, dict) or not isinstance(units, dict) or not isinstance(times, list):
            raise OpenMeteoFloodError("Open-Meteo GloFAS response is missing daily discharge data.")
        if any(not isinstance(value, str) for value in times):
            raise OpenMeteoFloodError("Open-Meteo GloFAS returned invalid daily timestamps.")
        try:
            dates = [datetime.fromisoformat(value).date() for value in times]
        except ValueError as exc:
            raise OpenMeteoFloodError("Open-Meteo GloFAS returned invalid daily timestamps.") from exc
        if any(right <= left for left, right in zip(dates, dates[1:])):
            raise OpenMeteoFloodError("Open-Meteo GloFAS timestamps are not strictly increasing.")

        unit = units.get("river_discharge")
        compatible_units = unit == "m³/s"
        retrieved_at = datetime.now(timezone.utc).isoformat()
        today = datetime.now(timezone.utc).date()
        points: list[FloodDailyEvidence] = []
        valid_discharge_count = 0
        historical_dates: list[str] = []
        forecast_dates: list[str] = []
        partial_points = False
        for index, value in enumerate(times):
            data_type: Literal["FORECAST", "REANALYSIS", "UNAVAILABLE"] = (
                "REANALYSIS"
                if dates[index] <= GLOFAS_REANALYSIS_END_DATE
                else "FORECAST"
                if dates[index] >= today
                else "UNAVAILABLE"
            )
            if data_type == "REANALYSIS":
                historical_dates.append(value)
            elif data_type == "FORECAST":
                forecast_dates.append(value)

            measures: dict[str, FloodMeasurement] = {}
            for api_name, field_name in FLOOD_VARIABLES.items():
                metric_unit = units.get(api_name)
                if not isinstance(metric_unit, str):
                    metric_unit = ""
                measure_value = (
                    self._number(daily.get(api_name), index, len(times))
                    if compatible_units and metric_unit == "m³/s" and data_type != "UNAVAILABLE"
                    else None
                )
                status: Literal["AVAILABLE", "UNAVAILABLE"] = (
                    "AVAILABLE" if measure_value is not None else "UNAVAILABLE"
                )
                if field_name == "discharge" and measure_value is not None:
                    valid_discharge_count += 1
                elif field_name == "discharge":
                    partial_points = True
                measures[field_name] = FloodMeasurement(
                    value=measure_value,
                    unit=metric_unit or "m³/s",
                    status=status,
                    data_type=data_type,
                    reference_time=value,
                    provenance={
                        "source": "Open-Meteo Global Flood API",
                        "dataset": "GloFAS",
                        "data_type": data_type,
                        "reference_time": value,
                        "retrieval_time": retrieved_at,
                        "requested_latitude": latitude,
                        "requested_longitude": longitude,
                        "grid_latitude": payload.get("latitude"),
                        "grid_longitude": payload.get("longitude"),
                        "unit": metric_unit or "m³/s",
                        "quality": "Modeled river-discharge evidence; local river association is unverified.",
                    },
                )
            points.append(FloodDailyEvidence(
                time=value,
                data_type=data_type,
                discharge=measures["discharge"],
                mean=measures["mean"],
                median=measures["median"],
                minimum=measures["minimum"],
                maximum=measures["maximum"],
                p25=measures["p25"],
                p75=measures["p75"],
                **self._spread_summary(
                    data_type,
                    measures["median"].value,
                    measures["p25"].value,
                    measures["p75"].value,
                ),
            ))

        grid_latitude = payload.get("latitude")
        grid_longitude = payload.get("longitude")
        historical_period = (
            f"{historical_dates[0]} to {historical_dates[-1]}"
            if historical_dates
            else None
        )
        forecast_period = (
            f"{forecast_dates[0]} to {forecast_dates[-1]}"
            if forecast_dates
            else None
        )
        horizon = len(forecast_dates)
        if valid_discharge_count == 0:
            status = "UNAVAILABLE"
            quality = (
                "No usable discharge values were returned for the selected GloFAS grid cell; "
                "no local river evidence is available."
            )
        elif partial_points:
            status = "DEGRADED"
            quality = "Some daily discharge values are unavailable; remaining values are modeled evidence."
        else:
            status = "AVAILABLE"
            quality = "Daily GloFAS model discharge; not a measured river level."

        return FloodEvidence(
            status=status,
            location=FloodLocationEvidence(
                requested_latitude=latitude,
                requested_longitude=longitude,
                grid_latitude=grid_latitude if isinstance(grid_latitude, (int, float)) else None,
                grid_longitude=grid_longitude if isinstance(grid_longitude, (int, float)) else None,
                elevation_m=payload.get("elevation") if isinstance(payload.get("elevation"), (int, float)) else None,
                timezone=payload.get("timezone") if isinstance(payload.get("timezone"), str) else None,
            ),
            daily=points,
            provenance=FloodProvenance(
                retrieved_at=retrieved_at,
                generationtime_ms=(
                    float(payload["generationtime_ms"])
                    if isinstance(payload.get("generationtime_ms"), (int, float))
                    else None
                ),
                timezone=payload.get("timezone") if isinstance(payload.get("timezone"), str) else None,
                requested_latitude=latitude,
                requested_longitude=longitude,
                grid_latitude=grid_latitude if isinstance(grid_latitude, (int, float)) else None,
                grid_longitude=grid_longitude if isinstance(grid_longitude, (int, float)) else None,
                returned_historical_period=historical_period,
                returned_forecast_period=forecast_period,
                forecast_horizon_days=horizon,
                quality=quality,
                limitations=FLOOD_LIMITATIONS,
            ),
        )

    @staticmethod
    def _spread_summary(
        data_type: Literal["FORECAST", "REANALYSIS"],
        median: float | None,
        p25: float | None,
        p75: float | None,
    ) -> dict[str, Any]:
        if (
            data_type != "FORECAST"
            or median is None
            or p25 is None
            or p75 is None
        ):
            return {
                "discharge_spread_iqr": None,
                "uncertainty": "UNAVAILABLE",
                "uncertainty_reason": "GloFAS ensemble quantiles are unavailable for this period.",
            }
        spread = max(0.0, p75 - p25)
        if median == 0 and spread > 0:
            return {
                "discharge_spread_iqr": spread,
                "uncertainty": "HIGH",
                "uncertainty_reason": "Ensemble median is zero with nonzero IQR; display-only spread descriptor.",
            }
        ratio = spread / abs(median) if median else 0.0
        if ratio <= 0.25 or math.isclose(ratio, 0.25, rel_tol=1e-12, abs_tol=1e-12):
            category = "LOW"
        elif ratio <= 0.75 or math.isclose(ratio, 0.75, rel_tol=1e-12, abs_tol=1e-12):
            category = "MODERATE"
        else:
            category = "HIGH"
        return {
            "discharge_spread_iqr": spread,
            "uncertainty": category,
            "uncertainty_reason": (
                f"Ensemble discharge IQR/median is {ratio:.2f}; display-only spread category, "
                "not a flood threshold."
            ),
        }