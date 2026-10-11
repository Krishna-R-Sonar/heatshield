from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock

from app.models.forecast_validation import ForecastValidationEvidence
from app.providers.open_meteo_validation import OpenMeteoForecastValidationProvider


class ForecastValidationService:
    def __init__(
        self,
        provider: OpenMeteoForecastValidationProvider,
        *,
        cache_ttl_seconds: int = 86400,
    ) -> None:
        self.provider = provider
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cache: dict[tuple[float, float], tuple[datetime, ForecastValidationEvidence]] = {}
        self._lock = Lock()

    def get_for_location(
        self,
        latitude: float,
        longitude: float,
        *,
        force_refresh: bool = False,
    ) -> ForecastValidationEvidence:
        key = (round(latitude, 4), round(longitude, 4))
        now = datetime.now(timezone.utc)
        with self._lock:
            cached = self._cache.get(key)
            if not force_refresh and cached and now - cached[0] < self.cache_ttl:
                return cached[1]
        result = self.provider.get_historical_performance(latitude, longitude)
        with self._lock:
            self._cache[key] = (now, result)
        return result