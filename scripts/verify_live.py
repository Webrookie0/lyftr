"""Verify a running backend using real providers. Standard library only."""

import json
import os
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

base = os.environ.get("LYFTR_API_URL", "http://localhost:8000").rstrip("/")
run = uuid.uuid4().hex[:12]


def request(path, body=None, failure=None):
    headers = {"Content-Type": "application/json"}
    if failure:
        headers["X-Simulate-Failure"] = failure
    req = Request(base + path, data=json.dumps(body).encode() if body else None, headers=headers)
    try:
        with urlopen(req, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def chat(session, message, failure=None, **extra):
    return request("/chat", {"session_id": session, "message": message, **extra}, failure)


def main():
    status, health = request("/health")
    assert status == 200 and health["keys_configured"] >= 2 and health["cache_enabled"]
    print("PASS health, key pool, cache")
    prompt = f"What is the capital of France? Answer in one sentence. Check reference {run}."
    session = f"verify-{run}"
    status, first = chat(session, prompt)
    assert status == 200, first
    assert first["reply"].strip() and first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    print(f"Live model: {first['metadata']['model']} via {first['metadata']['key_id']}")
    status, cached = chat(f"cache-{run}", prompt)
    assert status == 200 and cached["metadata"]["resolution_source"] == "LLM_CACHE", cached
    assert cached["reply"] == first["reply"]
    assert cached["metadata"]["model"] == first["metadata"]["model"]
    for field in ("input_tokens", "output_tokens"):
        assert cached["metadata"][field] == first["metadata"][field]
    assert cached["metadata"]["key_id"] is None and cached["metadata"]["fallback_attempts"] == 0
    assert cached["metadata"]["latency_ms"] < first["metadata"]["latency_ms"], (first, cached)
    print("PASS immediate native cache, identical usage, lower server latency")
    for status_code in (429, 500):
        status, result = chat(f"fallback-{status_code}-{run}", prompt, f"key_1={status_code}")
        assert status == 200, result
        assert result["metadata"]["key_id"] == "key_2"
        assert result["metadata"]["fallback_attempts"] == 1
    print(f"PASS real answer through key_2 after simulated 429 and 500 ({result['metadata']['model']})")
    _, before = request(f"/sessions/{session}/messages")
    status, result = chat(session, prompt, "*=429")
    assert status == 503 and result == {"detail": "all providers unavailable"}
    assert request(f"/sessions/{session}/messages")[1] == before
    print("PASS warmed-cache failure simulation and unchanged history")
    status, result = chat(f"memory-{run}", "Remember my favorite city is Kyoto.")
    assert status == 200, result
    status, result = chat(f"memory-{run}", "What is my favorite city? Answer with only its name.")
    assert status == 200 and "kyoto" in result["reply"].lower(), result
    status, history = request(f"/sessions/memory-{run}/messages")
    assert status == 200 and [message["role"] for message in history["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    print("PASS real multi-turn memory and ordered stored history")
    print(f"Persistence check: restart the backend, then GET {base}/sessions/{session}/messages")


if __name__ == "__main__":
    try:
        main()
    except URLError as error:
        print(f"Cannot reach {base}: {error.reason}", file=sys.stderr)
        raise SystemExit(1) from None
    except AssertionError as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
