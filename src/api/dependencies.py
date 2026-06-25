"""
FastAPI dependencies for the Supply Chain Knowledge Graph API.
"""

from typing import Generator, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from src.api.auth import decode_access_token
from src.graph.neo4j_client import Neo4jClient

# ── Auth dependency ───────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> dict:
    """
    Validate the Bearer JWT and return the decoded payload.

    Raises 401 if no token or invalid token.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(credentials.credentials)


# ── DB dependency ─────────────────────────────────────────────────────────────


def get_db() -> Generator[Optional[Neo4jClient], None, None]:
    """
    Yield a connected Neo4jClient, or None if Neo4j is unreachable.

    Endpoints that absolutely require the database should check for None
    and raise HTTP 503. Endpoints that only optionally use the database
    (e.g. extraction without persist) can proceed without it.

    Yielding None instead of raising keeps FastAPI's dependency injection
    from crashing before Pydantic has a chance to validate the request body —
    which would turn a 422 (bad request) into a 500 (server error).
    """
    from neo4j.exceptions import ServiceUnavailable

    client = Neo4jClient()
    try:
        client.connect()
    except (ServiceUnavailable, Exception) as exc:
        logger.warning(f"get_db: Neo4j unavailable — yielding None: {exc}")
        yield None
        return

    try:
        yield client
    finally:
        client.close()


def require_db(db: Optional[Neo4jClient] = Depends(get_db)) -> Neo4jClient:
    """
    Like get_db but raises HTTP 503 if Neo4j is unavailable.

    Use this on endpoints that cannot function without the database.
    """
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        )
    return db