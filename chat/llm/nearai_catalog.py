"""Cached NEAR AI Cloud model catalog for the admin typeahead and metadata
snapshots.

NEAR AI Cloud (https://cloud.near.ai) serves open-weight models behind an
OpenAI-compatible chat-completions API (``https://cloud-api.near.ai/v1``;
the ``nearai`` instance kind in config/inference_providers.py). Its selling
point is confidential inference: models run inside hardware-attested
enclaves (Intel TDX VMs with NVIDIA TEE GPUs, TLS terminated inside the
enclave). The catalog is built from two PUBLIC endpoints -- no key is sent,
so a catalog refresh can never leak one and the typeahead works before the
admin has saved a key:

- ``GET /v1/models`` (OpenAI-style ``{"data": [...]}``) lists every model
  with ``context_length``, OpenRouter-style ``pricing.prompt`` /
  ``pricing.completion`` in $/token (converted to $/1M here), an optional
  ``pricing.input_cache_read``, an output cap (``top_provider.
  max_completion_tokens`` or ``max_output_length``) and a
  ``supported_features`` list. Only models whose features include
  ``"tools"`` are chat models Quest can drive (:func:`is_chat_model`):
  that drops embeddings, rerankers, speech, image and classifier models.
  They stay in the cache so a custom-id check can still name them.
- ``GET /v1/model/list`` (paginated ``{"models": [...], "total": n}``)
  carries each model's TEE metadata: ``metadata.providerType`` (``"vllm"``
  = NEAR's own TEE fleet, ``"external"`` / ``"chutes"`` = relayed to a
  third party), ``metadata.verifiable`` and ``metadata.attestationSupported``.
  :func:`classify_entry` reduces them to one ``tee`` label: ``"enclave"``
  only when all three say so, ``"proxy"`` for everything else -- including
  a missing field, a model absent from the list or a failed fetch (fail
  closed). The label is informational (a badge in Settings); nothing
  enforces it and no attestation is verified (see the inference-providers
  doc for what that would take).

Settings > Inference Providers uses the result the same two ways as the
Fireworks catalog: the "Add model" typeahead on a NEAR card searches it
(``GET /admin/inference-providers/instances/<id>/catalog?q=``, chat models
only unless ``all=true``), and when a model is added the admin endpoint
snapshots its metadata (:func:`openrouter_catalog.catalog_snapshot`, which
passes the ``tee`` label through) into the instance config so nothing at
request time depends on the catalog. ``OpenRouterProvider.check_model_access``
also consults it for NEAR instances: the health sweep verifies catalog
membership instead of billing a completion per model (NEAR documents no
rate limits and lists ~40 chat models).

The normalized list is cached in ``data/nearai_catalog.json`` for
:data:`CATALOG_TTL_SECONDS` (same for every key, so one cache serves every
NEAR instance); a failed fetch keeps serving the stale cache -- or an empty
list when there never was one -- with the error reported alongside, so the
UI falls back to custom-id entry.

Adapted from the ``config/near_catalog.py`` module of rchnbch/prusik-one
(Apache-2.0), a Quest fork that integrated NEAR first.
"""

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request

from config.paths import NEARAI_CATALOG_FILE

logger = logging.getLogger(__name__)

NEARAI_BASE_URL = "https://cloud-api.near.ai/v1"
MODELS_URL = f"{NEARAI_BASE_URL}/models"
MODEL_LIST_URL = f"{NEARAI_BASE_URL}/model/list"
# Largest page the metadata list allows; one page holds the whole catalog
# today. A bound on pages keeps a malformed ``total`` from looping forever.
MODEL_LIST_PAGE_SIZE = 500
MAX_PAGES = 20
CATALOG_TTL_SECONDS = 24 * 60 * 60
FETCH_TIMEOUT_SECONDS = 15
_PER_MILLION = 1_000_000

TEE_ENCLAVE = "enclave"
TEE_PROXY = "proxy"
TEE_LABELS = (TEE_ENCLAVE, TEE_PROXY)
# NEAR's providerType for models served on its own TEE fleet.
TEE_PROVIDER_TYPE = "vllm"
TOOLS_FEATURE = "tools"


class NearAICatalogError(RuntimeError):
    """The catalog could not be fetched (network, unexpected shape)."""


def _rate_per_million(value) -> float | None:
    """NEAR, like OpenRouter, quotes ``prompt``/``completion`` in $/token."""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate < 0:
        return None
    return rate * _PER_MILLION


def _positive_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


