"""Ordered, request-local LiteLLM failover with its native response cache."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)
SUPPORTED_FAILURES = {429, 500, 502, 503}


class ProvidersUnavailable(Exception):
    pass


def parse_simulation(header: str | None, key_count: int) -> dict[str, int]:
    if header is None:
        return {}
    failures: dict[str, int] = {}
    allowed = {f"key_{index + 1}" for index in range(key_count)} | {"*"}
    for pair in header.split(","):
        key, separator, status = pair.strip().partition("=")
        key, status = key.strip(), status.strip()
        if (
            not separator
            or key not in allowed
            or status not in {str(value) for value in SUPPORTED_FAILURES}
            or key in failures
        ):
            raise ValueError("invalid X-Simulate-Failure header")
        failures[key] = int(status)
    return failures


@dataclass(frozen=True)
class Resolution:
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    source: str
    key_id: str | None
    fallback_attempts: int


def response_values(response: Any) -> tuple[str, int, int]:
    data = response.model_dump() if hasattr(response, "model_dump") else response
    try:
        reply = data["choices"][0]["message"]["content"]
        input_tokens = data["usage"]["prompt_tokens"]
        output_tokens = data["usage"]["completion_tokens"]
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("provider response is missing content or usage") from error
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("provider returned an empty reply")
    if any(type(value) is not int or value <= 0 for value in (input_tokens, output_tokens)):
        raise ValueError("provider response contains invalid usage")
    return reply, input_tokens, output_tokens


class Gateway:
    def __init__(self, settings: Settings):
        import litellm
        from litellm.caching.caching import Cache

        self.litellm = litellm
        self.settings = settings
        self.keys = settings.keys
        if len(self.keys) < 2 or len(set(self.keys)) != len(self.keys):
            raise ValueError("LLM_API_KEYS must contain at least two distinct API keys")
        settings.models  # Validate any per-key model configuration before startup.
        # The Cache instance belongs to this application, not a global callback pool.
        self.cache = Cache(type="local", ttl=settings.cache_ttl_seconds)
        litellm.telemetry = False
        litellm.suppress_debug_info = True

    def cache_arguments(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        identity = json.dumps(
            {"models": list(dict.fromkeys(self.settings.models)), "messages": messages},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return {
            "cache_key": hashlib.sha256(identity.encode()).hexdigest(),
            "model": self.settings.models[0],
            "messages": messages,
            "cache": {"ttl": self.settings.cache_ttl_seconds},
        }

    async def resolve(self, messages: list[dict[str, str]], failures: dict[str, int]) -> Resolution:
        models = self.settings.models
        cache_args = self.cache_arguments(messages)
        if not failures:
            cached = await self.cache.async_get_cache(**cache_args)
            if cached is not None:
                try:
                    reply, input_tokens, output_tokens = response_values(cached)
                    cached_model = cached.get("lyftr_model", models[0])
                    if cached_model not in models:
                        raise ValueError("cached model is not in the configured route")
                except ValueError:
                    pass  # An unusable cache entry cannot masquerade as an answer.
                else:
                    return Resolution(
                        reply,
                        cached_model,
                        input_tokens,
                        output_tokens,
                        "LLM_CACHE",
                        None,
                        0,
                    )

        for index, key in enumerate(self.keys):
            key_id = f"key_{index + 1}"
            model = models[index]
            simulated_status = failures.get(key_id, failures.get("*"))
            try:
                if simulated_status:
                    # Run through the same exception/fallback path as normalized provider errors.
                    if simulated_status == 429:
                        raise self.litellm.RateLimitError(
                            message="simulated rate limit",
                            model=model,
                            llm_provider="simulation",
                        )
                    raise self.litellm.APIError(
                        status_code=simulated_status,
                        message="simulated provider failure",
                        model=model,
                        llm_provider="simulation",
                    )
                kwargs = {
                    "model": model,
                    "messages": messages,
                    "api_key": key,
                    "timeout": self.settings.llm_timeout_seconds,
                    "num_retries": 0,
                    "stream": False,
                    "caching": False,
                    "cache": {"no-cache": True, "no-store": True},
                }
                if self.settings.llm_api_base and model == models[0]:
                    kwargs["api_base"] = self.settings.llm_api_base
                if model.startswith("deepseek/"):
                    # Regular chat avoids DeepSeek's default thinking-mode latency.
                    kwargs.setdefault("api_base", "https://api.deepseek.com")
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                if model.startswith("groq/"):
                    # Groq's OpenAI-compatible API avoids a missing-service_tier
                    # response bug in this pinned LiteLLM Groq adapter.
                    kwargs["model"] = "openai/" + model.removeprefix("groq/")
                    kwargs.setdefault("api_base", "https://api.groq.com/openai/v1")
                    if model == "groq/openai/gpt-oss-20b":
                        kwargs["extra_body"] = {"reasoning_effort": "low"}
                # Bound the entire SDK attempt, including any provider-client work.
                async with asyncio.timeout(self.settings.llm_timeout_seconds):
                    response = await self.litellm.acompletion(**kwargs)
                reply, input_tokens, output_tokens = response_values(response)
            except Exception as error:
                # Never log provider exception text: it may include credentials or content.
                status = getattr(error, "status_code", None)
                log = logger.info if simulated_status else logger.warning
                log(
                    "Provider attempt failed: %s (%s, status=%s)",
                    key_id,
                    type(error).__name__,
                    status if type(status) is int else "unknown",
                )
                continue
            if not failures:
                # Await native insertion so an immediately repeated request can hit.
                data = response.model_dump() if hasattr(response, "model_dump") else response
                await self.cache.async_add_cache({**data, "lyftr_model": model}, **cache_args)
            return Resolution(
                reply,
                model,
                input_tokens,
                output_tokens,
                "LIVE_INFERENCE",
                key_id,
                index,
            )
        raise ProvidersUnavailable
