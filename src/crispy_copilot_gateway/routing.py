"""Endpoint-cache and routing policy: which upstream API a Copilot model speaks.

The cache is keyed by connection label so a pinned key routes on its own account's
catalog rather than the active account's (P4).
"""
from __future__ import annotations

import time

from . import connections

MODELS_TTL = 600  # seconds before a connection's supported_endpoints cache refreshes
FALLBACK_CODE = "no_available_model_endpoints"  # Copilot rejects an advertised endpoint

_cache: dict = {}  # label -> {"endpoints": {model_id: frozenset}, "fetched_at": float}


def _slot(label: str | None) -> dict:
    return _cache.setdefault(label or "", {"endpoints": {}, "fetched_at": 0.0})


def set_cache(endpoints: dict, label: str | None = None) -> None:
    """Test hook: inject a connection's model endpoints without network.

    With no label, targets the effective active account (what unpinned keys use).
    """
    if label is None:
        label = connections.label_for_key(None)
    slot = _slot(label)
    slot["endpoints"] = {mid: frozenset(eps or []) for mid, eps in endpoints.items()}
    slot["fetched_at"] = time.time()


async def ensure_cache(key_name: str | None = None) -> None:
    """Refresh the key's account catalog when stale; failures fail open."""
    slot = _slot(connections.label_for_key(key_name))
    if time.time() - slot["fetched_at"] <= MODELS_TTL:
        return
    from . import proxy  # circular-import safety
    try:
        models = await proxy.fetch_models(connections.for_key(key_name))
        slot["endpoints"] = {m.get("id"): frozenset(m.get("supported_endpoints") or [])
                             for m in models if isinstance(m, dict)}
    except Exception:
        pass  # fail open; retry after TTL
    slot["fetched_at"] = time.time()


def model_endpoints(model: str | None, key_name: str | None = None) -> frozenset | None:
    """Cached endpoint set for the model on the key's account; None when unknown."""
    return _slot(connections.label_for_key(key_name))["endpoints"].get(model or "")


async def upstream_kind(model: str | None, key_name: str | None = None) -> str:
    """'responses' only when the model lists /responses and not /chat/completions.

    Unknown models or fetch failures fail open to 'chat' (passthrough).
    """
    await ensure_cache(key_name)
    eps = model_endpoints(model, key_name)
    if eps is not None and "/responses" in eps and "/chat/completions" not in eps:
        return "responses"
    return "chat"
