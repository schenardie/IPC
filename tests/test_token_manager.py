"""Tests for IPCSkill TokenManager."""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ipc_skill import IPCSkillConfig, TokenExpiredError, TokenManager
from ipc_skill.config import LocalTokenStoreConfig
from ipc_skill.local_token_store import LocalTokenStore
from ipc_skill.token_manager import TokenRefreshError, _extract_tenant_from_jwt, _normalize_access_token
from ipc_skill.wam_token_provider import WamTokenProviderError, WamTokenResult
from ipc_skill.broci_token_provider import BrociTokenProviderError, BrociTokenResult


def _make_jwt(payload: dict) -> str:
    """Build a fake JWT with the given payload (signature not verified in tests)."""
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{b64({})}.{b64(payload)}.fake-sig"


def _make_manager(config: IPCSkillConfig, tmp_path: Path, wam_provider=None, broci_provider=None) -> tuple[TokenManager, LocalTokenStore]:
    store = LocalTokenStore(store_dir=tmp_path)
    manager = TokenManager(config, token_store=store, wam_provider=wam_provider, broci_provider=broci_provider)
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


def _wam_result(token: str = "wam-access-token", offset: int = 3600) -> WamTokenResult:
    return WamTokenResult(
        access_token=token,
        expires_at=time.time() + offset,
        account_username="user@contoso.com",
        tenant_id="test-tenant",
    )


class TestStoreWamAuth:
    def test_stores_token_and_wam_metadata(self, config: IPCSkillConfig, tmp_path: Path):
        mock_wam = MagicMock()
        mock_wam.refresh.return_value = _wam_result()
        manager, store = _make_manager(config, tmp_path, wam_provider=mock_wam)

        username = manager.store_wam_auth(tenant_id="test-tenant", username="user@contoso.com")

        assert username == "user@contoso.com"
        data = store.load()
        assert data["access_token"] == "wam-access-token"
        assert data["metadata"]["wam_tenant_id"] == "test-tenant"
        assert data["metadata"]["wam_username"] == "user@contoso.com"

    def test_raises_token_refresh_error_when_wam_fails(self, config: IPCSkillConfig, tmp_path: Path):
        mock_wam = MagicMock()
        mock_wam.refresh.side_effect = WamTokenProviderError("broker unavailable")
        manager, _ = _make_manager(config, tmp_path, wam_provider=mock_wam)

        with pytest.raises(TokenRefreshError, match="broker unavailable"):
            manager.store_wam_auth(tenant_id="test-tenant")


class TestWamAutoRefresh:
    def test_silently_refreshes_expired_wam_token(self, config: IPCSkillConfig, tmp_path: Path):
        """Expired token + WAM metadata → WAM provider called, new token returned."""
        mock_wam = MagicMock()
        mock_wam.refresh.return_value = _wam_result(token="new-token")
        manager, store = _make_manager(config, tmp_path, wam_provider=mock_wam)

        past_ts = time.time() - 100
        store.save(
            "old-token",
            metadata={
                "expires_at": past_ts,
                "wam_tenant_id": "test-tenant",
                "wam_username": "user@contoso.com",
            },
        )

        result = manager.get_valid_token()
        assert result == "new-token"
        mock_wam.refresh.assert_called_once_with(
            tenant_id="test-tenant", username="user@contoso.com"
        )

    def test_raises_token_expired_when_no_wam_configured(self, config: IPCSkillConfig, tmp_path: Path):
        """Expired token with no WAM metadata → TokenExpiredError (not WAM)."""
        mock_wam = MagicMock()
        manager, store = _make_manager(config, tmp_path, wam_provider=mock_wam)

        past_ts = time.time() - 100
        store.save("old-token", metadata={"expires_at": past_ts})

        with pytest.raises(TokenExpiredError):
            manager.get_valid_token()
        mock_wam.refresh.assert_not_called()

    def test_raises_token_refresh_error_when_wam_refresh_fails(self, config: IPCSkillConfig, tmp_path: Path):
        """WAM configured but refresh fails → TokenRefreshError propagated."""
        mock_wam = MagicMock()
        mock_wam.refresh.side_effect = WamTokenProviderError("silent refresh failed")
        manager, store = _make_manager(config, tmp_path, wam_provider=mock_wam)

        past_ts = time.time() - 100
        store.save(
            "old-token",
            metadata={"expires_at": past_ts, "wam_tenant_id": "test-tenant"},
        )

        with pytest.raises(TokenRefreshError, match="silent refresh failed"):
            manager.get_valid_token()

    def test_token_info_shows_wam_enabled(self, config: IPCSkillConfig, tmp_path: Path):
        mock_wam = MagicMock()
        manager, store = _make_manager(config, tmp_path, wam_provider=mock_wam)

        future_ts = time.time() + 3600
        jwt = _make_jwt({"exp": int(future_ts), "upn": "user@contoso.com", "tid": "test-tenant"})
        store.save(
            jwt,
            metadata={
                "expires_at": future_ts,
                "wam_tenant_id": "test-tenant",
                "wam_username": "user@contoso.com",
            },
        )

        info = manager.token_info()
        assert info["auto_refresh"] == "WAM (Windows broker)"

    def test_token_info_shows_wam_disabled_when_not_configured(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)

        future_ts = time.time() + 3600
        jwt = _make_jwt({"exp": int(future_ts), "upn": "user@contoso.com", "tid": "tid"})
        store.save(jwt, metadata={"expires_at": future_ts})

        info = manager.token_info()
        assert info["auto_refresh"] == "disabled"


