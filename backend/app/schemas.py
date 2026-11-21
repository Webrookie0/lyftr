from typing import Any, Literal

from pydantic import BaseModel, Field, StrictStr, field_validator


class ChatRequest(BaseModel):
    session_id: StrictStr = Field(min_length=1, max_length=128)
    message: StrictStr = Field(min_length=1, max_length=4000)
    system_prompt: StrictStr | None = None

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id must not be blank")
        return value

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class Metadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    key_id: str | None
    fallback_attempts: int = Field(ge=0)


class ChatResponse(BaseModel):
    session_id: str
    reply: str = Field(min_length=1)
    metadata: Metadata


class StoredMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    metadata: dict[str, Any] | None = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[StoredMessage]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    keys_configured: int
    cache_enabled: bool
