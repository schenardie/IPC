"""Tests for IPCSkill TokenManager."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ipc_skill import IPCSkillConfig, TokenExpiredError, TokenManager
from ipc_skill.config import LocalTokenStoreConfig
from ipc_skill.local_token_store import LocalTokenStore
from ipc_skill.token_manager import _extract_tenant_from_jwt, _normalize_access_token


def _make_jwt(payload: dict) -> str:
    """Build a fake JWT with the given payload (signature not verified in tests)."""
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{b64({})}.{b64(payload)}.fake-sig"


def _make_manager(config: IPCSkillConfig, tmp_path: Path) -> tuple[TokenManager, LocalTokenStore]:
    store = LocalTokenStore(store_dir=tmp_path)
    manager = TokenManager(config, token_store=store)
    return manager, store


class TestExtractTenantFromJwt:
    def test_extracts_tid_from_valid_jwt(self):
        token = _make_jwt({"tid": "my-tenant-id", "oid": "some-user"})
        assert _extract_tenant_from_jwt(token) == "my-tenant-id"

    def test_returns_none_when_tid_missing(self):
        token = _make_jwt({"oid": "some-user"})
        assert _extract_tenant_from_jwt(token) is None

    def test_returns_none_for_garbage_input(self):
        assert _extract_tenant_from_jwt("not.a.jwt") is None
        assert _extract_tenant_from_jwt("garbage") is None


class TestStoreToken:
    def test_stores_access_token(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        manager.store_token(access_token="my-access-token")
        assert store.load()["access_token"] == "my-access-token"

    def test_stores_expiry_in_metadata(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        manager.store_token(access_token="at", expires_in=7200)
        metadata = store.load()["metadata"]
        now = datetime.now(tz=timezone.utc).timestamp()
        assert metadata["expires_at"] > now + 7000

    def test_strips_bearer_prefix_before_storing(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        jwt = _make_jwt({"exp": int(datetime.now(tz=timezone.utc).timestamp()) + 3600})
        manager.store_token(access_token=f"Bearer {jwt}")
        assert store.load()["access_token"] == jwt

    def test_strips_authorization_header_before_storing(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        jwt = _make_jwt({"exp": int(datetime.now(tz=timezone.utc).timestamp()) + 3600})
        manager.store_token(access_token=f"Authorization: Bearer {jwt}")
        assert store.load()["access_token"] == jwt


class TestGetValidToken:
    def test_returns_valid_token(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        future_ts = datetime.now(tz=timezone.utc).timestamp() + 3600
        store.save("valid-token", metadata={"expires_at": future_ts})
        assert manager.get_valid_token() == "valid-token"

    def test_raises_when_expired(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        past_ts = datetime.now(tz=timezone.utc).timestamp() - 100
        store.save("expired-token", metadata={"expires_at": past_ts})
        with pytest.raises(TokenExpiredError):
            manager.get_valid_token()

    def test_returns_normalized_token_for_legacy_stored_header_value(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        future_ts = datetime.now(tz=timezone.utc).timestamp() + 3600
        jwt = _make_jwt({"exp": int(future_ts)})
        store.save(f"Authorization: Bearer {jwt}", metadata={"expires_at": future_ts})
        assert manager.get_valid_token() == jwt


class TestNormalizeAccessToken:
    def test_extracts_from_authorization_header(self):
        jwt = _make_jwt({"exp": 9999999999})
        assert _normalize_access_token(f"Authorization: Bearer {jwt}") == jwt

    def test_extracts_from_quoted_bearer(self):
        jwt = _make_jwt({"exp": 9999999999})
        assert _normalize_access_token(f'"Bearer {jwt}"') == jwt