def _bool_or_none(value) -> bool | None:
    return value if isinstance(value, bool) else None


def classify_entry(tee_fields: dict | None) -> str:
    """``"enclave"`` or ``"proxy"`` from one model's TEE metadata
    (``provider_type`` / ``verifiable`` / ``attestation_supported``).

    Enclave only on NEAR's positive signal in all three fields; every
    other case, including missing metadata, is ``"proxy"`` (fail closed).
    """
    if not isinstance(tee_fields, dict):
        return TEE_PROXY
    if (
        tee_fields.get("provider_type") == TEE_PROVIDER_TYPE
        and tee_fields.get("verifiable") is True
        and tee_fields.get("attestation_supported") is True
    ):
        return TEE_ENCLAVE
    return TEE_PROXY


def is_chat_model(entry: dict) -> bool:
    """Quest's agent loop needs tool calling: only models NEAR lists with
    ``tools`` among their supported features are offered by default."""
    return TOOLS_FEATURE in (entry.get("capabilities") or [])


def _normalize_entry(raw, tee: dict | None = None) -> dict | None:
    """Normalize one ``/v1/models`` entry into the OpenRouter catalog row
    shape (+ ``capabilities`` / ``detail`` like Fireworks and self-hosted
    discovery, + ``tee``); None when unusable.

    ``tee`` is the model's entry from :func:`fetch_tee_metadata` (None when
    the metadata list did not name it -> ``"proxy"``).
    """
    if not isinstance(raw, dict):
        return None
    wire_id = raw.get("id")
    if not isinstance(wire_id, str) or not wire_id.strip():
        return None
    wire_id = wire_id.strip()
    pricing_raw = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    top = raw.get("top_provider") if isinstance(raw.get("top_provider"), dict) else {}
    pricing = None
    prompt = _rate_per_million(pricing_raw.get("prompt"))
    completion = _rate_per_million(pricing_raw.get("completion"))
    if prompt is not None and completion is not None:
        pricing = {"prompt": prompt, "completion": completion}
        cache_read = _rate_per_million(pricing_raw.get("input_cache_read"))
        if cache_read is not None:
            pricing["cache_read"] = cache_read
    features = _str_list(raw.get("supported_features"))
    # Same convention as Fireworks / self-hosted discovery: the typeahead
    # flags a model whose capabilities lack "tools".
    capabilities: list[str] = []
    if TOOLS_FEATURE in features:
        capabilities.append(TOOLS_FEATURE)
    if "vision" in features or "image_input" in features:
        capabilities.append("vision")
    tee_label = classify_entry(tee)
    bits = [tee_label]
    if "vision" in capabilities:
        bits.append("vision")
    name = raw.get("name")
    return {
        "id": wire_id,
        "name": name.strip() if isinstance(name, str) and name.strip() else wire_id,
        "context_length": _positive_int(raw.get("context_length")),
        "max_completion_tokens": (
            _positive_int(top.get("max_completion_tokens"))
            or _positive_int(raw.get("max_output_length"))
        ),
        "pricing": pricing,
        "detail": " · ".join(bits),
        "capabilities": capabilities,
        "tee": tee_label,
    }


def _cached_entry(raw) -> dict | None:
    """Validate one ALREADY-normalized cache entry (prices are per-1M)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
        return None
    pricing = raw.get("pricing")
    if not (isinstance(pricing, dict) and "prompt" in pricing and "completion" in pricing):
        pricing = None
    name = raw.get("name")
    capabilities = raw.get("capabilities")
    tee = raw.get("tee")
    return {
        "id": raw["id"],
        "name": name if isinstance(name, str) and name else raw["id"],
        "context_length": _positive_int(raw.get("context_length")),
        "max_completion_tokens": _positive_int(raw.get("max_completion_tokens")),
        "pricing": pricing,
        "detail": raw.get("detail") if isinstance(raw.get("detail"), str) else "",
        "capabilities": [c for c in capabilities if isinstance(c, str)] if isinstance(capabilities, list) else [],
        "tee": tee if tee in TEE_LABELS else TEE_PROXY,
    }


def _read_cache() -> dict | None:
    try:
        with open(NEARAI_CATALOG_FILE, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable NEAR AI catalog cache: %s", NEARAI_CATALOG_FILE)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return None
    fetched_at = data.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    models = [m for m in (_cached_entry(x) for x in data["models"]) if m]
    return {"fetched_at": float(fetched_at), "models": models}


def _write_cache(cache: dict) -> None:
    NEARAI_CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=NEARAI_CATALOG_FILE.parent, prefix=".nearai_catalog.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cache, f)
        os.replace(tmp_path, NEARAI_CATALOG_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _get_json(url: str):
    """GET a public ``url`` (no credentials); raises
    :class:`NearAICatalogError` with the status or the error text."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Quest"},
    )
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise NearAICatalogError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        raise NearAICatalogError(str(exc) or exc.__class__.__name__) from exc


