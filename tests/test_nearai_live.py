"""Live NEAR AI Cloud smoke check. Opt-in and cheap:

    QUEST_NEARAI_TEST_KEY=sk-... uv run pytest tests/test_nearai_live.py -q

Skipped whenever ``QUEST_NEARAI_TEST_KEY`` is unset, so it never runs in CI
or in a bare ``uv run pytest``. It fetches the public catalog, completes one
short streamed turn on the first enclave-labelled chat model (or
``QUEST_NEARAI_TEST_MODEL``) and asserts the request carried the
``x-no-aliasing`` header. The key value is never printed: assertions compare
headers by name only.
"""

from __future__ import annotations

import asyncio
import os

import pytest

KEY_ENV = "QUEST_NEARAI_TEST_KEY"
MODEL_ENV = "QUEST_NEARAI_TEST_MODEL"


@pytest.fixture(autouse=True)
def _require_key(tmp_path, monkeypatch):
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        pytest.skip(f"{KEY_ENV} not set")
    import config.inference_providers as ip

    ip.upsert_instance({"id": "nearai", "kind": "nearai", "label": "NEAR", "models": []})
    ip.write_inference_credentials("nearai", {"api_key": key})


def test_live_catalog_and_one_streamed_turn():
    import httpx
    import openai

    import chat.llm.nearai_catalog as na
    from chat.llm.openrouter_provider import OpenRouterProvider

    catalog = na.get_catalog(refresh=True)
    assert catalog["error"] is None, catalog["error"]
    chat = na.chat_models(catalog["models"])
    assert chat, "NEAR catalog listed no tool-capable models"
    enclave = [m for m in chat if m["tee"] == "enclave"]
    print(f"NEAR catalog: {len(catalog['models'])} models, {len(chat)} tool-capable, {len(enclave)} enclave")
    wire_id = os.environ.get(MODEL_ENV, "").strip() or (enclave or chat)[0]["id"]

    seen_headers: list[httpx.Headers] = []

    async def record(request: httpx.Request) -> None:
        seen_headers.append(request.headers)

    provider = OpenRouterProvider("nearai")
    endpoint = provider._endpoint()
    provider._is_openrouter = endpoint["openrouter"]
    provider._client = openai.AsyncOpenAI(
        base_url=endpoint["base_url"], api_key=endpoint["api_key"],
        default_headers=endpoint["headers"],
        http_client=httpx.AsyncClient(event_hooks={"request": [record]}, timeout=60),
    )
    session = provider.create_session(f"nearai:{wire_id}", "Reply with one short sentence.", tools=[])

    async def go():
        return [e async for e in provider.send_message_stream(session, "Say hello.")]

    events = asyncio.run(go())
    text = "".join(getattr(e, "text", "") or "" for e in events)
    print(f"NEAR {wire_id}: {text[:80]!r}")
    assert text.strip()
    assert seen_headers and seen_headers[0].get("x-no-aliasing") == "true"
    assert session.last_usage.get("prompt_tokens")
