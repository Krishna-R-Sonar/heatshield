from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_OPENROUTER_FALLBACK_MODELS = (
    "apodex/apodex-1.1-mini:free,"
    "dots-studio/dots-3-note-preview:free,"
    "google/gemma-4-26b-a4b-it:free,"
    "google/gemma-4-31b-it:free,"
    "cohere/north-mini-code:free,"
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free,"
    "nvidia/nemotron-3-super-120b-a12b:free,"
    "nvidia/nemotron-3-ultra-550b-a55b:free,"
    "nvidia/nemotron-3.5-lightning:free,"
    "poolside/laguna-s-2.1:free,"
    "poolside/laguna-xs-2.1:free,"
    "thinkingmachines/inkling:free,"
    "thinkingmachines/inkling-small:free,"
    "inclusionai/ling-3.1-flash,"
    "openrouter/free"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_model: str = "liquid/lfm-2.5-2.6b:free"
    openrouter_fallback_models: str = DEFAULT_OPENROUTER_FALLBACK_MODELS
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ai_agent_enabled: bool = True
    ai_model_provider: Literal["openrouter"] = "openrouter"
    data_mode: Literal["demo", "real"] = "demo"
    open_meteo_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    openweather_api_key: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    data_dir: Path = ROOT / "data" / "demo"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def openrouter_model_chain(self) -> list[str]:
        configured = [self.openrouter_model.strip()]
        configured.extend(
            model.strip()
            for model in self.openrouter_fallback_models.split(",")
            if model.strip()
        )
        return list(dict.fromkeys(model for model in configured if model))


settings = Settings()
