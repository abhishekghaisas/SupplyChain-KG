"""
OAuth2 authentication — client credentials flow with refresh token rotation.

Endpoints
─────────
  POST /auth/token
    Exchange client_id + client_secret for an access token + refresh token.
    grant_type=client_credentials

  POST /auth/refresh
    Exchange a valid refresh token for a new access + refresh token pair.
    The old refresh token is immediately invalidated (rotation).
    grant_type=refresh_token

  POST /auth/revoke
    Revoke a refresh token (logout). Accepts the refresh_token in the body.

Token lifetimes
───────────────
  Access token:  jwt_expire_minutes (default 60 min)
  Refresh token: refresh_expire_days (default 7 days)

Refresh token rotation
──────────────────────
  Each refresh token is single-use. On /auth/refresh:
    1. JWT signature + expiry verified
    2. JTI looked up in Redis — reuse or revocation detected if missing
    3. Old JTI deleted atomically
    4. New access + refresh token pair issued

Setup
─────
  Generate client secret hash (run once):
    python -c "
    import bcrypt, getpass
    s = getpass.getpass('Client secret: ').encode()
    print(bcrypt.hashpw(s, bcrypt.gensalt(rounds=12)).decode())
    "
  Store the output in .env as OAUTH2_CLIENT_SECRET_HASH.
"""


import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import bcrypt
from fastapi import APIRouter, Body, Form, HTTPException, Request, Response, status
from jose import JWTError, jwt
from loguru import logger
from pydantic import BaseModel

from src.api.limiter import AUTH_LIMIT  # noqa: F401 — see SlowAPIMiddleware in main.py
from src.config import get_settings

# Exposed at module level for test patching


def validate_and_consume_refresh_token(jti: str):
    from src.api.token_store import validate_and_consume_refresh_token as _fn
    return _fn(jti)


def store_refresh_token(jti: str, client_id: str, expire_seconds: int):
    from src.api.token_store import store_refresh_token as _fn
    return _fn(jti, client_id, expire_seconds)


def revoke_refresh_token(jti: str):
    from src.api.token_store import revoke_refresh_token as _fn
    return _fn(jti)


router = APIRouter(prefix="/auth", tags=["Auth"])


# ── Response schemas ──────────────────────────────────────────────────────────


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token seconds until expiry
    refresh_expires_in: int  # refresh token seconds until expiry


class RevokeRequest(BaseModel):
    refresh_token: str


# ── JWT helpers ───────────────────────────────────────────────────────────────


def create_access_token(data: Dict[str, Any]) -> tuple[str, int]:
    """
    Sign an access JWT.

    Returns (token, expires_in_seconds).
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)

    payload = {
        **data,
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, settings.jwt_expire_minutes * 60


def create_refresh_token(client_id: str) -> tuple[str, str, int]:
    """
    Sign a refresh JWT and store its JTI in Redis.

    Returns (token, jti, expires_in_seconds).
    The JTI is needed to revoke the token later.
    """
    from src.api.token_store import store_refresh_token

    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire_seconds = settings.refresh_expire_days * 24 * 3600
    expire = now + timedelta(seconds=expire_seconds)
    jti = str(uuid.uuid4())

    payload = {
        "sub": client_id,
        "jti": jti,
        "iat": now,
        "exp": expire,
        "type": "refresh",
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    store_refresh_token(jti, client_id, expire_seconds)
    return token, jti, expire_seconds


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Verify and decode an access JWT.

    Raises HTTPException 401 on any failure.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        logger.warning(f"JWT decode failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def _decode_refresh_token(token: str) -> Dict[str, Any]:
    """
    Verify and decode a refresh JWT (signature + expiry only).

    Does NOT check the Redis store — that happens in the endpoint.
    Raises HTTPException 401 on any failure.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        logger.warning(f"Refresh JWT decode failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def _verify_client_secret(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception as exc:
        logger.warning(f"bcrypt check failed: {exc}")
        return False


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/token", response_model=TokenResponse)
def token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
) -> TokenResponse:
    """
    OAuth2 client credentials — issue access + refresh token pair.

    Request (application/x-www-form-urlencoded):
        grant_type=client_credentials
        client_id=supply-chain-api
        client_secret=<your secret>
    """
    settings = get_settings()

    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported grant_type: {grant_type!r}. "
            "Use 'client_credentials' or 'refresh_token'.",
        )

    if client_id != settings.oauth2_client_id:
        logger.warning(f"Token request with unknown client_id: {client_id!r}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not _verify_client_secret(client_secret, settings.oauth2_client_secret_hash):
        logger.warning(f"Invalid client_secret for client_id: {client_id!r}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token, expires_in = create_access_token({"sub": client_id})
    refresh_token, _, refresh_exp = create_refresh_token(client_id)

    logger.info(f"Issued token pair for client {client_id!r}")
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_exp,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    grant_type: str = Form(...),
    refresh_token: str = Form(...),
) -> TokenResponse:
    """
    Exchange a refresh token for a new access + refresh token pair.

    The provided refresh token is immediately invalidated (rotation).
    If the token has already been used or is not found in the store,
    the request is rejected with 401.

    Request (application/x-www-form-urlencoded):
        grant_type=refresh_token
        refresh_token=<your refresh token>
    """
    if grant_type != "refresh_token":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="grant_type must be 'refresh_token'",
        )

    # 1. Verify JWT signature and expiry
    payload = _decode_refresh_token(refresh_token)
    jti = payload.get("jti")
    client_id = payload.get("sub")

    if not jti or not client_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token claims",
        )

    # 2. Atomically validate and consume the JTI from Redis
    from src.api.token_store import validate_and_consume_refresh_token

    stored_client = validate_and_consume_refresh_token(jti)

    if stored_client is None:
        # Token not in Redis — already used, expired, or revoked.
        # This could be a reuse attack; log and reject.
        logger.warning(
            f"Refresh token reuse or revocation detected for client {client_id!r} "
            f"JTI {jti[:8]}…"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has already been used or revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if stored_client != client_id:
        logger.error(
            f"JTI {jti[:8]}… belongs to {stored_client!r} " f"but token claims {client_id!r}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # 3. Issue a new token pair
    new_access, expires_in = create_access_token({"sub": client_id})
    new_refresh, _, refresh_exp = create_refresh_token(client_id)

    logger.info(f"Refreshed token pair for client {client_id!r}")
    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=expires_in,
        refresh_expires_in=refresh_exp,
    )


@router.post("/revoke")
def revoke(
    request: Request,
    body: RevokeRequest = Body(...),
) -> Response:
    """
    Revoke a refresh token (logout).

    The access token is not revoked — it will expire naturally after
    jwt_expire_minutes. For immediate access token invalidation,
    reduce jwt_expire_minutes or implement an access token blocklist.
    """
    # Decode to get the JTI — ignore expiry errors (token may have just expired)
    settings = get_settings()
    try:
        payload = jwt.decode(
            body.refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},  # allow revocation of expired tokens
        )
    except JWTError:
        # Malformed token — nothing to revoke, return success anyway
        # (don't leak information about token validity)
        return Response(status_code=204)

    jti = payload.get("jti")
    if jti:
        from src.api.token_store import revoke_refresh_token

        revoke_refresh_token(jti)
        logger.info(f"Revoked refresh token JTI {jti[:8]}… via /auth/revoke")
    return Response(status_code=204)