def _broci_result(token: str = "broci-access-token", rt: str = "new-broker-rt", offset: int = 3600) -> BrociTokenResult:
    return BrociTokenResult(
        access_token=token,
        refresh_token=rt,
        expires_at=time.time() + offset,
        tenant_id="test-tenant",
    )


class TestStoreBrociAuth:
    def test_stores_token_and_broci_metadata(self, config: IPCSkillConfig, tmp_path: Path):
        mock_broci = MagicMock()
        jwt = _make_jwt({"upn": "user@contoso.com", "tid": "test-tenant"})
        mock_broci.refresh.return_value = _broci_result(token=jwt)
        manager, store = _make_manager(config, tmp_path, broci_provider=mock_broci)

        upn = manager.store_broci_auth(tenant_id="test-tenant", broker_refresh_token="broker-rt")

        assert upn == "user@contoso.com"
        data = store.load()
        assert data["metadata"]["broci_tenant_id"] == "test-tenant"
        assert data["metadata"]["broci_broker_rt"] == "new-broker-rt"

    def test_raises_token_refresh_error_when_broci_fails(self, config: IPCSkillConfig, tmp_path: Path):
        mock_broci = MagicMock()
        mock_broci.refresh.side_effect = BrociTokenProviderError("reply address mismatch")
        manager, _ = _make_manager(config, tmp_path, broci_provider=mock_broci)

        with pytest.raises(TokenRefreshError, match="reply address mismatch"):
            manager.store_broci_auth(tenant_id="test-tenant", broker_refresh_token="rt")


class TestBrociAutoRefresh:
    def test_silently_refreshes_expired_broci_token(self, config: IPCSkillConfig, tmp_path: Path):
        mock_broci = MagicMock()
        mock_broci.refresh.return_value = _broci_result(token="new-at", rt="new-rt")
        manager, store = _make_manager(config, tmp_path, broci_provider=mock_broci)

        past_ts = time.time() - 100
        store.save(
            "old-token",
            metadata={
                "expires_at": past_ts,
                "broci_tenant_id": "test-tenant",
                "broci_broker_rt": "old-rt",
            },
        )

        result = manager.get_valid_token()
        assert result == "new-at"
        mock_broci.refresh.assert_called_once_with(
            tenant_id="test-tenant", broker_refresh_token="old-rt"
        )
        # Persisted new RT
        assert store.load()["metadata"]["broci_broker_rt"] == "new-rt"

    def test_raises_token_expired_when_neither_wam_nor_broci_configured(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        store.save("old", metadata={"expires_at": time.time() - 100})

        with pytest.raises(TokenExpiredError):
            manager.get_valid_token()

    def test_raises_token_refresh_error_when_broci_exchange_fails(self, config: IPCSkillConfig, tmp_path: Path):
        mock_broci = MagicMock()
        mock_broci.refresh.side_effect = BrociTokenProviderError("invalid_grant")
        manager, store = _make_manager(config, tmp_path, broci_provider=mock_broci)

        store.save(
            "old",
            metadata={"expires_at": time.time() - 100, "broci_tenant_id": "t", "broci_broker_rt": "rt"},
        )

        with pytest.raises(TokenRefreshError, match="invalid_grant"):
            manager.get_valid_token()

    def test_token_info_shows_broci_enabled(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        future_ts = time.time() + 3600
        jwt = _make_jwt({"exp": int(future_ts), "upn": "user@contoso.com", "tid": "t"})
        store.save(jwt, metadata={"expires_at": future_ts, "broci_tenant_id": "t", "broci_broker_rt": "rt"})

        info = manager.token_info()
        assert info["auto_refresh"] == "BroCI (NAA broker)"

    def test_wam_takes_priority_over_broci_when_both_configured(self, config: IPCSkillConfig, tmp_path: Path):
        """If both WAM and BroCI metadata are stored, WAM is attempted first."""
        mock_wam = MagicMock()
        mock_wam.refresh.return_value = _wam_result(token="wam-token")
        mock_broci = MagicMock()
        manager, store = _make_manager(config, tmp_path, wam_provider=mock_wam, broci_provider=mock_broci)

        store.save(
            "old",
            metadata={
                "expires_at": time.time() - 100,
                "wam_tenant_id": "t",
                "broci_tenant_id": "t",
                "broci_broker_rt": "rt",
            },
        )

        result = manager.get_valid_token()
        assert result == "wam-token"
        mock_wam.refresh.assert_called_once()
        mock_broci.refresh.assert_not_called()


class TestClearToken:
    def test_clear_removes_stored_token(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        future_ts = time.time() + 3600
        jwt = _make_jwt({"exp": int(future_ts), "upn": "user@contoso.com", "tid": "t"})
        store.save(jwt, metadata={"expires_at": future_ts})

        assert manager.token_info() is not None
        manager.clear_token()
        assert manager.token_info() is None

    def test_clear_removes_broci_metadata(self, config: IPCSkillConfig, tmp_path: Path):
        manager, store = _make_manager(config, tmp_path)
        future_ts = time.time() + 3600
        jwt = _make_jwt({"exp": int(future_ts), "upn": "user@contoso.com", "tid": "t"})
        store.save(jwt, metadata={"expires_at": future_ts, "broci_tenant_id": "t", "broci_broker_rt": "rt"})

        manager.clear_token()
        assert manager.token_info() is None
