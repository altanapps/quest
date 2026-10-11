"""Tests for NEAR AI Cloud inference provider instances (kind ``nearai``).

Covers the registry entry and credential-file bootstrap in
``config/inference_providers.py`` (incl. the ``tee`` snapshot field), the
fixed-upstream endpoint resolution of ``OpenRouterProvider`` (NEAR base URL,
bearer key, the ``x-no-aliasing`` default header, no OpenRouter extras), the
catalog-based health check, model resolution / backend label / pricing
through the shared instance machinery, the catalog module
``chat/llm/nearai_catalog.py`` (normalization of the public ``/v1/models``
list merged with the paginated ``/v1/model/list`` TEE metadata, the
enclave/proxy classification, caching and error degradation against a
stubbed ``urllib``), and the admin endpoint behaviour (kinds listing,
per-instance catalog route with the chat-only filter, snapshots on add).
No network: every HTTP call is stubbed.

Modeled on ``tests/test_fireworks_inference.py``; catalog cases adapted from
the ``tests/test_near_provider.py`` suite of rchnbch/prusik-one (Apache-2.0).
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import urllib.error
import urllib.request

import pytest
from fastapi import HTTPException

import chat.llm.nearai_catalog as na
import config.inference_providers as ip
from chat.llm.openrouter_provider import OPENROUTER_BASE_URL, OpenRouterProvider

ADMIN_USER = {"id": 1, "email": "admin@example.com", "is_admin": True}
NEARAI_URL = "https://cloud-api.near.ai/v1"


def _run(coro):
    return asyncio.run(coro)


def _nearai(instance_id="nearai", models=()):
    return {"id": instance_id, "kind": "nearai", "label": "NEAR", "models": list(models)}


def _model(wire_id, *, name=None, context=131072, features=("tools",), prompt="0.0000006",
           completion="0.0000022", cache_read=None, max_out=32768):
    """One entry shaped like NEAR's ``GET /v1/models``."""
    pricing = {"prompt": prompt, "completion": completion}
    if cache_read is not None:
        pricing["input_cache_read"] = cache_read
    return {
        "id": wire_id, "object": "model", "owned_by": "near", "name": name,
        "context_length": context, "pricing": pricing,
        "top_provider": {"max_completion_tokens": max_out},
        "supported_features": list(features), "is_ready": True,
    }


def _meta(wire_id, *, provider_type="vllm", verifiable=True, attestation=True):
    """One item shaped like NEAR's ``GET /v1/model/list``."""
    return {
        "modelId": wire_id,
        "metadata": {
            "providerType": provider_type,
            "verifiable": verifiable,
            "attestationSupported": attestation,
        },
    }


def _catalog_entry(wire_id, name, *, context=131072, tee="enclave", capabilities=("tools",),
                   pricing=None):
    return {
        "id": wire_id, "name": name, "context_length": context, "max_completion_tokens": 32768,
        "pricing": pricing if pricing is not None else {"prompt": 0.6, "completion": 2.2},
        "detail": tee, "capabilities": list(capabilities), "tee": tee,
    }


# ---------------------------------------------------------------------------
# Registry + config store
# ---------------------------------------------------------------------------

def test_nearai_kind_registry_entry():
    spec = ip.INSTANCE_KINDS["nearai"]
    assert spec["provider"] == "openrouter" and spec["backend"] == "nearai"
    assert spec["key_required"] is True and spec["endpoint"] is False
    assert spec["upstream_url"] == NEARAI_URL and spec["catalog"] == "nearai"
    assert spec["label"] == "NEAR AI Cloud" and spec["hint"] == "sk-..."
    assert ip.is_endpoint_kind("nearai") is False


def test_nearai_instance_normalization_and_readiness():
    stored = ip.upsert_instance({
        "id": "nearai", "kind": "nearai", "label": "", "base_url": "http://x",
        "models": ["zai-org/GLM-5.1-FP8"],
    })
    assert stored["label"] == "NEAR AI Cloud"
    assert "base_url" not in stored and "api_type" not in stored
    assert stored["models"][0]["id"] == "zai-org/GLM-5.1-FP8"
    assert ip.instance_configured(ip.get_instance("nearai")) is False
    ip.write_inference_credentials("nearai", {"api_key": "sk-near"})
    assert ip.instance_configured(ip.get_instance("nearai")) is True


