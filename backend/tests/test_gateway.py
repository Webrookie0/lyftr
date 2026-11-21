"""Integration with actual LiteLLM and native Cache, mocking HTTP transport only."""

import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.config import Settings
from app.gateway import Gateway, ProvidersUnavailable
from app.main import create_app

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
HAS_LITELLM = importlib.util.find_spec("litellm") is not None


@unittest.skipUnless(HAS_LITELLM, "LiteLLM must be installed for native integration checks")
class NativeGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import litellm
        from litellm.litellm_core_utils import logging_worker
        from openai import AsyncOpenAI

        # unittest gives each test its own loop; the SDK's global logging queue
        # must belong to that loop too. Actual completion/caching remain native.
        self.logging_worker = logging_worker.LoggingWorker()
        self.worker_patch = patch.object(
            logging_worker, "GLOBAL_LOGGING_WORKER", self.logging_worker
        )
        self.worker_patch.start()
        self.requests = []
        self.statuses = {}
        self.missing_usage = False
        self.empty_reply = False
        self.settings = Settings(
            _env_file=None,
            llm_api_keys="test-key-one,test-key-two",
            llm_model="openai/gpt-4o-mini",
            llm_models="",
            llm_api_base=None,
            llm_timeout_seconds=2,
        )
        self.gateway = Gateway(self.settings)

        async def provider(request):
            self.requests.append(request)
            await asyncio.sleep(0.01)
            key = request.headers["authorization"].removeprefix("Bearer ")
            status = self.statuses.get(key, 200)
            if status != 200:
                return httpx.Response(
                    status,
                    json={
                        "error": {
                            "message": "provider unavailable",
                            "type": "rate_limit_error" if status == 429 else "server_error",
                            "code": "rate_limit_exceeded" if status == 429 else None,
                        }
                    },
                )
            messages = json.loads(request.content)["messages"]
            result = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "" if self.empty_reply else "A test answer.",
                        },
                    }
                ],
            }
            if not self.missing_usage:
                result["usage"] = {
                    "prompt_tokens": len(json.dumps(messages)),
                    "completion_tokens": 8,
                    "total_tokens": len(json.dumps(messages)) + 8,
                }
            return httpx.Response(200, json=result)

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        self.clients = {
            key: AsyncOpenAI(
                api_key=key,
                base_url="https://provider.test/v1",
                http_client=self.http,
                max_retries=0,
            )
            for key in self.settings.keys
        }
        native_completion = litellm.acompletion

        async def complete(**kwargs):
            return await native_completion(**kwargs, client=self.clients[kwargs["api_key"]])

        self.patcher = patch.object(litellm, "acompletion", side_effect=complete)
        self.patcher.start()
        self.messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "hello"},
        ]

    async def asyncTearDown(self):
        self.patcher.stop()
        await asyncio.sleep(0)
        await self.logging_worker.flush()
        await self.logging_worker.stop()
        self.worker_patch.stop()
        await self.http.aclose()

    async def test_immediate_native_cache_preserves_usage(self):
        first = await self.gateway.resolve(self.messages, {})
        second = await self.gateway.resolve(self.messages, {})
        self.assertEqual((first.source, second.source), ("LIVE_INFERENCE", "LLM_CACHE"))
        self.assertEqual(
            (first.reply, first.input_tokens, first.output_tokens),
            (second.reply, second.input_tokens, second.output_tokens),
        )
        self.assertIsNone(second.key_id)
        self.assertEqual(second.fallback_attempts, 0)
        self.assertEqual(len(self.requests), 1)

    async def test_repeated_question_with_history_misses_but_fresh_session_hits(self):
        question = "can you tell me what's the name of first president of america ?"
        turns = [
            question,
            "can you tell me what i asked before this qn ?",
            "can you tell me what i asked before this qn?",
            question,
        ]
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings.model_copy(
                update={"database_url": f"sqlite:///{directory}/app.db"}
            )
            app = create_app(settings, self.gateway)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    replies = []
                    for message in turns:
                        response = await client.post(
                            "/chat", json={"session_id": "conversation", "message": message}
                        )
                        self.assertEqual(response.status_code, 200)
                        replies.append(response.json())
                        self.assertEqual(
                            replies[-1]["metadata"]["resolution_source"], "LIVE_INFERENCE"
                        )
                    self.assertEqual(len(self.requests), 4)
                    self.assertEqual(
                        [len(json.loads(request.content)["messages"]) for request in self.requests],
                        [2, 4, 6, 8],
                    )
                    self.assertGreater(
                        replies[-1]["metadata"]["input_tokens"],
                        replies[0]["metadata"]["input_tokens"],
                    )
                    response = await client.post(
                        "/chat", json={"session_id": "fresh-conversation", "message": question}
                    )
                    self.assertEqual(response.status_code, 200)
                    cached = response.json()
                    self.assertEqual(cached["metadata"]["resolution_source"], "LLM_CACHE")
                    self.assertEqual(cached["reply"], replies[0]["reply"])
                    for field in ("model", "input_tokens", "output_tokens"):
                        self.assertEqual(cached["metadata"][field], replies[0]["metadata"][field])
                    self.assertIsNone(cached["metadata"]["key_id"])
                    self.assertEqual(cached["metadata"]["fallback_attempts"], 0)
                    self.assertEqual(len(self.requests), 4)
                    history = await client.get("/sessions/fresh-conversation/messages")
                    self.assertEqual(
                        history.json()["messages"][1]["metadata"]["resolution_source"],
                        "LLM_CACHE",
                    )

    async def test_model_prompt_and_full_history_cache_identity(self):
        await self.gateway.resolve(self.messages, {})
        variants = [
            [*self.messages[:-1], {"role": "user", "content": "different"}],
            [{"role": "system", "content": "Different tone"}, self.messages[-1]],
            [*self.messages, {"role": "assistant", "content": "prior"}, self.messages[-1]],
        ]
        for messages in variants:
            self.assertEqual((await self.gateway.resolve(messages, {})).source, "LIVE_INFERENCE")
        self.assertEqual(len(self.requests), 4)
        identity = self.gateway.cache_arguments(self.messages)["cache_key"]
        self.gateway.settings = self.settings.model_copy(update={"llm_model": "openai/other-model"})
        self.assertNotEqual(identity, self.gateway.cache_arguments(self.messages)["cache_key"])

    async def test_real_http_failover_for_retryable_statuses(self):
        for status in (429, 500, 502, 503):
            self.statuses["test-key-one"] = status
            messages = [*self.messages, {"role": "user", "content": str(status)}]
            result = await self.gateway.resolve(messages, {})
            self.assertEqual((result.key_id, result.fallback_attempts), ("key_2", 1))
        self.assertEqual(len(self.requests), 8)

    async def test_fallback_response_warms_shared_cache(self):
        self.statuses["test-key-one"] = 429
        first = await self.gateway.resolve(self.messages, {})
        second = await self.gateway.resolve(self.messages, {})
        self.assertEqual(first.key_id, "key_2")
        self.assertEqual(second.source, "LLM_CACHE")
        self.assertEqual(len(self.requests), 2)

    async def test_deepseek_native_request_failover_and_cache(self):
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

        self.patcher.stop()
        native_completion = self.gateway.litellm.acompletion
        handler = AsyncHTTPHandler()
        await handler.client.aclose()
        handler.client = self.http
        settings = self.settings.model_copy(update={"llm_model": "deepseek/deepseek-flash"})
        gateway = Gateway(settings)
        self.statuses["test-key-one"] = 429

        async def complete(**kwargs):
            return await native_completion(**kwargs, client=handler)

        with patch.object(gateway.litellm, "acompletion", side_effect=complete):
            first = await gateway.resolve(self.messages, {})
            cached = await gateway.resolve(self.messages, {})

        self.assertEqual((first.key_id, first.fallback_attempts), ("key_2", 1))
        self.assertEqual(cached.source, "LLM_CACHE")
        self.assertEqual(
            (cached.input_tokens, cached.output_tokens), (first.input_tokens, first.output_tokens)
        )
        self.assertIsNone(cached.key_id)
        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["thinking"], {"type": "disabled"})

    async def test_mixed_providers_keep_fallback_model_in_cache(self):
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
        from openai import AsyncOpenAI

        self.patcher.stop()
        native_completion = self.gateway.litellm.acompletion
        handler = AsyncHTTPHandler()
        await handler.client.aclose()
        handler.client = self.http
        settings = self.settings.model_copy(
            update={
                "llm_models": "deepseek/deepseek-flash,groq/openai/gpt-oss-20b",
                "llm_api_base": "https://api.deepseek.com",
            }
        )
        gateway = Gateway(settings)
        self.statuses["test-key-one"] = 429
        groq_client = AsyncOpenAI(
            api_key="test-key-two",
            base_url="https://api.groq.com/openai/v1",
            http_client=self.http,
            max_retries=0,
        )

        async def complete(**kwargs):
            client = groq_client if kwargs["model"].startswith("openai/") else handler
            return await native_completion(**kwargs, client=client)

        with patch.object(gateway.litellm, "acompletion", side_effect=complete):
            first = await gateway.resolve(self.messages, {})
            cached = await gateway.resolve(self.messages, {})

        self.assertEqual((first.key_id, first.fallback_attempts), ("key_2", 1))
        self.assertEqual(first.model, "groq/openai/gpt-oss-20b")
        self.assertEqual(cached.model, first.model)
        self.assertEqual(cached.source, "LLM_CACHE")
        self.assertEqual(
            (cached.input_tokens, cached.output_tokens), (first.input_tokens, first.output_tokens)
        )
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(str(self.requests[0].url), "https://api.deepseek.com/chat/completions")
        self.assertEqual(
            str(self.requests[1].url), "https://api.groq.com/openai/v1/chat/completions"
        )
        body = json.loads(self.requests[1].content)
        self.assertEqual(body["model"], "openai/gpt-oss-20b")
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertNotIn("thinking", body)
        old_identity = gateway.cache_arguments(self.messages)["cache_key"]
        gateway.settings = settings.model_copy(
            update={
                "llm_models": "deepseek/deepseek-flash,groq/other-model",
            }
        )
        self.assertNotEqual(old_identity, gateway.cache_arguments(self.messages)["cache_key"])

    async def test_per_key_models_require_complete_configuration(self):
        for models in ("deepseek/deepseek-flash", "deepseek/deepseek-flash,"):
            with self.assertRaises(ValueError):
                Gateway(self.settings.model_copy(update={"llm_models": models}))

    async def test_simulation_bypasses_warm_cache_and_does_not_poison_it(self):
        await self.gateway.resolve(self.messages, {})
        with self.assertRaises(ProvidersUnavailable):
            await self.gateway.resolve(self.messages, {"*": 429})
        result = await self.gateway.resolve(self.messages, {"key_1": 500})
        self.assertEqual((result.key_id, result.fallback_attempts), ("key_2", 1))
        self.assertEqual((await self.gateway.resolve(self.messages, {})).source, "LLM_CACHE")
        self.assertEqual(len(self.requests), 2)

    async def test_simulated_reply_is_not_cached(self):
        await self.gateway.resolve(self.messages, {"key_1": 429})
        self.assertEqual((await self.gateway.resolve(self.messages, {})).source, "LIVE_INFERENCE")
        self.assertEqual(len(self.requests), 2)

    async def test_concurrent_failure_simulation_is_request_local(self):
        normal, failure = await asyncio.gather(
            self.gateway.resolve(self.messages, {}),
            self.gateway.resolve(self.messages, {"*": 429}),
            return_exceptions=True,
        )
        self.assertEqual(normal.key_id, "key_1")
        self.assertIsInstance(failure, ProvidersUnavailable)
        self.assertEqual(len(self.requests), 1)

    async def test_empty_provider_reply_never_succeeds(self):
        self.empty_reply = True
        with self.assertRaises(ProvidersUnavailable):
            await self.gateway.resolve(self.messages, {})
        self.assertEqual(len(self.requests), 2)

    async def test_missing_provider_usage_never_succeeds(self):
        self.missing_usage = True
        with self.assertRaises(ProvidersUnavailable):
            await self.gateway.resolve(self.messages, {})

    async def test_entire_attempt_timeout_is_bounded(self):
        self.gateway.settings = self.settings.model_copy(update={"llm_timeout_seconds": 0.001})
        with self.assertRaises(ProvidersUnavailable):
            await self.gateway.resolve(self.messages, {})

    async def test_unusable_key_pool_rejected(self):
        for keys in ("", "one", "one,one"):
            with self.assertRaises(ValueError):
                Gateway(self.settings.model_copy(update={"llm_api_keys": keys}))


if __name__ == "__main__":
    unittest.main()
