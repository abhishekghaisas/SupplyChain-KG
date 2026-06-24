"""
Unit tests for refresh token rotation.

Tests cover:
  - /auth/token now returns refresh_token alongside access_token
  - /auth/refresh issues a new token pair and invalidates the old token
  - Reuse of a consumed refresh token returns 401
  - /auth/revoke invalidates the refresh token
  - Expired/tampered/wrong-type refresh tokens are rejected
  - Redis store: store, consume, revoke

No Neo4j required. Redis is mocked throughout.

Run with:
    pytest test_token_refresh.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient
from jose import jwt


# ── Test config ───────────────────────────────────────────────────────────────

TEST_CLIENT_ID     = "test-client"
TEST_CLIENT_SECRET = "refreshtestpass"
TEST_SECRET_HASH   = bcrypt.hashpw(
    TEST_CLIENT_SECRET.encode(), bcrypt.gensalt(rounds=4)
).decode()
TEST_JWT_SECRET  = "test-jwt-secret"
TEST_ALGORITHM   = "HS256"
REFRESH_DAYS     = 7


class _TestSettings:
    jwt_secret_key            = TEST_JWT_SECRET
    jwt_algorithm             = TEST_ALGORITHM
    jwt_expire_minutes        = 60
    refresh_expire_days       = REFRESH_DAYS
    oauth2_client_id          = TEST_CLIENT_ID
    oauth2_client_secret_hash = TEST_SECRET_HASH
    app_name    = "Test"; app_version = "0.0.1"
    neo4j_uri   = "bolt://localhost:7687"; neo4j_user = "neo4j"
    neo4j_password = "test"; neo4j_database = "neo4j"
    api_host    = "0.0.0.0"; api_port = 8000
    api_reload  = False; api_log_level = "info"; debug = False
    anthropic_api_key = "test"
    redis_host = None; redis_port = None; redis_db = 0


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_app_overrides():
    """Ensure dependency overrides are clean before and after every test."""
    from src.api.main import app
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def patch_settings():
    s = _TestSettings()
    with patch("src.config.get_settings", return_value=s), \
         patch("src.api.auth.get_settings", return_value=s), \
         patch("src.api.limiter.get_settings", return_value=s):
        yield


@pytest.fixture
def mock_store():
    """
    In-memory mock of the Redis token store.
    Backed by a dict so we can inspect state across calls.
    """
    store: dict = {}

    def _store(jti, client_id, ttl):
        store[jti] = client_id

    def _consume(jti):
        return store.pop(jti, None)

    def _revoke(jti):
        store.pop(jti, None)

    mock = MagicMock()
    mock.store_refresh_token.side_effect = _store
    mock.validate_and_consume_refresh_token.side_effect = _consume
    mock.revoke_refresh_token.side_effect = _revoke
    mock._store = store   # expose for assertions
    return mock


@pytest.fixture
def app_client(mock_store):
    from src.api.main import app
    from src.api.dependencies import get_db
    from slowapi import Limiter
    import src.api.limiter as lmod

    # Fresh limiter + mocked DB
    fresh_limiter = Limiter(key_func=lmod._resolve_key, storage_uri="memory://")
    original = app.state.limiter
    app.state.limiter = fresh_limiter
    lmod.limiter = fresh_limiter

    mock_db = MagicMock()
    mock_db.execute_query.return_value = [{"1": 1}]
    app.dependency_overrides[get_db] = lambda: (yield mock_db)

    with patch("src.api.auth.create_refresh_token",
               wraps=_patched_create_refresh(mock_store)), \
         patch("src.api.auth.validate_and_consume_refresh_token",
               side_effect=mock_store.validate_and_consume_refresh_token), \
         patch("src.api.auth.revoke_refresh_token",
               side_effect=mock_store.revoke_refresh_token), \
         patch("src.api.token_store.store_refresh_token",
               side_effect=lambda jti, client_id, ttl: mock_store._store.update({jti: client_id})), \
         patch("src.api.token_store.validate_and_consume_refresh_token",
               side_effect=mock_store.validate_and_consume_refresh_token), \
         patch("src.api.token_store.revoke_refresh_token",
               side_effect=mock_store.revoke_refresh_token):
        app.dependency_overrides[get_db] = lambda: (yield mock_db)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, mock_store

    app.state.limiter = original
    lmod.limiter = original
    app.dependency_overrides.clear()
    try:
        import src.api.token_store as _ts
        if hasattr(_ts, '_redis_client'):
            _ts._redis_client = None
    except Exception:
        pass


def _patched_create_refresh(mock_store):
    """Wrap create_refresh_token so it uses the mock store."""
    from src.api.auth import create_refresh_token as _orig

    def _wrapped(client_id):
        token, jti, exp = _orig(client_id)
        # The real function calls store_refresh_token internally;
        # redirect that call to our mock store
        mock_store._store[jti] = client_id
        return token, jti, exp

    return _wrapped


def _get_tokens(client, secret=TEST_CLIENT_SECRET):
    """Convenience: obtain a fresh token pair."""
    r = client.post("/auth/token", data={
        "grant_type":    "client_credentials",
        "client_id":     TEST_CLIENT_ID,
        "client_secret": secret,
    })
    return r


# ── /auth/token now returns refresh_token ─────────────────────────────────────

class TestTokenEndpointRefreshField:
    def test_token_response_includes_refresh_token(self, app_client):
        client, _ = app_client
        r = _get_tokens(client)
        assert r.status_code == 200
        body = r.json()
        assert "refresh_token" in body
        assert body["refresh_token"]

    def test_token_response_includes_refresh_expires_in(self, app_client):
        client, _ = app_client
        body = _get_tokens(client).json()
        assert "refresh_expires_in" in body
        assert body["refresh_expires_in"] == REFRESH_DAYS * 24 * 3600

    def test_refresh_token_is_valid_jwt(self, app_client):
        client, _ = app_client
        body = _get_tokens(client).json()
        payload = jwt.decode(body["refresh_token"], TEST_JWT_SECRET,
                             algorithms=[TEST_ALGORITHM])
        assert payload["type"] == "refresh"
        assert payload["sub"]  == TEST_CLIENT_ID
        assert "jti" in payload

    def test_access_and_refresh_tokens_are_different(self, app_client):
        client, _ = app_client
        body = _get_tokens(client).json()
        assert body["access_token"] != body["refresh_token"]


# ── /auth/refresh ─────────────────────────────────────────────────────────────

class TestRefreshEndpoint:
    def test_valid_refresh_returns_200(self, app_client):
        client, _ = app_client
        refresh_token = _get_tokens(client).json()["refresh_token"]
        r = client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
        })
        assert r.status_code == 200

    def test_new_access_token_issued(self, app_client):
        client, _ = app_client
        import time
        body1 = _get_tokens(client).json()
        time.sleep(1)  # ensure different iat/exp so tokens differ
        r = client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": body1["refresh_token"],
        })
        body2 = r.json()
        assert "access_token" in body2
        assert body2["access_token"] != body1["access_token"]

    def test_new_refresh_token_issued(self, app_client):
        client, _ = app_client
        body1 = _get_tokens(client).json()
        r = client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": body1["refresh_token"],
        })
        body2 = r.json()
        assert "refresh_token" in body2
        assert body2["refresh_token"] != body1["refresh_token"]

    def test_old_refresh_token_rejected_after_use(self, app_client):
        """Refresh token rotation: old token must not work after use."""
        client, _ = app_client
        old_refresh = _get_tokens(client).json()["refresh_token"]

        # Use it once
        client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": old_refresh,
        })

        # Try to use it again
        r = client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": old_refresh,
        })
        assert r.status_code == 401

    def test_wrong_grant_type_returns_400(self, app_client):
        client, _ = app_client
        refresh_token = _get_tokens(client).json()["refresh_token"]
        r = client.post("/auth/refresh", data={
            "grant_type":    "client_credentials",
            "refresh_token": refresh_token,
        })
        assert r.status_code == 400

    def test_tampered_refresh_token_returns_401(self, app_client):
        client, _ = app_client
        refresh_token = _get_tokens(client).json()["refresh_token"]
        tampered = refresh_token[:-4] + "XXXX"
        r = client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": tampered,
        })
        assert r.status_code == 401

    def test_expired_refresh_token_returns_401(self, app_client):
        client, _ = app_client
        expired = jwt.encode(
            {"sub": TEST_CLIENT_ID, "type": "refresh", "jti": "test-jti",
             "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            TEST_JWT_SECRET, algorithm=TEST_ALGORITHM,
        )
        r = client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": expired,
        })
        assert r.status_code == 401

    def test_access_token_as_refresh_returns_401(self, app_client):
        """An access token must not be accepted as a refresh token."""
        client, _ = app_client
        access_token = _get_tokens(client).json()["access_token"]
        r = client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": access_token,
        })
        assert r.status_code == 401

    def test_missing_refresh_token_returns_422(self, app_client):
        client, _ = app_client
        r = client.post("/auth/refresh", data={"grant_type": "refresh_token"})
        assert r.status_code == 422


# ── /auth/revoke ──────────────────────────────────────────────────────────────

class TestRevokeEndpoint:
    def test_revoke_returns_204(self, app_client):
        client, _ = app_client
        refresh_token = _get_tokens(client).json()["refresh_token"]
        r = client.post("/auth/revoke",
                        json={"refresh_token": refresh_token})
        assert r.status_code == 204

    def test_revoked_token_cannot_be_used(self, app_client):
        client, store = app_client
        refresh_token = _get_tokens(client).json()["refresh_token"]

        # Revoke it
        client.post("/auth/revoke", json={"refresh_token": refresh_token})

        # Try to refresh with it
        r = client.post("/auth/refresh", data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
        })
        assert r.status_code == 401

    def test_revoke_with_garbage_token_returns_204(self, app_client):
        """Revoke must not leak info about token validity — always 204."""
        client, _ = app_client
        r = client.post("/auth/revoke",
                        json={"refresh_token": "not.a.real.token"})
        assert r.status_code == 204

    def test_revoke_with_expired_token_returns_204(self, app_client):
        """Expired tokens should still be revocable (removes from store)."""
        client, _ = app_client
        expired = jwt.encode(
            {"sub": TEST_CLIENT_ID, "type": "refresh", "jti": "exp-jti",
             "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            TEST_JWT_SECRET, algorithm=TEST_ALGORITHM,
        )
        r = client.post("/auth/revoke", json={"refresh_token": expired})
        assert r.status_code == 204


# ── Token store unit tests ────────────────────────────────────────────────────

class TestTokenStore:
    """Tests for token_store.py helpers using a mocked Redis client."""

    @pytest.fixture
    def mock_redis(self):
        store = {}

        class _FakeRedis:
            def setex(self, key, ttl, val): store[key] = val
            def get(self, key): return store.get(key)
            def delete(self, key): store.pop(key, None); return 1
            def eval(self, script, numkeys, *keys):
                # Simplified Lua emulation: GET + DEL
                key = keys[0]
                val = store.get(key)
                if val:
                    del store[key]
                    return val
                return None
            def scan(self, cursor, match=None, count=None):
                return 0, list(store.keys())
            _store = store

        return _FakeRedis()

    def test_store_and_consume(self, mock_redis):
        from src.api import token_store as ts
        with patch.object(ts, "_get_redis", return_value=mock_redis):
            ts.store_refresh_token("jti-1", "client-a", 3600)
            result = ts.validate_and_consume_refresh_token("jti-1")
        assert result == "client-a"

    def test_consume_removes_from_store(self, mock_redis):
        from src.api import token_store as ts
        with patch.object(ts, "_get_redis", return_value=mock_redis):
            ts.store_refresh_token("jti-2", "client-a", 3600)
            ts.validate_and_consume_refresh_token("jti-2")
            result = ts.validate_and_consume_refresh_token("jti-2")  # second use
        assert result is None

    def test_consume_missing_returns_none(self, mock_redis):
        from src.api import token_store as ts
        with patch.object(ts, "_get_redis", return_value=mock_redis):
            result = ts.validate_and_consume_refresh_token("nonexistent-jti")
        assert result is None

    def test_revoke_prevents_consume(self, mock_redis):
        from src.api import token_store as ts
        with patch.object(ts, "_get_redis", return_value=mock_redis):
            ts.store_refresh_token("jti-3", "client-a", 3600)
            ts.revoke_refresh_token("jti-3")
            result = ts.validate_and_consume_refresh_token("jti-3")
        assert result is None

    def test_revoke_all_for_client(self, mock_redis):
        from src.api import token_store as ts
        with patch.object(ts, "_get_redis", return_value=mock_redis):
            ts.store_refresh_token("jti-a1", "client-x", 3600)
            ts.store_refresh_token("jti-a2", "client-x", 3600)
            ts.store_refresh_token("jti-b1", "client-y", 3600)
            revoked = ts.revoke_all_for_client("client-x")
        assert revoked == 2
        # client-y's token should still be there
        assert mock_redis._store.get("refresh:jti-b1") == "client-y"