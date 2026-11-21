from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(Path(__file__).resolve().parents[2] / ".env", ".env"),
        extra="ignore",
    )

    llm_api_keys: str = ""
    llm_model: str = "deepseek/deepseek-flash"
    llm_models: str = ""
    llm_api_base: str | None = None
    database_url: str = "sqlite:///./data/app.db"
    allowed_origins: str = "http://localhost:3000"
    enable_failure_simulation: bool = False
    default_system_prompt: str = (
        "You are a helpful assistant. Keep simple factual answers to 1-3 sentences. "
        "Expand only when the user asks for detail or the task requires it. "
        "Use Markdown when useful."
    )
    llm_timeout_seconds: float = Field(default=20, gt=0, le=120)
    cache_ttl_seconds: int = Field(default=86400, gt=0)
    port: int = Field(default=8000, ge=1, le=65535)

    @property
    def keys(self) -> list[str]:
        return [key.strip() for key in self.llm_api_keys.split(",") if key.strip()]

    @property
    def models(self) -> list[str]:
        if not self.llm_models.strip():
            return [self.llm_model] * len(self.keys)
        models = [model.strip() for model in self.llm_models.split(",")]
        if len(models) != len(self.keys) or not all(models):
            raise ValueError("LLM_MODELS must contain one nonempty model per API key")
        return models

    @property
    def origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def database_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("DATABASE_URL must use a SQLite file URL")
        location = self.database_url.removeprefix(prefix)
        if not location or location == ":memory:":
            raise ValueError("DATABASE_URL must point to a persistent SQLite file")
        return Path(location)
