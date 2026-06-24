"""
Redis-backed response cache for all GET endpoints.

Design
──────
Cache key:  md5(METHOD:PATH:sorted_query_string)
Storage:    Redis string, value = JSON-serialised response body
TTL by group:
  /parts, /suppliers         → 300 s  (5 min)
  /boms                      → 60 s   (1 min — status changes frequently)
  /reasoning, /disruption    → 600 s  (10 min — pure computation)
  /search                    → 120 s  (2 min)
  default                    → 180 s  (3 min)

Invalidation:
  Any non-GET request to a resource path flushes all cache keys whose
  path prefix matches that resource (e.g. POST /parts → flush parts:*).

Metrics:
  Each hit/miss is counted in Redis so /health can expose cache stats.

Usage (FastAPI middleware — applied once in main.py):
  app.add_middleware(CacheMiddleware)

The middleware is transparent: cached responses carry an
  X-Cache: HIT  or  X-Cache: MISS
header so you can verify it's working.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Optional

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# ── TTL map ───────────────────────────────────────────────────────────────────

_TTL: list[tuple[str, int]] = [
    (r"^/parts", 300),
    (r"^/suppliers", 300),
    (r"^/boms", 60),
    (r"^/reasoning", 600),
    (r"^/disruption", 600),
    (r"^/search", 120),
]
_DEFAULT_TTL = 180

# Endpoints that should never be cached
_SKIP_PATHS = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/auth/token",
    "/auth/refresh",
    "/auth/revoke",
}

# Redis key prefixes for invalidation
_INVALIDATION_MAP: list[tuple[str, str]] = [
    (r"^/parts", "cache:parts"),
    (r"^/suppliers", "cache:suppliers"),
    (r"^/boms", "cache:boms"),
    (r"^/disruption", "cache:disruption"),
    (r"^/search", "cache:search"),
]

_METRICS_KEY = "cache:metrics"


def _ttl_for(path: str) -> int:
    for pattern, ttl in _TTL:
        if re.match(pattern, path):
            return ttl
    return _DEFAULT_TTL


def _cache_key(method: str, path: str, query: str) -> str:
    raw = f"{method}:{path}:{query}"
    digest = hashlib.md5(raw.encode()).hexdigest()
    # Prefix by resource so we can flush by pattern
    prefix = "cache:default"
    for pattern, pfx in _INVALIDATION_MAP:
        if re.match(pattern, path):
            prefix = pfx
            break
    return f"{prefix}:{digest}"


def _get_redis():
    import redis as redis_lib
    from src.config import get_settings

    s = get_settings()
    host = s.redis_host or "localhost"
    port = s.redis_port or 6379
    db = (s.redis_db or 0) + 2  # db+2 — separate from rate-limit (db) and tokens (db+1)
    return redis_lib.Redis(
        host=host, port=port, db=db, decode_responses=True, socket_connect_timeout=1
    )


# ── Cache helpers (used by middleware + stats endpoint) ───────────────────────


def get_cached(key: str) -> Optional[str]:
    try:
        r = _get_redis()
        val = r.get(key)
        if val:
            r.hincrby(_METRICS_KEY, "hits", 1)
        else:
            r.hincrby(_METRICS_KEY, "misses", 1)
        return val
    except Exception as exc:
        logger.debug(f"Cache get failed: {exc}")
        return None


def set_cached(key: str, value: str, ttl: int) -> None:
    try:
        r = _get_redis()
        r.setex(key, ttl, value)
    except Exception as exc:
        logger.debug(f"Cache set failed: {exc}")


def invalidate_prefix(prefix: str) -> int:
    """Delete all cache keys matching prefix:*. Returns count deleted."""
    try:
        r = _get_redis()
        keys = list(r.scan_iter(f"{prefix}:*"))
        if keys:
            r.delete(*keys)
            logger.debug(f"Cache invalidated {len(keys)} keys for prefix {prefix!r}")
        return len(keys)
    except Exception as exc:
        logger.debug(f"Cache invalidation failed: {exc}")
        return 0


def get_stats() -> dict:
    """Return cache hit/miss counts and hit rate."""
    try:
        r = _get_redis()
        hits = int(r.hget(_METRICS_KEY, "hits") or 0)
        misses = int(r.hget(_METRICS_KEY, "misses") or 0)
        total = hits + misses
        return {
            "hits": hits,
            "misses": misses,
            "total": total,
            "hit_rate": round(hits / total, 3) if total else 0.0,
        }
    except Exception:
        return {"hits": 0, "misses": 0, "total": 0, "hit_rate": 0.0}


# ── Middleware ─────────────────────────────────────────────────────────────────


class CacheMiddleware(BaseHTTPMiddleware):
    """
    Transparent read-through cache for GET endpoints.

    - Skips auth, docs, and write endpoints automatically.
    - Adds X-Cache: HIT / MISS header to every cacheable response.
    - On write (POST/PATCH/PUT/DELETE), invalidates the matching prefix.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # ── Write: invalidate cache then pass through ─────────────────────────
        if method != "GET":
            response = await call_next(request)
            if response.status_code < 400:
                for pattern, prefix in _INVALIDATION_MAP:
                    if re.match(pattern, path):
                        invalidate_prefix(prefix)
                        break
            return response

        # ── Skip non-cacheable paths ──────────────────────────────────────────
        if path in _SKIP_PATHS or path.startswith("/extraction"):
            return await call_next(request)

        # ── Cache lookup ──────────────────────────────────────────────────────
        query = str(request.query_params)
        key = _cache_key(method, path, query)
        cached = get_cached(key)

        if cached:
            try:
                body = json.loads(cached)
                return Response(
                    content=json.dumps(body),
                    media_type="application/json",
                    headers={"X-Cache": "HIT"},
                )
            except Exception:
                pass  # corrupted cache entry — fall through to live request

        # ── Cache miss: call endpoint and store result ─────────────────────────
        response = await call_next(request)

        if response.status_code == 200:
            # Read the response body (streaming response needs buffering)
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk

            body_str = body_bytes.decode()
            ttl = _ttl_for(path)
            set_cached(key, body_str, ttl)

            return Response(
                content=body_str,
                status_code=response.status_code,
                headers={**dict(response.headers), "X-Cache": "MISS"},
                media_type="application/json",
            )

        return response
