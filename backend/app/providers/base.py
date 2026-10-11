from abc import ABC, abstractmethod
from typing import Any, Literal, NotRequired, Protocol, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.weather_evidence import WeatherEvidence

ProviderValueStatus = Literal["synthetic", "simulated", "observed", "forecast", "unavailable"]
ProviderLegacyDataType = Literal["observation", "forecast", "demo", "simulation", "calculated"]
ProviderDataType = Literal[
    "OBSERVATION", "FORECAST", "DEMO", "SIMULATION", "CALCULATED"
]
ProviderStatus = Literal["available", "partial", "unavailable"]


class ProviderProvenance(TypedDict):
    source: str
    source_url: str
    location_id: str
    latitude: float | None
    longitude: float | None
    data_type: ProviderDataType
    retrieval_time: str
    observation_time: NotRequired[str | None]
    forecast_time: NotRequired[str | None]
    quality: NotRequired[str]
    status: NotRequired[str]


class MeasurementProvenance(ProviderProvenance):
    value: Any
    unit: str
    quality: str
    status: str
    observation_time: NotRequired[str | None]
    forecast_time: NotRequired[str | None]


class ProviderValue(TypedDict):
    value: Any
    unit: str
    status: ProviderValueStatus
    dataType: NotRequired[ProviderDataType | ProviderLegacyDataType]
    validTime: NotRequired[str | None]
    provenance: NotRequired[MeasurementProvenance]


class ProviderResult(TypedDict):
    source: str
    timestamp: str
    retrievedAt: NotRequired[str]
    dataType: NotRequired[ProviderDataType | ProviderLegacyDataType]
    location: dict[str, Any]
    status: ProviderStatus
    dataQuality: str
    dataProvenance: Literal["DEMO", "DEMO_SIMULATION", "LIVE"]
    values: dict[str, ProviderValue]
    provenance: NotRequired[ProviderProvenance]
    measurements: NotRequired[list[MeasurementProvenance]]


class WeatherDataProvider(ABC):
    @abstractmethod
    def get_heat_observations(self, location_id: str) -> ProviderResult: ...


class WeatherProviderError(RuntimeError):
    """Expected, safely reportable failure from a configured weather provider."""


class WeatherEvidenceProvider(Protocol):
    provider_name: str

    def get_weather_evidence_for_location(
        self,
        location: dict[str, Any],
    ) -> "WeatherEvidence": ...


class RainfallDataProvider(ABC):
    @abstractmethod
    def get_rainfall_observations(self, location_id: str) -> ProviderResult: ...


class RiverDataProvider(ABC):
    @abstractmethod
    def get_river_observations(self, location_id: str) -> ProviderResult: ...


class GlacierDataProvider(ABC):
    @abstractmethod
    def get_glacial_lake_status(self, location_id: str) -> ProviderResult: ...


class PopulationExposureProvider(ABC):
    @abstractmethod
    def get_exposure_indices(self, location_id: str) -> ProviderResult: ...
