from datetime import datetime, timedelta, timezone
from threading import Lock

from app.models.flood_evidence import FloodEvidence
from app.providers.open_meteo_flood import OpenMeteoFloodProvider


class FloodEvidenceService:
    def __init__(self, provider: OpenMeteoFloodProvider, *, cache_ttl_seconds: int = 900) -> None:
        self.provider = provider
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cache: dict[tuple[float, float], tuple[datetime, FloodEvidence]] = {}
        self._lock = Lock()

    def get_for_location(
        self,
        latitude: float,
        longitude: float,
        *,
        force_refresh: bool = False,
    ) -> FloodEvidence:
        key = (latitude, longitude)
        now = datetime.now(timezone.utc)
        with self._lock:
            cached = self._cache.get(key)
            if not force_refresh and cached and now - cached[0] < self.cache_ttl:
                result = cached[1]
                return result.model_copy(update={
                    "location": result.location.model_copy(update={
                        "requested_latitude": latitude,
                        "requested_longitude": longitude,
                    }),
                    "provenance": result.provenance.model_copy(update={
                        "requested_latitude": latitude,
                        "requested_longitude": longitude,
                    }),
                })
        result = self.provider.get_evidence(latitude, longitude)
        with self._lock:
            self._cache[key] = (now, result)
        return result