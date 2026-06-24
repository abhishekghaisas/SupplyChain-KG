"""
FastAPI dependency injection: authentication and database session.

Authentication is now OAuth2 Bearer (JWT).
Obtain a token via POST /auth/token (client credentials flow),
then pass it as:  Authorization: Bearer <token>
"""

from typing import Generator

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.auth import decode_access_token
from src.graph.neo4j_client import Neo4jClient

# ── Auth ──────────────────────────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)


def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(_bearer_scheme),
) -> dict:
    """
    Validate the Authorization: Bearer <token> header.

    Decodes and verifies the JWT, returning the token payload on success.
    Raises HTTP 401 if the header is missing, the token is invalid, or expired.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. "
                   "Use: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(credentials.credentials)


# ── Database ──────────────────────────────────────────────────────────────────

def get_db() -> Generator[Neo4jClient, None, None]:
    """Yield a connected Neo4jClient and close it when the request finishes."""
    client = Neo4jClient()
    client.connect()
    try:
        yield client
    finally:
        client.close()