def test_kind_for_credential_file_and_bootstrap():
    assert ip.kind_for_credential_file("nearai") == "nearai"
    assert ip.kind_for_credential_file("nearai-2") == "nearai"
    assert ip.kind_for_credential_file("nearaix") == "openrouter"
    ip.write_inference_credentials("nearai", {
        "api_key": "sk-near",
        "models": ["zai-org/GLM-5.1-FP8", {"id": "deepseek-ai/DeepSeek-V3.2", "tee": "proxy"}],
    })
    instance = ip.get_instance("nearai")
    assert instance["kind"] == "nearai" and instance["label"] == "NEAR AI Cloud"
    assert [m["id"] for m in instance["models"]] == ["zai-org/GLM-5.1-FP8", "deepseek-ai/DeepSeek-V3.2"]
    assert "tee" not in instance["models"][0] and instance["models"][1]["tee"] == "proxy"


def test_normalize_instance_model_keeps_only_known_tee_labels():
    assert ip.normalize_instance_model({"id": "a", "tee": "enclave"})["tee"] == "enclave"
    assert ip.normalize_instance_model({"id": "a", "tee": "proxy"})["tee"] == "proxy"
    assert "tee" not in ip.normalize_instance_model({"id": "a", "tee": "secure"})
    assert "tee" not in ip.normalize_instance_model({"id": "a", "tee": None})
    assert "tee" not in ip.normalize_instance_model("a")


# ---------------------------------------------------------------------------
# Model resolution, backend label, pricing
# ---------------------------------------------------------------------------

def test_resolve_nearai_model():
    from chat.llm.config import (
        get_backend_for_model,
        get_configured_models,
        get_provider_for_model,
        resolve_model,
    )

    ip.upsert_instance(_nearai(models=[{
        "id": "zai-org/GLM-5.1-FP8", "enabled": True, "name": "GLM 5.1 FP8",
        "context_length": 131072, "max_completion_tokens": 32768,
        "pricing": {"prompt": 0.6, "completion": 2.2}, "tee": "enclave",
    }]))
    model_id = "nearai:zai-org/GLM-5.1-FP8"
    spec = resolve_model(model_id)
    assert spec is not None
    assert (spec.instance_id, spec.wire_id) == ("nearai", "zai-org/GLM-5.1-FP8")
    assert spec.provider == "openrouter" and spec.backend == "nearai"
    assert spec.display_name == "GLM 5.1 FP8" and spec.max_input_tokens == 131072
    assert spec.max_output_tokens == 32768 and spec.tee == "enclave"
    assert get_provider_for_model(model_id) == "openrouter"
    assert get_backend_for_model(model_id) == "nearai"
    assert ip.split_model_id(model_id) == ("nearai", "zai-org/GLM-5.1-FP8")
    assert ip.canonical_model_id(model_id) == model_id
    # Vertex and other instance specs carry no tee label
    assert resolve_model("claude-sonnet-5").tee is None
    assert model_id not in get_configured_models()
    ip.write_inference_credentials("nearai", {"api_key": "sk-near"})
    assert model_id in get_configured_models()


def test_nearai_pricing_uses_instance_snapshot():
    from db.llm_pricing import estimate_cost_usd

    ip.upsert_instance(_nearai(models=[{
        "id": "zai-org/GLM-5.1-FP8", "enabled": True,
        "pricing": {"prompt": 1.0, "completion": 2.0, "cache_read": 0.1},
    }]))
    cost = estimate_cost_usd(
        "openrouter", "nearai:zai-org/GLM-5.1-FP8",
        {"prompt_tokens": 1_000_000, "cached_prompt_tokens": 500_000, "completion_tokens": 1_000_000},
    )
    assert cost == pytest.approx(0.5 * 1.0 + 0.5 * 0.1 + 2.0)
    assert estimate_cost_usd("openrouter", "nearai:some/custom", {"prompt_tokens": 10}) is None


# ---------------------------------------------------------------------------
# Provider endpoint resolution + health check
# ---------------------------------------------------------------------------

