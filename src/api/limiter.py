"""
Rate limiting for the Supply Chain Knowledge Graph API.

Uses slowapi (wraps the `limits` library) with in-memory storage.
To scale horizontally, set REDIS_HOST in .env and the limiter will
automatically switch to Redis-backed storage.

Limit tiers
───────────
  auth        10  / minute   — brute-force protection on /auth/token
  extraction  20  / hour     — Anthropic API cost protection
  default    120  / minute   — all other authenticated endpoints

Key resolution
──────────────
  Authenticated requests  → client_id from JWT sub claim
  Unauthenticated         → client IP address

Usage (on an endpoint):
  @router.post("/extract")
  @limiter.limit(EXTRACTION_LIMIT)
  async def extract(request: Request, ...):
      ...
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import get_settings

# ── Limit strings ─────────────────────────────────────────────────────────────
# Import these in router files to keep limits DRY.

AUTH_LIMIT = "10/minute"
EXTRACTION_LIMIT = "20/hour"
DEFAULT_LIMIT = "120/minute"


# ── Key function ──────────────────────────────────────────────────────────────


def _resolve_key(request) -> str:
    """
    Return the rate-limit key for a request.

    - Authenticated (valid Bearer JWT): use the client_id from the token.
      This correctly buckets all requests from the same client together,
      regardless of which IP they come from.
    - Unauthenticated: fall back to the client IP address.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        try:
            from src.api.auth import decode_access_token

            payload = decode_access_token(token)
            client_id = payload.get("sub", "")
            if client_id:
                return f"client:{client_id}"
        except Exception:
            pass  # invalid token — fall through to IP

    return f"ip:{get_remote_address(request)}"


# ── Limiter instance ──────────────────────────────────────────────────────────


def _make_storage_uri() -> str:
    try:
        settings = get_settings()
        if settings.redis_host:
            port = settings.redis_port or 6379
            db = settings.redis_db or 0
            return f"redis://{settings.redis_host}:{port}/{db}"
    except Exception:
        pass
    return "memory://"


def _make_limiter():
    """Build lazily so tests can patch get_settings before Redis connects."""
    return Limiter(
        key_func=_resolve_key,
        storage_uri=_make_storage_uri(),
        default_limits=[DEFAULT_LIMIT],
    )


try:
    limiter = _make_limiter()
except Exception:
    # Fall back to in-memory limiter (tests / no Redis)
    limiter = Limiter(key_func=_resolve_key, default_limits=[DEFAULT_LIMIT])
