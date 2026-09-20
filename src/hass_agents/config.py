"""Application settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic_prefix: str = "house"

    openai_api_key: str = ""
    openai_api_base: str = ""
    llm_model: str = "google/gemma-4-31B-it"
    infomaniak_product_id: str = ""

    zscore_warn: float = 2.0
    zscore_critical: float = 3.0
    delta_pct_warn: float = 25.0
    delta_pct_critical: float = 50.0
    comfort_base_temp: float = 18.0

    entities_config: Path = Field(default=Path("config/entities.yaml"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def openai_base_url(self) -> str:
        if self.openai_api_base.strip():
            return self.openai_api_base.rstrip("/")
        if self.infomaniak_product_id.strip():
            return (
                f"https://api.infomaniak.com/2/ai/"
                f"{self.infomaniak_product_id.strip()}/openai/v1"
            )
        return "https://api.openai.com/v1"

    @property
    def crewai_model(self) -> str:
        """Model id for CrewAI/LiteLLM.

        Infomaniak model names like ``google/gemma-4-31B-it`` must be prefixed
        with ``openai/`` so LiteLLM uses the OpenAI-compatible base_url instead
        of the native Google GenAI provider.
        """
        model = self.llm_model.strip()
        if not model:
            return model
        known_prefixes = (
            "openai/",
            "azure/",
            "ollama/",
            "anthropic/",
            "bedrock/",
            "groq/",
        )
        if model.startswith(known_prefixes):
            return model
        # Custom OpenAI-compatible endpoint (Infomaniak, etc.)
        if self.openai_base_url and "api.openai.com" not in self.openai_base_url:
            return f"openai/{model}"
        return model