def test_openrouter_provider_endpoint_for_nearai_instance():
    ip.upsert_instance(_nearai())
    provider = OpenRouterProvider("nearai")
    with pytest.raises(ValueError, match="NEAR AI Cloud API key not configured"):
        provider._endpoint()
    ip.write_inference_credentials("nearai", {"api_key": "sk-near"})
    endpoint = provider._endpoint()
    assert endpoint == {
        "base_url": NEARAI_URL,
        "api_key": "sk-near",
        "headers": {"x-no-aliasing": "true"},
        "openrouter": False,
    }
    client = provider._get_client()
    assert str(client.base_url).rstrip("/") == NEARAI_URL
    assert provider._is_openrouter is False
    assert client.default_headers.get("x-no-aliasing") == "true"
    # Other kinds are untouched
    ip.upsert_instance({"id": "openrouter", "kind": "openrouter", "models": []})
    ip.write_inference_credentials("openrouter", {"api_key": "sk-or"})
    other = OpenRouterProvider("openrouter")._endpoint()
    assert other["base_url"] == OPENROUTER_BASE_URL and other["headers"] == {"X-Title": "Quest"}


def test_streamed_request_carries_no_aliasing_header_and_no_openrouter_extras(monkeypatch):
    """What actually goes on the wire: the header on the request and no
    ``usage.include`` accounting opt-in in the body."""
    import httpx
    import openai

    ip.upsert_instance(_nearai(models=["zai-org/GLM-5.1-FP8"]))
    ip.write_inference_credentials("nearai", {"api_key": "sk-near"})
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        chunks = [
            {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "zai-org/GLM-5.1-FP8",
             "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hi"}, "finish_reason": None}]},
            {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "zai-org/GLM-5.1-FP8",
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
        ]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    provider = OpenRouterProvider("nearai")
    endpoint = provider._endpoint()
    provider._is_openrouter = endpoint["openrouter"]
    provider._client = openai.AsyncOpenAI(
        base_url=endpoint["base_url"], api_key=endpoint["api_key"],
        default_headers=endpoint["headers"],
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    session = provider.create_session("nearai:zai-org/GLM-5.1-FP8", "sys", tools=[])

    async def go():
        return [e async for e in provider.send_message_stream(session, "Say hello.")]

    events = _run(go())
    assert any(getattr(e, "text", None) == "hi" for e in events)
    assert captured["headers"]["x-no-aliasing"] == "true"
    assert captured["headers"]["authorization"] == "Bearer sk-near"
    assert captured["body"]["model"] == "zai-org/GLM-5.1-FP8"
    assert "usage" not in captured["body"]
    assert "sk-near" not in repr(provider)


def test_health_check_uses_catalog_not_a_completion(monkeypatch):
    calls: list[str] = []

    def fake_get_catalog(refresh=False):
        return {
            "models": [
                _catalog_entry("zai-org/GLM-5.1-FP8", "GLM"),
                _catalog_entry("baai/bge-m3", "BGE", capabilities=(), tee="proxy"),
            ],
            "fetched_at": time.time(), "stale": False, "error": None,
        }

    monkeypatch.setattr(na, "get_catalog", fake_get_catalog)
    ip.upsert_instance(_nearai(models=["zai-org/GLM-5.1-FP8"]))
    provider = OpenRouterProvider("nearai")

    class _NoCalls:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError("must not call the API")

    monkeypatch.setattr(provider, "_get_client", lambda: _NoCalls())
    # No key: reported like every other kind, before the catalog is consulted
    with pytest.raises(ValueError, match="NEAR AI Cloud API key not configured"):
        _run(provider.check_model_access("nearai:zai-org/GLM-5.1-FP8"))
    ip.write_inference_credentials("nearai", {"api_key": "sk-near"})
    _run(provider.check_model_access("nearai:zai-org/GLM-5.1-FP8"))
    with pytest.raises(ValueError, match="not in NEAR AI Cloud's catalog"):
        _run(provider.check_model_access("nearai:nope/missing"))
    with pytest.raises(ValueError, match="does not support tool calling"):
        _run(provider.check_model_access("nearai:baai/bge-m3"))
    assert calls == []
    # Catalog unreachable with nothing cached: that is the error
    monkeypatch.setattr(na, "get_catalog", lambda refresh=False: {
        "models": [], "fetched_at": None, "stale": True, "error": "dns down",
    })
    with pytest.raises(ValueError, match="NEAR AI catalog unavailable: dns down"):
        _run(provider.check_model_access("nearai:zai-org/GLM-5.1-FP8"))


def test_health_check_unchanged_for_other_kinds(monkeypatch):
    ip.upsert_instance({"id": "openrouter", "kind": "openrouter", "models": []})
    ip.write_inference_credentials("openrouter", {"api_key": "sk-or"})
    provider = OpenRouterProvider("openrouter")
    seen: list[dict] = []

    class _Completions:
        async def create(self, **kwargs):
            seen.append(kwargs)

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setattr(provider, "_get_client", lambda: _Client())
    _run(provider.check_model_access("openrouter:deepseek/x"))
    assert seen and seen[0]["model"] == "deepseek/x" and seen[0]["max_tokens"] == 1


def test_provider_singleton_per_nearai_instance(monkeypatch):
    import chat.llm.config as llm_config

    monkeypatch.setattr(llm_config, "_provider_instances", {})
    ip.upsert_instance(_nearai())
    provider = llm_config.get_provider_instance("openrouter", "nearai")
    assert isinstance(provider, OpenRouterProvider) and provider.instance_id == "nearai"
    assert provider is llm_config.get_provider_instance("openrouter", "nearai")
    assert provider is not llm_config.get_provider_instance("openrouter", None)


# ---------------------------------------------------------------------------
# Catalog module
# ---------------------------------------------------------------------------

def test_classify_entry_fails_closed():
    assert na.classify_entry({"provider_type": "vllm", "verifiable": True, "attestation_supported": True}) == "enclave"
    assert na.classify_entry({"provider_type": "external", "verifiable": True, "attestation_supported": True}) == "proxy"
    assert na.classify_entry({"provider_type": "chutes", "verifiable": True, "attestation_supported": True}) == "proxy"
    assert na.classify_entry({"provider_type": "vllm", "verifiable": False, "attestation_supported": True}) == "proxy"
    assert na.classify_entry({"provider_type": "vllm", "verifiable": True, "attestation_supported": None}) == "proxy"
    assert na.classify_entry({"provider_type": "vllm"}) == "proxy"
    assert na.classify_entry(None) == "proxy"


def test_normalize_entry_shape():
    tee = {"provider_type": "vllm", "verifiable": True, "attestation_supported": True}
    entry = na._normalize_entry(
        _model("zai-org/GLM-5.1-FP8", name="GLM 5.1 FP8", cache_read="0.0000001", features=("tools", "vision")),
        tee,
    )
    assert entry == {
        "id": "zai-org/GLM-5.1-FP8",
        "name": "GLM 5.1 FP8",
        "context_length": 131072,
        "max_completion_tokens": 32768,
        "pricing": {"prompt": pytest.approx(0.6), "completion": pytest.approx(2.2), "cache_read": pytest.approx(0.1)},
        "detail": "enclave · vision",
        "capabilities": ["tools", "vision"],
        "tee": "enclave",
    }
    # No metadata -> proxy; no name -> id; no tools -> not a chat model
    plain = na._normalize_entry(_model("baai/bge-m3", features=("embeddings",), prompt=None, completion=None), None)
    assert plain["name"] == "baai/bge-m3" and plain["tee"] == "proxy" and plain["detail"] == "proxy"
    assert plain["pricing"] is None and plain["capabilities"] == []
    assert na.is_chat_model(entry) is True and na.is_chat_model(plain) is False
    # ``max_output_length`` fallback for the output cap
    raw = _model("x/y", max_out=None)
    raw["max_output_length"] = 4096
    assert na._normalize_entry(raw)["max_completion_tokens"] == 4096
    assert na._normalize_entry("nope") is None
    assert na._normalize_entry({"object": "model"}) is None


@pytest.fixture
def fake_urlopen(monkeypatch):
    """Stub ``urllib.request.urlopen``: ``routes`` maps ``"models"`` to the
    ``/v1/models`` payload and an ``offset`` (int) to that ``/v1/model/list``
    page; an Exception value is raised instead. Records every request."""
    routes: dict[object, object] = {}
    seen: list[urllib.request.Request] = []

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    def urlopen(request, timeout=None):
        seen.append(request)
        from urllib.parse import parse_qs, urlparse
        if request.full_url.startswith(na.MODELS_URL):
            key: object = "models"
        else:
            key = int(parse_qs(urlparse(request.full_url).query).get("offset", ["0"])[0])
        target = routes.get(key, KeyError(f"unexpected request {request.full_url}"))
        if isinstance(target, Exception):
            raise target
        return _Response(json.dumps(target).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return routes, seen


def test_fetch_catalog_merges_lists_paginates_and_sends_no_key(fake_urlopen):
    routes, seen = fake_urlopen
    routes["models"] = {"data": [
        _model("b/model"),
        _model("a/model", features=("tools", "vision")),
        _model("c/embed", features=("embeddings",)),
        _model("a/model"),  # duplicate dropped
        "junk",
    ]}
    routes[0] = {"models": [_meta("a/model"), _meta("zzz/unlisted")], "total": 3}
    routes[2] = {"models": [_meta("b/model", provider_type="external")], "total": 3}
    models = na.fetch_catalog()
    assert [(m["id"], m["tee"]) for m in models] == [
        ("a/model", "enclave"), ("b/model", "proxy"), ("c/embed", "proxy"),
    ]
    assert models[0]["capabilities"] == ["tools", "vision"]
    assert [m["id"] for m in na.chat_models(models)] == ["a/model", "b/model"]
    assert len(seen) == 3
    for request in seen:
        assert request.get_header("Authorization") is None
        assert request.get_header("User-agent") == "Quest"
    assert seen[0].full_url == na.MODELS_URL
    assert "limit=500" in seen[1].full_url and "offset=0" in seen[1].full_url
    assert "offset=2" in seen[2].full_url


def test_fetch_catalog_survives_missing_metadata(fake_urlopen):
    routes, _seen = fake_urlopen
    routes["models"] = {"data": [_model("a/model")]}
    routes[0] = urllib.error.HTTPError(na.MODEL_LIST_URL, 503, "Unavailable", {}, io.BytesIO(b""))
    models = na.fetch_catalog()
    # Fail closed: without metadata every model is a proxy
    assert [(m["id"], m["tee"]) for m in models] == [("a/model", "proxy")]
    routes[0] = {"nope": []}
    assert na.fetch_catalog()[0]["tee"] == "proxy"


def test_fetch_catalog_errors(fake_urlopen):
    routes, _seen = fake_urlopen
    routes["models"] = urllib.error.HTTPError(na.MODELS_URL, 500, "Server Error", {}, io.BytesIO(b""))
    with pytest.raises(na.NearAICatalogError, match="HTTP 500"):
        na.fetch_catalog()
    routes["models"] = {"models": []}
    with pytest.raises(na.NearAICatalogError, match="Unexpected model list response shape"):
        na.fetch_catalog()
    routes["models"] = {"data": ["junk"]}
    routes[0] = {"models": [], "total": 0}
    with pytest.raises(na.NearAICatalogError, match="no models"):
        na.fetch_catalog()
    routes["models"] = urllib.error.URLError("dns down")
    with pytest.raises(na.NearAICatalogError, match="dns down"):
        na.fetch_catalog()


def test_get_catalog_caches_and_degrades(fake_urlopen, monkeypatch):
    routes, seen = fake_urlopen
    routes["models"] = {"data": [_model("a/model", name="A")]}
    routes[0] = {"models": [_meta("a/model")], "total": 1}
    first = na.get_catalog()
    assert first["stale"] is False and first["error"] is None
    assert [(m["id"], m["name"], m["tee"]) for m in first["models"]] == [("a/model", "A", "enclave")]
    assert na.NEARAI_CATALOG_FILE.exists()
    routes["models"] = AssertionError("must not fetch")
    cached = na.get_catalog()
    assert cached["models"] == first["models"] and cached["stale"] is False
    assert len(seen) == 2
    monkeypatch.setattr(na, "CATALOG_TTL_SECONDS", 0)
    routes["models"] = urllib.error.URLError("offline")
    stale = na.get_catalog()
    assert stale["stale"] is True and "offline" in stale["error"]
    assert [m["id"] for m in stale["models"]] == ["a/model"]
    na.NEARAI_CATALOG_FILE.unlink()
    empty = na.get_catalog()
    assert empty["models"] == [] and empty["stale"] is True and "offline" in empty["error"]
    monkeypatch.setattr(na, "CATALOG_TTL_SECONDS", 3600)
    routes["models"] = {"data": [_model("b/model")]}
    na.get_catalog()
    routes["models"] = {"data": [_model("c/model")]}
    assert [m["id"] for m in na.get_catalog()["models"]] == ["b/model"]
    assert [m["id"] for m in na.get_catalog(refresh=True)["models"]] == ["c/model"]


def test_cache_round_trip_keeps_shape(fake_urlopen):
    routes, _seen = fake_urlopen
    routes["models"] = {"data": [_model("a/model", features=("tools", "vision"))]}
    routes[0] = {"models": [_meta("a/model")], "total": 1}
    fetched = na.get_catalog()["models"]
    cached = na._read_cache()["models"]
    assert cached == fetched
    na._write_cache({"fetched_at": time.time(), "models": [
        {"id": "", "name": "x"}, "junk", {"id": "k/m", "tee": "secure"},
    ]})
    assert [(m["id"], m["tee"]) for m in na._read_cache()["models"]] == [("k/m", "proxy")]
    na.NEARAI_CATALOG_FILE.write_text("{not json")
    assert na._read_cache() is None


def test_search_and_snapshot_apply_to_nearai_entries():
    from chat.llm.openrouter_catalog import catalog_snapshot, search_catalog

    models = [
        _catalog_entry("zai-org/GLM-5.1-FP8", "GLM 5.1 FP8"),
        _catalog_entry("deepseek-ai/DeepSeek-V3.2", "DeepSeek V3.2", tee="proxy"),
    ]
    assert [m["id"] for m in search_catalog(models, "deepseek")] == ["deepseek-ai/DeepSeek-V3.2"]
    snapshot = catalog_snapshot(models, "zai-org/GLM-5.1-FP8")
    assert snapshot == {
        "name": "GLM 5.1 FP8", "context_length": 131072, "max_completion_tokens": 32768,
        "pricing": {"prompt": 0.6, "completion": 2.2}, "tee": "enclave",
    }
    # Entries without the field (OpenRouter, Fireworks) snapshot as before
    assert "tee" not in catalog_snapshot(
        [{"id": "x", "name": "X", "context_length": 1, "max_completion_tokens": None, "pricing": None}], "x",
    )
    assert catalog_snapshot(models, "nope") is None


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def admin(monkeypatch, tmp_path):
    import chat.llm.health as health
    import chat.routes.admin as admin_routes

    monkeypatch.setattr(admin_routes, "is_admin", lambda email: email == ADMIN_USER["email"])
    monkeypatch.setattr(health, "_store", health.ModelHealthStore(tmp_path / "model_health.json"))
    monkeypatch.setattr(health, "schedule_model_rechecks", lambda ids: None)
    return admin_routes


def test_admin_create_and_list_nearai_instance(admin):
    created = _run(admin.admin_create_inference_instance(
        admin.InstanceCreate(kind="nearai", label="Confidential"), user=ADMIN_USER,
    ))
    assert created["id"] == "nearai" and created["kind"] == "nearai"
    assert created["kind_label"] == "NEAR AI Cloud" and created["label"] == "Confidential"
    assert created["configured"] is False and created["key_required"] is True
    assert created["hint"] == "sk-..." and created["base_url"] is None and created["api_type"] is None
    listing = _run(admin.admin_list_inference_providers(user=ADMIN_USER))
    assert [i["id"] for i in listing["instances"]] == ["nearai"]
    kinds = {k["kind"]: k for k in listing["kinds"]}
    assert kinds["nearai"] == {"kind": "nearai", "label": "NEAR AI Cloud", "endpoint": False, "catalog": "nearai"}


def test_admin_update_nearai_models_snapshot_from_catalog_with_tee(admin, monkeypatch):
    calls: list[bool] = []

    def fake_get_catalog(refresh=False):
        calls.append(refresh)
        return {
            "models": [
                _catalog_entry("zai-org/GLM-5.1-FP8", "GLM 5.1 FP8"),
                _catalog_entry("deepseek-ai/DeepSeek-V3.2", "DeepSeek V3.2", tee="proxy"),
            ],
            "fetched_at": time.time(), "stale": False, "error": None,
        }

    monkeypatch.setattr(na, "get_catalog", fake_get_catalog)
    ip.upsert_instance(_nearai())
    ip.write_inference_credentials("nearai", {"api_key": "sk-near"})
    body = admin.InstanceUpdate(models=[
        admin.InstanceModelUpdate(id="zai-org/GLM-5.1-FP8"),
        admin.InstanceModelUpdate(id="deepseek-ai/DeepSeek-V3.2"),
        admin.InstanceModelUpdate(id="some/custom", name="Custom"),
    ])
    status = _run(admin.admin_update_inference_instance("nearai", body, user=ADMIN_USER))
    assert calls == [False]
    rows = {m["wire_id"]: m for m in status["models"]}
    assert rows["zai-org/GLM-5.1-FP8"]["display_name"] == "GLM 5.1 FP8"
    assert rows["zai-org/GLM-5.1-FP8"]["max_input_tokens"] == 131072
    assert rows["zai-org/GLM-5.1-FP8"]["id"] == "nearai:zai-org/GLM-5.1-FP8"
    assert rows["zai-org/GLM-5.1-FP8"]["tee"] == "enclave"
    assert rows["deepseek-ai/DeepSeek-V3.2"]["tee"] == "proxy"
    assert rows["some/custom"]["tee"] is None
    assert rows["some/custom"]["max_input_tokens"] == ip.DEFAULT_INSTANCE_CONTEXT_LENGTH
    stored = {m["id"]: m for m in ip.get_instance("nearai")["models"]}
    assert stored["zai-org/GLM-5.1-FP8"]["pricing"] == {"prompt": 0.6, "completion": 2.2}
    assert stored["zai-org/GLM-5.1-FP8"]["tee"] == "enclave"
    assert "tee" not in stored["some/custom"] and stored["some/custom"]["pricing"] is None
    assert ip.instance_model_pricing("zai-org/GLM-5.1-FP8") == {"prompt": 0.6, "completion": 2.2}
    # Vertex rows carry the field too, as null
    listing = _run(admin.admin_list_inference_providers(user=ADMIN_USER))
    assert all(row["tee"] is None for row in listing["vertex"]["models"])


def test_admin_instance_catalog_for_nearai(admin, monkeypatch):
    calls: list[bool] = []

    def fake_get_catalog(refresh=False):
        calls.append(refresh)
        return {
            "models": [
                _catalog_entry("zai-org/GLM-5.1-FP8", "GLM"),
                _catalog_entry("deepseek-ai/DeepSeek-V3.2", "DeepSeek", tee="proxy"),
                _catalog_entry("baai/bge-m3", "BGE embeddings", capabilities=(), tee="proxy"),
            ],
            "fetched_at": time.time(), "stale": True, "error": "HTTP 503",
        }

    monkeypatch.setattr(na, "get_catalog", fake_get_catalog)
    ip.upsert_instance(_nearai())
    # Public catalog: works before a key is saved, chat models only
    result = _run(admin.admin_instance_catalog("nearai", user=ADMIN_USER))
    assert [m["id"] for m in result["models"]] == ["zai-org/GLM-5.1-FP8", "deepseek-ai/DeepSeek-V3.2"]
    assert result["models"][0]["tee"] == "enclave" and result["error"] == "HTTP 503"
    assert calls == [False]
    result = _run(admin.admin_instance_catalog("nearai", q="bge", all=True, refresh=True, user=ADMIN_USER))
    assert [m["id"] for m in result["models"]] == ["baai/bge-m3"]
    assert calls == [False, True]
    with pytest.raises(HTTPException) as exc_info:
        _run(admin.admin_instance_catalog("nearai", user={"id": 2, "email": "x@y", "is_admin": False}))
    assert exc_info.value.status_code == 403


def test_admin_delete_nearai_instance_removes_key_file(admin):
    ip.upsert_instance(_nearai(models=["zai-org/GLM-5.1-FP8"]))
    ip.write_inference_credentials("nearai", {"api_key": "sk-near"})
    assert _run(admin.admin_delete_inference_instance("nearai", user=ADMIN_USER)) == {"success": True}
    assert ip.get_instance("nearai") is None
    assert ip.read_inference_credentials("nearai") is None
    assert ip.list_instances() == []
