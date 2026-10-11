from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from app.models.weather_evidence import (
    HistoricalAnomalyEvidence,
    HistoricalAnomalyPoint,
    HistoricalBaselineStatistics,
    WeatherEvidence,
)
from app.providers.open_meteo_historical import OpenMeteoHistoricalProvider

BASELINE_METHOD = (
    "For each forecast date, compare the forecast daily value with ERA5 daily values "
    "from the same calendar month in the fixed 1991-2020 baseline. The baseline is the "
    "arithmetic mean; percentile is the empirical midrank among available daily values. "
    "Quantiles use linear interpolation (R-7)."
)
HISTORICAL_LIMITATIONS = [
    "ERA5 is a gridded reanalysis estimate, not a measured station observation.",
    "The monthly calendar comparison is seasonal context and does not adjust for local terrain or station microclimate.",
    "ERA5 grid resolution is approximately 25 km; forecast and reanalysis grid cells may differ.",
    "Rainfall anomaly uses ERA5 rain_sum; precipitation anomaly uses precipitation_sum, which may include solid precipitation water equivalent.",
]


class HistoricalBaselineService:
    def __init__(
        self,
        provider: OpenMeteoHistoricalProvider,
        *,
        cache_ttl_seconds: int = 86400,
    ) -> None:
        self.provider = provider
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cache: dict[tuple[float, float, str], tuple[datetime, dict[str, Any]]] = {}
        self._lock = Lock()

    @staticmethod
    def _quantile(sorted_values: list[float], fraction: float) -> float:
        if len(sorted_values) == 1:
            return sorted_values[0]
        position = (len(sorted_values) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return sorted_values[lower]
        weight = position - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight

    def _history(
        self,
        latitude: float,
        longitude: float,
        timezone_name: str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        key = (round(latitude, 4), round(longitude, 4), timezone_name)
        now = datetime.now(timezone.utc)
        with self._lock:
            cached = self._cache.get(key)
            if not force_refresh and cached and now - cached[0] < self.cache_ttl:
                return cached[1]
            result = self.provider.get_daily_baseline(
                latitude,
                longitude,
                timezone_name=timezone_name,
            )
            self._cache[key] = (now, result)
            return result

    def calculate(
        self,
        weather: WeatherEvidence,
        *,
        force_refresh: bool = False,
    ) -> HistoricalAnomalyEvidence:
        timezone_name = weather.location.timezone
        if not timezone_name:
            return HistoricalAnomalyEvidence(
                status="UNAVAILABLE",
                anomalies=[],
                methodology=BASELINE_METHOD,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                quality="Forecast timezone unavailable; daily ERA5 comparisons cannot be aligned to forecast dates.",
                limitations=HISTORICAL_LIMITATIONS,
            )
        methodology = (
            f"{BASELINE_METHOD} Forecast and reanalysis daily periods use the "
            f"{timezone_name} calendar timezone."
        )
        try:
            history = self._history(
                weather.location.requested_latitude,
                weather.location.requested_longitude,
                timezone_name,
                force_refresh=force_refresh,
            )
        except Exception as exc:
            return HistoricalAnomalyEvidence(
                status="UNAVAILABLE",
                anomalies=[],
                methodology=methodology,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                quality=f"Historical baseline unavailable: {type(exc).__name__}.",
                limitations=HISTORICAL_LIMITATIONS,
            )

        historical_by_month: dict[int, dict[str, list[float]]] = {
            month: {variable: [] for variable in history["series"]}
            for month in range(1, 13)
        }
        for index, date_value in enumerate(history["times"]):
            try:
                month = datetime.fromisoformat(date_value).month
            except ValueError:
                continue
            for variable, values in history["series"].items():
                value = values[index]
                if value is not None:
                    historical_by_month[month][variable].append(value)

        indicators = (
            ("temperature_max", "temperature_2m_max", "°C"),
            ("precipitation", "precipitation_sum", "mm"),
            ("rainfall", "rain_sum", "mm"),
        )
        anomalies: list[HistoricalAnomalyPoint] = []
        limitations = list(HISTORICAL_LIMITATIONS)
        for index, forecast_date in enumerate(weather.daily.times):
            try:
                month = datetime.fromisoformat(forecast_date.replace("Z", "+00:00")).month
            except ValueError:
                continue
            for indicator, variable, unit in indicators:
                forecast_values = weather.daily.values.get(variable, [])
                current_value = forecast_values[index] if index < len(forecast_values) else None
                if (
                    not isinstance(current_value, (int, float))
                    or isinstance(current_value, bool)
                    or not math.isfinite(float(current_value))
                ):
                    current_value = None
                samples = sorted(historical_by_month.get(month, {}).get(variable, []))
                series_sources = weather.daily.provenance.get(variable, [])
                aggregation_period = (
                    str(series_sources[0].get("aggregation_period", ""))
                    if series_sources
                    else ""
                )
                comparable_period = not (
                    indicator == "temperature_max"
                    and "extrema of three-hour temperature samples" in aggregation_period
                )
                if not comparable_period:
                    limitations.append(
                        "OpenWeather daily maximum temperature is sampled at three-hour intervals and is not compared with ERA5 daily maximum temperature."
                    )
                baseline = None
                anomaly = None
                percentile = None
                status = "UNAVAILABLE"
                if samples:
                    baseline = HistoricalBaselineStatistics(
                        mean=sum(samples) / len(samples),
                        median=self._quantile(samples, 0.5),
                        p25=self._quantile(samples, 0.25),
                        p75=self._quantile(samples, 0.75),
                        minimum=samples[0],
                        maximum=samples[-1],
                        sample_count=len(samples),
                    )
                    if current_value is not None and comparable_period:
                        anomaly = float(current_value) - baseline.mean
                        below = sum(value < current_value for value in samples)
                        equal = sum(value == current_value for value in samples)
                        percentile = (below + 0.5 * equal) / len(samples) * 100
                        status = "AVAILABLE"
                anomalies.append(HistoricalAnomalyPoint(
                    date=forecast_date[:10],
                    indicator=indicator,
                    current_value=float(current_value) if current_value is not None else None,
                    unit=unit,
                    anomaly=anomaly,
                    percentile=percentile,
                    historical_baseline=baseline,
                    status=status,
                    forecast_source=(
                        next(
                            (
                                str(source.get("provider"))
                                for source in weather.daily.provenance.get(
                                    "temperature_2m_max"
                                    if indicator == "temperature_max"
                                    else "rain_sum"
                                    if indicator == "rainfall"
                                    else "precipitation_sum",
                                    [],
                                )
                                if source.get("provider")
                            ),
                            weather.provenance.source,
                        )
                        if current_value is not None
                        else None
                    ),
                ))

        available_count = sum(item.status == "AVAILABLE" for item in anomalies)
        report_status = "UNAVAILABLE" if not available_count else (
            "AVAILABLE" if available_count == len(anomalies) else "DEGRADED"
        )
        return HistoricalAnomalyEvidence(
            status=report_status,
            anomalies=anomalies,
            methodology=methodology,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            grid_latitude=(
                float(history["latitude"])
                if isinstance(history.get("latitude"), (int, float))
                else None
            ),
            grid_longitude=(
                float(history["longitude"])
                if isinstance(history.get("longitude"), (int, float))
                else None
            ),
            quality=(
                f"ERA5 monthly baseline; {len(history['times'])} daily reanalysis records returned."
            ),
            limitations=list(dict.fromkeys(limitations)),
        )