"""API and SQLite contracts. Run with: python -m unittest discover -s tests -v."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from app.config import Settings
from app.database import Database
from app.gateway import ProvidersUnavailable, Resolution, parse_simulation, response_values
from app.main import create_app


class ControlledGateway:
    """API tests isolate inference; native LiteLLM is exercised in test_gateway.py."""

    keys = ["test-secret-one", "test-secret-two"]

    def __init__(self):
        self.calls = []
        self.delay = 0

    async def resolve(self, messages, failures):
        self.calls.append((messages, failures))
        await asyncio.sleep(self.delay)
        failed = [key for key in ("key_1", "key_2") if failures.get(key, failures.get("*"))]
        if len(failed) == 2:
            raise ProvidersUnavailable
        return Resolution(
            "A useful answer.",
            "openai/test-model",
            len(json.dumps(messages)),
            7,
            "LIVE_INFERENCE",
            "key_2" if "key_1" in failed else "key_1",
            len(failed),
        )


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(
            _env_file=None,
            llm_api_keys="test-secret-one,test-secret-two",
            database_url=f"sqlite:///{self.temporary.name}/app.db",
            enable_failure_simulation=True,
        )
        self.gateway = ControlledGateway()
        self.app = create_app(self.settings, self.gateway)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.temporary.cleanup()

    async def chat(self, session="test", message="hello", **kwargs):
        return await self.client.post(
            "/chat", json={"session_id": session, "message": message, **kwargs}
        )

    async def test_health_and_unknown_history(self):
        self.assertEqual(
            (await self.client.get("/health")).json(),
            {
                "status": "ok",
                "keys_configured": 2,
                "cache_enabled": True,
            },
        )
        response = await self.client.get("/sessions/unknown/messages")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "session not found"})

    async def test_valid_reply_and_metadata(self):
        response = await self.chat(message="  hello  ")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["reply"], "A useful answer.")
        self.assertEqual(
            set(data["metadata"]),
            {
                "resolution_source",
                "model",
                "latency_ms",
                "input_tokens",
                "output_tokens",
                "key_id",
                "fallback_attempts",
            },
        )
        for key in ("latency_ms", "input_tokens", "output_tokens", "fallback_attempts"):
            self.assertIs(type(data["metadata"][key]), int)
        self.assertEqual(self.gateway.calls[0][0][-1]["content"], "hello")
        self.assertNotIn("test-secret", response.text)

    async def test_invalid_bodies_never_save(self):
        invalid = [
            {},
            {"session_id": "s"},
            {"message": "x"},
            {"session_id": "", "message": "x"},
            {"session_id": "  ", "message": "x"},
            {"session_id": "s" * 129, "message": "x"},
            {"session_id": 7, "message": "x"},
            {"session_id": True, "message": "x"},
            {"session_id": "test", "message": 3},
            {"session_id": "test", "message": None},
            {"session_id": "test", "message": ""},
            {"session_id": "test", "message": " \n "},
            {"session_id": "test", "message": "x" * 4001},
            {"session_id": "test", "message": "x", "system_prompt": 5},
        ]
        for body in invalid:
            with self.subTest(body=str(body)[:70]):
                response = await self.client.post("/chat", json=body)
                self.assertEqual(response.status_code, 422)
                self.assertIn("detail", response.json())
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual((await self.client.get("/sessions/test/messages")).status_code, 404)

    async def test_malformed_json_is_422(self):
        response = await self.client.post(
            "/chat", content='{"session_id":', headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("detail", response.json())

    async def test_trimmed_length_boundaries(self):
        response = await self.chat(session="s" * 128, message=" " + "x" * 4000 + " ")
        self.assertEqual(response.status_code, 200)

    async def test_session_context_and_usage(self):
        await self.chat(message="My city is Kyoto.")
        second = await self.chat(message="What city did I mention?")
        messages = self.gateway.calls[1][0]
        self.assertEqual(
            [item["role"] for item in messages],
            [
                "system",
                "user",
                "assistant",
                "user",
            ],
        )
        self.assertIn("Kyoto", messages[1]["content"])
        self.assertGreater(second.json()["metadata"]["input_tokens"], 0)
        await self.chat(session="separate", message="What city?")
        self.assertEqual(len(self.gateway.calls[2][0]), 2)

    async def test_history_order_and_metadata(self):
        first = await self.chat(message="first")
        await self.chat(message="second")
        history = (await self.client.get("/sessions/test/messages")).json()["messages"]
        self.assertEqual(
            [item["role"] for item in history],
            [
                "user",
                "assistant",
                "user",
                "assistant",
            ],
        )
        self.assertEqual(history[0]["content"], "first")
        self.assertEqual(history[1]["metadata"], first.json()["metadata"])

    async def test_simulation_success_for_all_supported_statuses(self):
        for status in (429, 500, 502, 503):
            with self.subTest(status=status):
                response = await self.client.post(
                    "/chat",
                    json={
                        "session_id": f"s{status}",
                        "message": "hello",
                    },
                    headers={"X-Simulate-Failure": f"key_1={status}"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["metadata"]["key_id"], "key_2")
                self.assertEqual(response.json()["metadata"]["fallback_attempts"], 1)

    async def test_all_failed_preserves_history_and_prompt(self):
        await self.chat(system_prompt="original")
        before = (await self.client.get("/sessions/test/messages")).json()
        for session, header in (("test", "*=429"), ("unknown", "key_1=429,key_2=500")):
            response = await self.client.post(
                "/chat",
                json={
                    "session_id": session,
                    "message": "failed",
                    "system_prompt": "changed",
                },
                headers={"X-Simulate-Failure": header},
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "all providers unavailable"})
        self.assertEqual((await self.client.get("/sessions/test/messages")).json(), before)
        self.assertEqual((await self.client.get("/sessions/unknown/messages")).status_code, 404)
        await self.chat(message="next")
        self.assertEqual(self.gateway.calls[-1][0][0]["content"], "original")

    async def test_system_prompt_persists_and_can_be_empty(self):
        await self.chat(system_prompt="Use bullet points")
        await self.chat(message="next")
        self.assertEqual(self.gateway.calls[-1][0][0]["content"], "Use bullet points")
        await self.chat(message="again", system_prompt="")
        await self.chat(message="last")
        self.assertEqual(self.gateway.calls[-1][0][0]["content"], "")

    async def test_invalid_simulation_no_history(self):
        for header in ("key_9=429", "key_1=401", "*=oops", "key_1", "", "*=429,", "*=429,*=500"):
            response = await self.client.post(
                "/chat",
                json={
                    "session_id": "test",
                    "message": "hello",
                },
                headers={"X-Simulate-Failure": header},
            )
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.gateway.calls, [])

    async def test_disabled_simulation_is_ignored(self):
        app = create_app(
            self.settings.model_copy(update={"enable_failure_simulation": False}), self.gateway
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/chat",
                    json={"session_id": "test", "message": "hi"},
                    headers={"X-Simulate-Failure": "invalid"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.gateway.calls[-1][1], {})

    async def test_concurrent_same_session_order_and_lock_cleanup(self):
        self.gateway.delay = 0.02
        responses = await asyncio.gather(self.chat(message="one"), self.chat(message="two"))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual([len(call[0]) for call in self.gateway.calls], [2, 4])
        self.assertEqual(self.app.state.database._locks, {})

    async def test_other_sessions_run_concurrently_and_simulation_is_isolated(self):
        self.gateway.delay = 0.03
        await asyncio.gather(
            self.chat(session="normal"),
            self.client.post(
                "/chat",
                json={
                    "session_id": "failing",
                    "message": "hi",
                },
                headers={"X-Simulate-Failure": "*=429"},
            ),
        )
        self.assertEqual((await self.client.get("/sessions/normal/messages")).status_code, 200)
        self.assertEqual((await self.client.get("/sessions/failing/messages")).status_code, 404)

    async def test_legacy_minimal_schema_metadata(self):
        async with self.app.state.database.connect() as connection:
            await connection.execute(
                "INSERT INTO messages(session_id,role,content,metadata,created_at) "
                "VALUES(?,?,?,?,?)",
                (
                    "legacy",
                    "assistant",
                    "Paris",
                    '{"input_tokens":42,"output_tokens":9}',
                    "2026-11-21",
                ),
            )
            await connection.commit()
        response = await self.client.get("/sessions/legacy/messages")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["messages"][0]["metadata"],
            {
                "input_tokens": 42,
                "output_tokens": 9,
            },
        )
        await self.chat(session="legacy")
        self.assertEqual(self.gateway.calls[-1][0][1]["content"], "Paris")

    async def test_persistence_after_new_database_instance(self):
        await self.chat(message="persistent")
        reopened = Database(self.settings.database_path)
        await reopened.initialize()
        _, messages = await reopened.session("test")
        self.assertEqual(messages[0].content, "persistent")

    async def test_transaction_rolls_back_both_messages_and_session(self):
        database = self.app.state.database
        async with database.connect() as connection:
            await connection.execute("""
                CREATE TRIGGER reject_assistant BEFORE INSERT ON messages
                WHEN NEW.role = 'assistant' BEGIN SELECT RAISE(ABORT, 'rejected'); END
            """)
            await connection.commit()
        response = await self.chat()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "internal server error"})
        self.assertEqual(await database.session("test"), (None, []))
        self.assertEqual(database._locks, {})

    async def test_exact_cors_origin_and_header(self):
        response = await self.client.options(
            "/chat",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-simulate-failure",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:3000")
        response = await self.client.options(
            "/chat",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertNotIn("access-control-allow-origin", response.headers)


class HelpersTests(unittest.TestCase):
    def test_parser_specific_overrides_wildcard(self):
        self.assertEqual(parse_simulation("*=429, key_1=500", 2), {"*": 429, "key_1": 500})

    def test_invalid_provider_content_and_usage(self):
        for content, usage in [
            ("", {"prompt_tokens": 3, "completion_tokens": 1}),
            ("hello", {}),
            ("hello", {"prompt_tokens": None, "completion_tokens": 1}),
            ("hello", {"prompt_tokens": -1, "completion_tokens": 1}),
        ]:
            with self.assertRaises(ValueError):
                response_values({"choices": [{"message": {"content": content}}], "usage": usage})

    def test_database_url_and_key_parsing(self):
        settings = Settings(
            _env_file=None, llm_api_keys=" a , b ", database_url="sqlite:////data/app.db"
        )
        self.assertEqual(settings.keys, ["a", "b"])
        self.assertEqual(settings.database_path, Path("/data/app.db"))
        with self.assertRaises(ValueError):
            Settings(_env_file=None, database_url="postgres://server/db").database_path


if __name__ == "__main__":
    unittest.main()
