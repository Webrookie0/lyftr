"""Verify fresh-session cache hits and history-sensitive misses against a running API."""

import argparse
import sys
import uuid
from urllib.error import URLError

from verify_live import base, chat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--message",
        default="can you tell me what's the name of first president of america ?",
    )
    message = parser.parse_args().message
    run = uuid.uuid4().hex[:12]
    first_session, fresh_session = f"cache-first-{run}", f"cache-fresh-{run}"
    prompt = f"You are a helpful assistant. Answer concisely. Cache verification reference: {run}."

    status, first = chat(first_session, message, system_prompt=prompt)
    assert status == 200, first
    status, cached = chat(fresh_session, message, system_prompt=prompt)
    assert status == 200, cached
    first_meta, cached_meta = first["metadata"], cached["metadata"]
    assert first_meta["resolution_source"] == "LIVE_INFERENCE", first_meta
    assert cached_meta["resolution_source"] == "LLM_CACHE", cached_meta
    assert first["reply"] == cached["reply"]
    for field in ("model", "input_tokens", "output_tokens"):
        assert first_meta[field] == cached_meta[field], field
    assert cached_meta["key_id"] is None and cached_meta["fallback_attempts"] == 0
    print(f"First fresh chat: {first_meta['resolution_source']} · {first_meta['latency_ms']} ms")
    print(f"Second fresh chat: {cached_meta['resolution_source']} · {cached_meta['latency_ms']} ms")
    print("PASS identical first prompts in fresh chats reuse reply and token usage")

    status, repeated = chat(first_session, message)
    assert status == 200, repeated
    assert repeated["metadata"]["resolution_source"] == "LIVE_INFERENCE", repeated
    print(f"Repeat inside first chat: {repeated['metadata']['resolution_source']}")
    print("PASS changed conversation history gets a separate cache entry")
    print("To see LLM_CACHE in the UI, repeat the exact first prompt in a new conversation.")


if __name__ == "__main__":
    try:
        main()
    except URLError as error:
        print(f"Cannot reach {base}: {error.reason}", file=sys.stderr)
        raise SystemExit(1) from None
    except AssertionError as error:
        print(f"Cache verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
