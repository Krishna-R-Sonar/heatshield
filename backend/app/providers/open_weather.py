from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import ValidationError

from app.models.weather_evidence import (
    WeatherEvidence,
    WeatherLocationEvidence,
    WeatherMeasurement,
    WeatherProvenance,
    WeatherTimeSeries,
)
from app.providers.base import WeatherProviderError
from app.providers.open_meteo import (
    DAILY_WEATHER_VARIABLES,
    DAILY_WEATHER_UNITS,
    HOURLY_WEATHER_UNITS,
    HOURLY_WEATHER_VARIABLES,
)

OPEN_WEATHER_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
MAX_RETURNED_COORDINATE_DISTANCE_KM = 30.0
MAX_THREE_HOUR_RAIN_MM = 500.0


class OpenWeatherError(WeatherProviderError):
    pass


class OpenWeatherForecastProvider:
    provider_name = "OpenWeather"
    source_url = "https://openweathermap.org/"
    supported_forecast_fields = frozenset({
        "temperature",
        "apparent_temperature",
        "humidity",
        "wind_speed",
        "wind_gusts",
        "wind_direction",
        "cloud_cover",
        "visibility",
    })

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.client = client

    @staticmethod
    def _number(value: Any, minimum: float, maximum: float) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        result = float(value)
        return result if math.isfinite(result) and minimum <= result <= maximum else None

    @staticmethod
    def _distance_km(
        first_latitude: float,
        first_longitude: float,
        second_latitude: float,
        second_longitude: float,
    ) -> float:
        earth_radius_km = 6371.0
        first_lat = math.radians(first_latitude)
        second_lat = math.radians(second_latitude)
        delta_lat = second_lat - first_lat
        delta_lon = math.radians(second_longitude - first_longitude)
        haversine = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(first_lat) * math.cos(second_lat) * math.sin(delta_lon / 2) ** 2
        )
        return earth_radius_km * 2 * math.asin(min(1.0, math.sqrt(haversine)))

    @classmethod
    def _coordinates(cls, location: dict[str, Any]) -> tuple[float, float]:
        latitude = cls._number(location.get("lat"), -90, 90)
        longitude = cls._number(location.get("lng"), -180, 180)
        if latitude is None or longitude is None:
            raise OpenWeatherError("Selected coordinates are missing or invalid.")
        return latitude, longitude

    def _request(self, latitude: float, longitude: float) -> dict[str, Any]:
        params = {
            "lat": latitude,
            "lon": longitude,
            "appid": self.api_key,
            "units": "metric",
        }
        try:
            response = (
                httpx.get(OPEN_WEATHER_FORECAST_URL, params=params, timeout=self.timeout_seconds)
                if self.client is None
                else self.client.get(
                    OPEN_WEATHER_FORECAST_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            )
            if response.status_code == 401 or response.status_code == 403:
                raise OpenWeatherError("OpenWeather rejected the configured API credentials or access.")
            if response.status_code == 429:
                raise OpenWeatherError("OpenWeather rate limit reached; secondary forecast unavailable.")
            response.raise_for_status()
            payload = response.json()
        except OpenWeatherError:
            raise
        except httpx.TimeoutException as exc:
            raise OpenWeatherError("OpenWeather forecast request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise OpenWeatherError(
                f"OpenWeather returned HTTP {exc.response.status_code}; secondary forecast unavailable."
            ) from exc
        except httpx.RequestError as exc:
            raise OpenWeatherError("OpenWeather forecast could not be reached.") from exc
        except ValueError as exc:
            raise OpenWeatherError("OpenWeather returned malformed forecast data.") from exc
        if not isinstance(payload, dict):
            raise OpenWeatherError("OpenWeather returned malformed forecast data.")
        return payload

    @staticmethod
    def _record_value(record: dict[str, Any], key: str) -> Any:
        path = key.split(".")
        value: Any = record
        for part in path:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def get_weather_evidence_for_location(
        self,
        location: dict[str, Any],
    ) -> WeatherEvidence:
        requested_latitude, requested_longitude = self._coordinates(location)
        payload = self._request(requested_latitude, requested_longitude)
        city = payload.get("city")
        returned = city.get("coord") if isinstance(city, dict) else None
        grid_latitude = self._number(
            returned.get("lat") if isinstance(returned, dict) else None, -90, 90
        )
        grid_longitude = self._number(
            returned.get("lon") if isinstance(returned, dict) else None, -180, 180
        )
        if grid_latitude is None or grid_longitude is None:
            raise OpenWeatherError("OpenWeather response omitted valid returned coordinates.")
        if self._distance_km(
            requested_latitude,
            requested_longitude,
            grid_latitude,
            grid_longitude,
        ) > MAX_RETURNED_COORDINATE_DISTANCE_KM:
            raise OpenWeatherError("OpenWeather forecast coordinates are too far from the selected location.")

        records = payload.get("list")
        if not isinstance(records, list) or not records:
            raise OpenWeatherError("OpenWeather response contains no forecast periods.")
        now = datetime.now(timezone.utc)
        parsed_records: list[tuple[datetime, dict[str, Any]]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            timestamp = record.get("dt")
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                continue
            try:
                valid_time = datetime.fromtimestamp(timestamp, timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
            parsed_records.append((valid_time, record))
        parsed_records.sort(key=lambda item: item[0])
        future_records = [item for item in parsed_records if item[0] >= now]
        if not future_records:
            raise OpenWeatherError("OpenWeather forecast contains no future forecast periods.")

        retrieved_at = datetime.now(timezone.utc).isoformat()
        forecast_time, current_record = future_records[0]
        source_quality = (
            "OpenWeather numerical forecast, requested by coordinates. "
            "The API returns forecast periods at three-hour steps, not station observations."
        )
        limitations = [
            "Forecast values are model output, not station observations or official warnings.",
            "OpenWeather returns a three-hour forecast interval and does not identify the underlying numerical model in this response.",
            "The provider supplies a UTC offset rather than an IANA timezone; these series use UTC and UTC calendar dates.",
            "Total precipitation is not inferred from optional rain or snow fields; only an explicit rain amount is retained.",
            "This feed is not assumed to be independent of other weather-model providers.",
        ]

        def raw_value(record: dict[str, Any], key: str, low: float, high: float) -> float | None:
            return self._number(self._record_value(record, key), low, high)

        def measurement(
            public_name: str,
            value: float | None,
            unit: str,
            *,
            aggregation_period: str = "instantaneous forecast at valid time",
            valid_from: str | None = None,
            valid_to: str | None = None,
            unavailable_reason: str | None = None,
        ) -> WeatherMeasurement:
            available = value is not None
            provenance: dict[str, Any] = {
                "source": self.provider_name,
                "source_url": self.source_url,
                "api": "5 Day / 3 Hour Forecast API",
                "model_id": None,
                "data_type": "FORECAST",
                "forecast_time": forecast_time.isoformat() if available else None,
                "retrieval_time": retrieved_at,
                "requested_latitude": requested_latitude,
                "requested_longitude": requested_longitude,
                "grid_latitude": grid_latitude,
                "grid_longitude": grid_longitude,
                "timezone": "UTC",
                "aggregation_period": aggregation_period,
                "quality": source_quality,
            }
            if valid_from is not None:
                provenance["valid_from"] = valid_from
            if valid_to is not None:
                provenance["valid_to"] = valid_to
            if unavailable_reason is not None:
                provenance["availability_reason"] = unavailable_reason
            return WeatherMeasurement(
                value=value,
                unit=unit,
                status="AVAILABLE" if available else "UNAVAILABLE",
                forecast_time=forecast_time.isoformat() if available else None,
                provenance=provenance,
            )

        three_hour_start = (forecast_time - timedelta(hours=3)).isoformat()
        three_hour_end = forecast_time.isoformat()
        field_values: dict[str, tuple[float | None, str, str]] = {
            "temperature": (
                raw_value(current_record, "main.temp", -90, 60),
                "°C",
                "instantaneous forecast at valid time",
            ),
            "apparent_temperature": (
                raw_value(current_record, "main.feels_like", -90, 60),
                "°C",
                "instantaneous forecast at valid time",
            ),
            "humidity": (
                raw_value(current_record, "main.humidity", 0, 100),
                "%",
                "instantaneous forecast at valid time",
            ),
            "rain": (
                raw_value(current_record, "rain.3h", 0, MAX_THREE_HOUR_RAIN_MM),
                "mm/3h",
                "rain accumulation over the preceding three-hour period",
            ),
            "precipitation_probability": (
                (
                    value * 100
                    if (value := raw_value(current_record, "pop", 0, 1)) is not None
                    else None
                ),
                "%",
                "probability over the provider's three-hour forecast period",
            ),
            "wind_speed": (
                (
                    value * 3.6
                    if (value := raw_value(current_record, "wind.speed", 0, 150)) is not None
                    else None
                ),
                "km/h",
                "instantaneous forecast at valid time",
            ),
            "wind_gusts": (
                (
                    value * 3.6
                    if (value := raw_value(current_record, "wind.gust", 0, 150)) is not None
                    else None
                ),
                "km/h",
                "instantaneous forecast at valid time",
            ),
            "wind_direction": (
                raw_value(current_record, "wind.deg", 0, 360),
                "°",
                "instantaneous forecast at valid time",
            ),
            "cloud_cover": (
                raw_value(current_record, "clouds.all", 0, 100),
                "%",
                "instantaneous forecast at valid time",
            ),
            "visibility": (
                raw_value(current_record, "visibility", 0, 100000),
                "m",
                "instantaneous forecast at valid time",
            ),
        }

        forecast: dict[str, WeatherMeasurement] = {}
        for public_name in HOURLY_WEATHER_VARIABLES.values():
            value, unit, aggregation_period = field_values.get(
                public_name,
                (None, HOURLY_WEATHER_UNITS.get(
                    next((key for key, name in HOURLY_WEATHER_VARIABLES.items() if name == public_name), ""),
                    "",
                ), "not provided by this provider"),
            )
            forecast[public_name] = measurement(
                public_name,
                value,
                unit,
                aggregation_period=aggregation_period,
                valid_from=three_hour_start if aggregation_period.startswith("probability") or aggregation_period.startswith("rain accumulation") else None,
                valid_to=three_hour_end if aggregation_period.startswith("probability") or aggregation_period.startswith("rain accumulation") else None,
                unavailable_reason=(
                    None if value is not None else
                    "This provider does not return this variable."
                    if public_name not in self.supported_forecast_fields else
                    "This provider did not return a valid value for this variable."
                ),
            )

        hourly_times = [time.isoformat() for time, _ in parsed_records]
        hourly_values: dict[str, list[float | int | None]] = {
            key: [None] * len(parsed_records) for key in HOURLY_WEATHER_VARIABLES
        }
        hourly_sources: dict[str, list[dict[str, Any]]] = {}
        record_fields = {
            "temperature_2m": ("main.temp", -90, 60, 1.0),
            "apparent_temperature": ("main.feels_like", -90, 60, 1.0),
            "relative_humidity_2m": ("main.humidity", 0, 100, 1.0),
            "rain": ("rain.3h", 0, MAX_THREE_HOUR_RAIN_MM, 1.0),
            "wind_speed_10m": ("wind.speed", 0, 150, 3.6),
            "wind_gusts_10m": ("wind.gust", 0, 150, 3.6),
            "wind_direction_10m": ("wind.deg", 0, 360, 1.0),
            "cloud_cover": ("clouds.all", 0, 100, 1.0),
            "visibility": ("visibility", 0, 100000, 1.0),
            "precipitation_probability": ("pop", 0, 1, 100.0),
        }
        for series_name, (api_field, minimum, maximum, multiplier) in record_fields.items():
            output = []
            for _, record in parsed_records:
                value = raw_value(record, api_field, minimum, maximum)
                output.append(value * multiplier if value is not None else None)
            hourly_values[series_name] = output
            unit = HOURLY_WEATHER_UNITS.get(series_name, "")
            hourly_sources[series_name] = [{
                "provider": self.provider_name,
                "source_url": self.source_url,
                "data_type": "FORECAST",
                "unit": unit,
                "retrieved_at": retrieved_at,
                "requested_latitude": requested_latitude,
                "requested_longitude": requested_longitude,
                "returned_latitude": grid_latitude,
                "returned_longitude": grid_longitude,
                "aggregation_period": "three-hour forecast steps",
            }]
        hourly_units = dict(HOURLY_WEATHER_UNITS)
        hourly_units["rain"] = "mm/3h"

        utc_days: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
        utc_period_days: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
        for valid_time, record in parsed_records:
            utc_days.setdefault(valid_time.date().isoformat(), []).append((valid_time, record))
            period_day = (valid_time - timedelta(microseconds=1)).date().isoformat()
            utc_period_days.setdefault(period_day, []).append((valid_time, record))
        daily_times = sorted(set(utc_days) | set(utc_period_days))
        daily_values: dict[str, list[float | int | None]] = {
            variable: [None] * len(daily_times) for variable in DAILY_WEATHER_VARIABLES
        }
        for index, day in enumerate(daily_times):
            temperature_periods = utc_days.get(day, [])
            accumulation_periods = utc_period_days.get(day, [])
            complete_temperature_day = (
                len(temperature_periods) == 8
                and all(temperature_periods[item][0].hour == item * 3 for item in range(8))
            )
            complete_accumulation_day = (
                len(accumulation_periods) == 8
                and [item[0].hour for item in accumulation_periods] == [3, 6, 9, 12, 15, 18, 21, 0]
            )
            temperatures = [
                raw_value(record, "main.temp", -90, 60)
                for _, record in temperature_periods
            ]
            if complete_temperature_day and all(value is not None for value in temperatures):
                daily_values["temperature_2m_max"][index] = max(
                    value for value in temperatures if value is not None
                )
                daily_values["temperature_2m_min"][index] = min(
                    value for value in temperatures if value is not None
                )
            rain_values = [
                raw_value(record, "rain.3h", 0, MAX_THREE_HOUR_RAIN_MM)
                for _, record in accumulation_periods
            ]
            if complete_accumulation_day and all(value is not None for value in rain_values):
                daily_values["rain_sum"][index] = sum(
                    value for value in rain_values if value is not None
                )

        daily_sources = {
            variable: [{
                "provider": self.provider_name,
                "source_url": self.source_url,
                "data_type": "FORECAST",
                "unit": DAILY_WEATHER_UNITS[variable],
                "retrieved_at": retrieved_at,
                "requested_latitude": requested_latitude,
                "requested_longitude": requested_longitude,
                "returned_latitude": grid_latitude,
                "returned_longitude": grid_longitude,
                "aggregation_period": (
                    "UTC calendar day; extrema of three-hour temperature samples"
                    if variable in {"temperature_2m_max", "temperature_2m_min"}
                    else "UTC calendar day; sum of eight consecutive preceding three-hour rain periods"
                ),
            }]
            for variable in DAILY_WEATHER_VARIABLES
            if variable in {
                "temperature_2m_max",
                "temperature_2m_min",
                "rain_sum",
            }
        }

        available_fields = [
            name for name, item in forecast.items() if item.status == "AVAILABLE"
        ]
        status = "AVAILABLE" if {"temperature", "humidity"}.issubset(available_fields) else (
            "DEGRADED" if available_fields else "UNAVAILABLE"
        )
        location_evidence = WeatherLocationEvidence(
            name=str(location.get("name") or "Selected map point"),
            state=str(location.get("state") or ""),
            country=str(location.get("country") or ""),
            requested_latitude=requested_latitude,
            requested_longitude=requested_longitude,
            grid_latitude=grid_latitude,
            grid_longitude=grid_longitude,
            timezone="UTC",
        )
        try:
            return WeatherEvidence(
                status=status,
                location=location_evidence,
                forecast=forecast,
                hourly=WeatherTimeSeries(
                    times=hourly_times,
                    values=hourly_values,
                    units=hourly_units,
                    interval_minutes=180,
                    aggregation_period="three-hour forecast steps; precipitation amounts cover each three-hour interval",
                    provenance=hourly_sources,
                ),
                daily=WeatherTimeSeries(
                    times=daily_times,
                    values=daily_values,
                    units=dict(DAILY_WEATHER_UNITS),
                    aggregation_period=(
                        "UTC calendar dates; temperature extrema use three-hour samples and rain "
                        "totals require eight complete preceding three-hour periods"
                    ),
                    provenance=daily_sources,
                ),
                provenance=WeatherProvenance(
                    source=self.provider_name,
                    source_url=self.source_url,
                    api="5 Day / 3 Hour Forecast API",
                    model_selection="provider-selected",
                    model_id=None,
                    forecast_generated_at=None,
                    retrieved_at=retrieved_at,
                    timezone="UTC",
                    spatial_resolution="Not provided in the forecast response.",
                    quality=source_quality,
                    limitations=limitations,
                ),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise OpenWeatherError("OpenWeather response could not be normalized safely.") from exc