def fetch_models() -> list[dict]:
    """The raw ``/v1/models`` entries (raises on failure)."""
    payload = _get_json(MODELS_URL)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise NearAICatalogError("Unexpected model list response shape")
    return [m for m in data if isinstance(m, dict)]


def _tee_from_list_item(item) -> tuple[str, dict] | None:
    if not isinstance(item, dict):
        return None
    model_id = item.get("modelId")
    meta = item.get("metadata")
    if not isinstance(model_id, str) or not model_id or not isinstance(meta, dict):
        return None
    provider_type = meta.get("providerType")
    return model_id, {
        "provider_type": provider_type if isinstance(provider_type, str) else None,
        "verifiable": _bool_or_none(meta.get("verifiable")),
        "attestation_supported": _bool_or_none(meta.get("attestationSupported")),
    }


def fetch_tee_metadata() -> dict[str, dict]:
    """``{wire_id: {provider_type, verifiable, attestation_supported}}``
    from the public ``/v1/model/list`` (raises on failure). Follows
    ``offset`` pagination until ``total`` is reached or the list ends."""
    out: dict[str, dict] = {}
    offset = 0
    for _ in range(MAX_PAGES):
        payload = _get_json(f"{MODEL_LIST_URL}?limit={MODEL_LIST_PAGE_SIZE}&offset={offset}")
        items = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise NearAICatalogError("Unexpected model metadata response shape")
        for item in items:
            parsed = _tee_from_list_item(item)
            if parsed:
                out[parsed[0]] = parsed[1]
        total = payload.get("total")
        offset += len(items)
        if not items or not isinstance(total, int) or offset >= total:
            break
    return out


def fetch_catalog() -> list[dict]:
    """Download and normalize the live catalog (blocking; raises
    :class:`NearAICatalogError` on failure).

    The model list is required; the TEE metadata list is best effort --
    its failure is logged and every model classifies as ``"proxy"``.
    """
    raw_models = fetch_models()
    try:
        tee = fetch_tee_metadata()
    except NearAICatalogError as exc:
        logger.warning("NEAR AI model metadata unavailable (every model marked proxy): %s", exc)
        tee = {}
    models: list[dict] = []
    seen: set[str] = set()
    for raw in raw_models:
        wire_id = raw.get("id") if isinstance(raw, dict) else None
        entry = _normalize_entry(raw, tee.get(wire_id) if isinstance(wire_id, str) else None)
        if entry is not None and entry["id"] not in seen:
            seen.add(entry["id"])
            models.append(entry)
    if not models:
        raise NearAICatalogError("Model list contained no models")
    models.sort(key=lambda m: m["id"])
    return models


def get_catalog(refresh: bool = False) -> dict:
    """Return ``{"models", "fetched_at", "stale", "error"}`` (blocking).

    Serves the cache while it is younger than the TTL, otherwise (or on
    ``refresh``) re-fetches. A failed fetch returns whatever cache exists
    (``stale: True``) with the error text; with no cache at all ``models``
    is empty. Call via ``asyncio.to_thread`` from request handlers.
    """
    cache = _read_cache()
    fresh = cache is not None and (time.time() - cache["fetched_at"]) < CATALOG_TTL_SECONDS
    if fresh and not refresh:
        return {**cache, "stale": False, "error": None}
    try:
        models = fetch_catalog()
    except NearAICatalogError as exc:
        error = str(exc) or exc.__class__.__name__
        logger.warning("NEAR AI catalog fetch failed: %s", error)
        if cache is None:
            return {"fetched_at": None, "models": [], "stale": True, "error": error}
        return {**cache, "stale": True, "error": error}
    cache = {"fetched_at": time.time(), "models": models}
    try:
        _write_cache(cache)
    except OSError:
        logger.warning("Failed to write NEAR AI catalog cache", exc_info=True)
    return {**cache, "stale": False, "error": None}


def chat_models(models: list[dict]) -> list[dict]:
    """The tool-capable subset of a catalog list, order preserved."""
    return [m for m in models if is_chat_model(m)]
