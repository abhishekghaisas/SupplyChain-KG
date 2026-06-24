"""
Unit tests for OAuth2 authentication.

Tests the token endpoint, JWT helpers, and the verify_token dependency.
No Neo4j required — DB dependency is mocked.

Run with:
    pytest test_auth.py -v
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient
from jose import jwt


# ── Fixtures ──────────────────────────────────────────────────────────────────

TEST_CLIENT_ID     = "test-client"
TEST_CLIENT_SECRET = "supersecret123"
TEST_SECRET_HASH   = bcrypt.hashpw(
    TEST_CLIENT_SECRET.encode(), bcrypt.gensalt(rounds=4)   # low rounds for test speed
).decode()
TEST_JWT_SECRET    = "test-jwt-secret-not-for-production"
TEST_ALGORITHM     = "HS256"
TEST_EXPIRE_MIN    = 60


class _TestSettings:
    jwt_secret_key             = TEST_JWT_SECRET
    jwt_algorithm              = TEST_ALGORITHM
    jwt_expire_minutes         = TEST_EXPIRE_MIN
    oauth2_client_id           = TEST_CLIENT_ID
    oauth2_client_secret_hash  = TEST_SECRET_HASH
    # keep other attrs the app might touch
    app_name    = "Test App"
    app_version = "0.0.1"
    neo4j_uri   = "bolt://localhost:7687"
    neo4j_user  = "neo4j"
    neo4j_password = "test"
    neo4j_database = "neo4j"
    api_host    = "0.0.0.0"
    api_port    = 8000
    api_reload  = False
    api_log_level = "info"
    debug       = False
    refresh_expire_days = 7
    anthropic_api_key = "test"
    redis_host = None
    redis_port = None
    redis_db = 0


@pytest.fixture(autouse=True)
def patch_settings():
    """Replace get_settings() everywhere with the test settings."""
    with patch("src.config.get_settings", return_value=_TestSettings()), \
         patch("src.api.auth.get_settings", return_value=_TestSettings()), \
         patch("src.config.get_settings", return_value=_TestSettings()):
        yield


@pytest.fixture
def app_client():
    """FastAPI TestClient with DB dependency overridden to a mock."""
    from src.api.main import app
    from src.api.dependencies import get_db

    mock_db = MagicMock()
    mock_db.execute_query.return_value = [{"1": 1}]   # health check

    def _override():
        yield mock_db

    app.dependency_overrides[get_db] = _override
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _valid_token() -> str:
    """Issue a real signed token using test settings."""
    from src.api.auth import create_access_token
    token, _ = create_access_token({"sub": TEST_CLIENT_ID})
    return token


# ── create_access_token ───────────────────────────────────────────────────────

class TestCreateAccessToken:
    def test_returns_string(self):
        from src.api.auth import create_access_token
        token, _ = create_access_token({"sub": "client"})
        assert isinstance(token, str)

    def test_expires_in_is_seconds(self):
        from src.api.auth import create_access_token
        _, expires_in = create_access_token({"sub": "client"})
        assert expires_in == TEST_EXPIRE_MIN * 60

    def test_payload_sub_preserved(self):
        from src.api.auth import create_access_token
        token, _ = create_access_token({"sub": "my-client"})
        payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=[TEST_ALGORITHM])
        assert payload["sub"] == "my-client"

    def test_type_claim_is_access(self):
        from src.api.auth import create_access_token
        token, _ = create_access_token({"sub": "client"})
        payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=[TEST_ALGORITHM])
        assert payload["type"] == "access"

    def test_exp_is_in_future(self):
        from src.api.auth import create_access_token
        token, _ = create_access_token({"sub": "client"})
        payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=[TEST_ALGORITHM])
        assert payload["exp"] > time.time()

    def test_exp_roughly_one_hour(self):
        from src.api.auth import create_access_token
        token, _ = create_access_token({"sub": "client"})
        payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=[TEST_ALGORITHM])
        delta = payload["exp"] - time.time()
        assert 3500 < delta <= 3600


# ── decode_access_token ───────────────────────────────────────────────────────

class TestDecodeAccessToken:
    def test_valid_token_returns_payload(self):
        from src.api.auth import decode_access_token
        token = _valid_token()
        payload = decode_access_token(token)
        assert payload["sub"] == TEST_CLIENT_ID

    def test_invalid_signature_raises_401(self):
        from fastapi import HTTPException
        from src.api.auth import decode_access_token
        bad_token = jwt.encode({"sub": "x", "type": "access"},
                               "wrong-secret", algorithm=TEST_ALGORITHM)
        with pytest.raises(HTTPException) as exc:
            decode_access_token(bad_token)
        assert exc.value.status_code == 401

    def test_expired_token_raises_401(self):
        from fastapi import HTTPException
        from src.api.auth import decode_access_token
        expired = jwt.encode(
            {"sub": "x", "type": "access",
             "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            TEST_JWT_SECRET, algorithm=TEST_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc:
            decode_access_token(expired)
        assert exc.value.status_code == 401

    def test_wrong_type_raises_401(self):
        from fastapi import HTTPException
        from src.api.auth import decode_access_token
        token = jwt.encode(
            {"sub": "x", "type": "refresh",
             "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            TEST_JWT_SECRET, algorithm=TEST_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc:
            decode_access_token(token)
        assert exc.value.status_code == 401

    def test_malformed_token_raises_401(self):
        from fastapi import HTTPException
        from src.api.auth import decode_access_token
        with pytest.raises(HTTPException) as exc:
            decode_access_token("not.a.jwt")
        assert exc.value.status_code == 401

    def test_www_authenticate_header_present(self):
        from fastapi import HTTPException
        from src.api.auth import decode_access_token
        with pytest.raises(HTTPException) as exc:
            decode_access_token("bad")
        assert "Bearer" in exc.value.headers.get("WWW-Authenticate", "")


# ── POST /auth/token ──────────────────────────────────────────────────────────

class TestTokenEndpoint:
    def test_valid_credentials_return_200(self, app_client):
        r = app_client.post("/auth/token", data={
            "grant_type":    "client_credentials",
            "client_id":     TEST_CLIENT_ID,
            "client_secret": TEST_CLIENT_SECRET,
        })
        assert r.status_code == 200

    def test_response_contains_access_token(self, app_client):
        r = app_client.post("/auth/token", data={
            "grant_type":    "client_credentials",
            "client_id":     TEST_CLIENT_ID,
            "client_secret": TEST_CLIENT_SECRET,
        })
        body = r.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == TEST_EXPIRE_MIN * 60

    def test_token_is_valid_jwt(self, app_client):
        r = app_client.post("/auth/token", data={
            "grant_type":    "client_credentials",
            "client_id":     TEST_CLIENT_ID,
            "client_secret": TEST_CLIENT_SECRET,
        })
        token = r.json()["access_token"]
        payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=[TEST_ALGORITHM])
        assert payload["sub"] == TEST_CLIENT_ID

    def test_wrong_client_id_returns_401(self, app_client):
        r = app_client.post("/auth/token", data={
            "grant_type":    "client_credentials",
            "client_id":     "wrong-client",
            "client_secret": TEST_CLIENT_SECRET,
        })
        assert r.status_code == 401

    def test_wrong_client_secret_returns_401(self, app_client):
        r = app_client.post("/auth/token", data={
            "grant_type":    "client_credentials",
            "client_id":     TEST_CLIENT_ID,
            "client_secret": "wrong-secret",
        })
        assert r.status_code == 401

    def test_wrong_grant_type_returns_400(self, app_client):
        r = app_client.post("/auth/token", data={
            "grant_type":    "authorization_code",
            "client_id":     TEST_CLIENT_ID,
            "client_secret": TEST_CLIENT_SECRET,
        })
        assert r.status_code == 400

    def test_missing_fields_returns_422(self, app_client):
        r = app_client.post("/auth/token", data={"grant_type": "client_credentials"})
        assert r.status_code == 422

    def test_error_detail_is_generic(self, app_client):
        """Error messages must not reveal which field was wrong."""
        r = app_client.post("/auth/token", data={
            "grant_type":    "client_credentials",
            "client_id":     TEST_CLIENT_ID,
            "client_secret": "bad",
        })
        detail = r.json()["detail"]
        assert "client_secret" not in detail.lower()
        assert "invalid client credentials" in detail.lower()


# ── verify_token dependency ───────────────────────────────────────────────────

class TestVerifyTokenDependency:
    def test_valid_bearer_token_passes(self, app_client):
        token = _valid_token()
        r = app_client.get("/parts", headers={"Authorization": f"Bearer {token}"})
        # parts list may be empty but should not be 401
        assert r.status_code != 401

    def test_missing_auth_header_returns_401(self, app_client):
        r = app_client.get("/parts")
        assert r.status_code == 401

    def test_wrong_scheme_returns_401(self, app_client):
        r = app_client.get("/parts",
                           headers={"Authorization": "ApiKey some-key"})
        assert r.status_code == 401

    def test_expired_token_returns_401(self, app_client):
        expired = jwt.encode(
            {"sub": TEST_CLIENT_ID, "type": "access",
             "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            TEST_JWT_SECRET, algorithm=TEST_ALGORITHM,
        )
        r = app_client.get("/parts",
                           headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 401

    def test_tampered_token_returns_401(self, app_client):
        token = _valid_token()
        tampered = token[:-4] + "XXXX"
        r = app_client.get("/parts",
                           headers={"Authorization": f"Bearer {tampered}"})
        assert r.status_code == 401

    def test_health_endpoint_needs_no_token(self, app_client):
        """Health check must remain unauthenticated."""
        r = app_client.get("/health")
        assert r.status_code == 200

    def test_token_endpoint_needs_no_token(self, app_client):
        """/auth/token itself must not require a Bearer token."""
        r = app_client.post("/auth/token", data={
            "grant_type":    "client_credentials",
            "client_id":     TEST_CLIENT_ID,
            "client_secret": TEST_CLIENT_SECRET,
        })
        assert r.status_code == 200


# ── End-to-end: obtain token, use token ──────────────────────────────────────

class TestFullFlow:
    def test_obtain_then_use_token(self, app_client):
        """Get a real token, then use it to hit a protected endpoint."""
        # 1. obtain
        r = app_client.post("/auth/token", data={
            "grant_type":    "client_credentials",
            "client_id":     TEST_CLIENT_ID,
            "client_secret": TEST_CLIENT_SECRET,
        })
        assert r.status_code == 200
        token = r.json()["access_token"]

        # 2. use
        r2 = app_client.get("/parts",
                            headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code != 401

    def test_x_api_key_no_longer_accepted(self, app_client):
        """Old X-API-Key header must not grant access after migration."""
        r = app_client.get("/parts",
                           headers={"X-API-Key": "dev-api-key"})
        assert r.status_code == 401