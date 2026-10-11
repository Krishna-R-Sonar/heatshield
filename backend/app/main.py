from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-engine"))
sys.path.insert(0, str(ROOT / "ai"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.providers.demo import DemoDataStore, DemoRainfallProvider
from app.providers.open_meteo import OpenMeteoRainfallProvider
from app.providers.open_weather import OpenWeatherForecastProvider
from app.providers.open_meteo_flood import OpenMeteoFloodProvider
from app.providers.open_meteo_historical import OpenMeteoHistoricalProvider
from app.providers.open_meteo_ensemble import OpenMeteoEnsembleProvider
from app.providers.open_meteo_validation import OpenMeteoForecastValidationProvider
from app.services.alert_service import AlertService
from app.services.flood_evidence_service import FloodEvidenceService
from app.services.historical_baseline_service import HistoricalBaselineService
from app.services.ensemble_evidence_service import EnsembleEvidenceService
from app.services.forecast_validation_service import ForecastValidationService
from app.services.weather_evidence_service import WeatherEvidenceService
from app.services.risk_service import RiskService
from app.services.real_heat_assessment_service import RealHeatAssessmentService


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = DemoDataStore(settings.data_dir)
    app.state.store = store
    app.state.rainfall_provider = (
        DemoRainfallProvider(store)
        if settings.data_mode == "demo"
        else OpenMeteoRainfallProvider(
            store,
            timeout_seconds=settings.open_meteo_timeout_seconds,
        )
    )
    app.state.weather_evidence_service = (
        WeatherEvidenceService(
            app.state.rainfall_provider,
            secondary_provider=(
                OpenWeatherForecastProvider(
                    settings.openweather_api_key,
                    timeout_seconds=settings.open_meteo_timeout_seconds,
                )
                if settings.openweather_api_key
                else None
            ),
        )
        if settings.data_mode == "real"
        else None
    )
    app.state.flood_evidence_service = (
        FloodEvidenceService(OpenMeteoFloodProvider(
            timeout_seconds=settings.open_meteo_timeout_seconds,
        ))
        if settings.data_mode == "real"
        else None
    )
    app.state.historical_baseline_service = (
        HistoricalBaselineService(OpenMeteoHistoricalProvider(
            timeout_seconds=max(settings.open_meteo_timeout_seconds, 10.0),
        ))
        if settings.data_mode == "real"
        else None
    )
    app.state.ensemble_evidence_service = (
        EnsembleEvidenceService(OpenMeteoEnsembleProvider(
            timeout_seconds=max(settings.open_meteo_timeout_seconds, 10.0),
        ))
        if settings.data_mode == "real"
        else None
    )
    app.state.forecast_validation_service = (
        ForecastValidationService(OpenMeteoForecastValidationProvider(
            timeout_seconds=max(settings.open_meteo_timeout_seconds, 10.0),
        ))
        if settings.data_mode == "real"
        else None
    )
    app.state.risk_service = RiskService(store)
    app.state.real_heat_assessment_service = (
        RealHeatAssessmentService(app.state.weather_evidence_service)
        if settings.data_mode == "real"
        else None
    )
    app.state.alert_service = AlertService()
    yield


app = FastAPI(title="HeatShield API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
