from __future__ import annotations

import logging
import math
import re

import httpx

from app.core.openai_usage import record_openai_usage

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "text-embedding-3-small"
_FALLBACK_DIMENSIONS = 12
_cache: dict[str, list[float]] = {}


def _hash_embedding(text: str, dimensions: int = _FALLBACK_DIMENSIONS) -> list[float]:
    tokens = sorted({token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2})
    if not tokens:
        return [0.0] * dimensions
    values = [0.0] * dimensions
    for token in tokens:
        bucket = hash(token) % dimensions
        values[bucket] += (len(token) % 7) + 1
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [round(v / norm, 5) for v in values]


def get_embedding(
    text: str,
    api_key: str | None,
    api_base: str = "https://api.openai.com/v1",
    usage_source: str = "embedding",
) -> list[float]:
    """Return a text embedding vector.

    Uses OpenAI text-embedding-3-small when api_key is set; falls back to a
    hash-bucket approximation otherwise.  Results are cached for the lifetime
    of the process to avoid redundant API calls.
    """
    if not api_key:
        return _hash_embedding(text)
    if text in _cache:
        return _cache[text]
    try:
        endpoint = f"{api_base.rstrip('/')}/embeddings"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                endpoint,
                headers=headers,
                json={"model": _EMBEDDING_MODEL, "input": text},
            )
            response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage", {})
        record_openai_usage(
            source=usage_source,
            model=_EMBEDDING_MODEL,
            prompt_tokens=int(usage.get("prompt_tokens", usage.get("total_tokens", 0)) or 0),
            completion_tokens=0,
        )
        vector: list[float] = payload["data"][0]["embedding"]
    except Exception:
        logger.warning("OpenAI embedding call failed; using hash-bucket fallback.", exc_info=True)
        return _hash_embedding(text)
    _cache[text] = vector
    return vector
