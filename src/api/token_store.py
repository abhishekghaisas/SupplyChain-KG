"""
Refresh token store — Redis-backed.

Each refresh token is a signed JWT containing a JTI (JWT ID, UUID4).
The JTI is stored as a Redis key:

    refresh:<jti>  →  <client_id>   TTL: refresh_expire_seconds

On use:
  1. Decode the JWT (verify signature + expiry)
  2. Look up the JTI in Redis — if missing, token has already been used
     or revoked (reuse detected → reject)
  3. Delete the JTI key (invalidate the old token)
  4. Issue a new access + refresh token pair

This is the "refresh token rotation" pattern:
  - Each refresh token is single-use
  - A stolen token can only be used once before the legitimate client's
    next refresh detects the conflict
  - On conflict, the safe response is to revoke everything and force
    re-authentication

Public API
──────────
  store_refresh_token(jti, client_id, ttl_seconds)
  validate_and_consume_refresh_token(jti) -> client_id | None
  revoke_refresh_token(jti)
  revoke_all_for_client(client_id)   — nuclear option, e.g. on password change
"""

from __future__ import annotations

from typing import Optional

from loguru import logger


def _get_redis():
    """
    Return a Redis client.

    Imported lazily so the module can be imported without Redis installed
    (e.g. in tests that don't exercise the refresh path).
    """
    import redis as redis_lib
    from src.config import get_settings

    settings = get_settings()
    host = settings.redis_host or "localhost"
    port = settings.redis_port or 6379
    db = (settings.redis_db or 0) + 1   # use db+1 to separate from rate-limit keys

    return redis_lib.Redis(host=host, port=port, db=db, decode_responses=True)


def _key(jti: str) -> str:
    return f"refresh:{jti}"


def store_refresh_token(jti: str, client_id: str, ttl_seconds: int) -> None:
    """
    Store a refresh token JTI in Redis with a TTL.

    Args:
        jti:         The JWT ID claim from the refresh token.
        client_id:   The client this token belongs to.
        ttl_seconds: How long until the token expires.
    """
    try:
        r = _get_redis()
        r.setex(_key(jti), ttl_seconds, client_id)
        logger.debug(f"Stored refresh token JTI {jti[:8]}… for client {client_id!r}")
    except Exception as exc:
        logger.error(f"Failed to store refresh token: {exc}")
        raise


def validate_and_consume_refresh_token(jti: str) -> Optional[str]:
    """
    Atomically validate and consume a refresh token JTI.

    Returns the client_id if the JTI was found and deleted, or None
    if the token was not found (already used, expired, or revoked).

    The deletion is atomic via a Lua script — no race between check and delete.
    """
    try:
        r = _get_redis()

        # Atomic get-and-delete: returns the value if key existed, else None
        # Using a pipeline with WATCH would be more portable, but a Lua script
        # is simpler and equally safe for this pattern.
        lua_script = """
        local val = redis.call('GET', KEYS[1])
        if val then
            redis.call('DEL', KEYS[1])
            return val
        end
        return nil
        """
        result = r.eval(lua_script, 1, _key(jti))
        if result:
            logger.debug(f"Consumed refresh token JTI {jti[:8]}…")
        else:
            logger.warning(f"Refresh token JTI {jti[:8]}… not found — possible reuse attempt")
        return result
    except Exception as exc:
        logger.error(f"Failed to validate refresh token: {exc}")
        return None


def revoke_refresh_token(jti: str) -> None:
    """Delete a single refresh token JTI (e.g. on explicit logout)."""
    try:
        r = _get_redis()
        deleted = r.delete(_key(jti))
        if deleted:
            logger.info(f"Revoked refresh token JTI {jti[:8]}…")
    except Exception as exc:
        logger.error(f"Failed to revoke refresh token: {exc}")


def revoke_all_for_client(client_id: str) -> int:
    """
    Revoke all refresh tokens for a client by scanning for matching values.

    This is O(n) across all refresh keys — use sparingly (e.g. on
    credential rotation, not on every logout).

    Returns the number of tokens revoked.
    """
    try:
        r = _get_redis()
        revoked = 0
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match="refresh:*", count=100)
            for key in keys:
                val = r.get(key)
                if val == client_id:
                    r.delete(key)
                    revoked += 1
            if cursor == 0:
                break
        logger.info(f"Revoked {revoked} refresh token(s) for client {client_id!r}")
        return revoked
    except Exception as exc:
        logger.error(f"Failed to revoke all tokens for client: {exc}")
        return 0
