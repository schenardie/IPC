"""
IPCSkill – Token Manager.

Handles:
- Accepting a raw token provided by the user (copy-pasted from a browser / Intune portal).
- Storing the access token encrypted on local disk or OS keyring.
- Returning a valid Bearer token for use in downstream Graph API calls.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from .config import IPCSkillConfig
from .keyring_token_store import KeyringTokenStore
from .local_token_store import LocalTokenStore
from .wam_token_provider import WamTokenProvider, WamTokenProviderError
from .broci_token_provider import BrociTokenProvider, BrociTokenProviderError

logger = logging.getLogger(__name__)


_JWT_PATTERN = re.compile(r"([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")


def _normalize_access_token(raw_token: str) -> str:
    """Normalize user-pasted token input to the raw JWT access token."""
    token = (raw_token or "").strip()

    if token.lower().startswith("authorization:"):
        token = token.split(":", 1)[1].strip()

    while len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        token = token[1:-1].strip()

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    match = _JWT_PATTERN.search(token)
    if match:
        return match.group(1)

    return token


def _decode_jwt_payload(token: str) -> dict:
    """Decode the payload section of a JWT without verifying the signature."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore padding
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def _extract_tenant_from_jwt(token: str) -> str | None:
    """Return the ``tid`` claim from a JWT without verifying the signature."""
    return _decode_jwt_payload(token).get("tid")


def _extract_expiry_from_jwt(token: str) -> float | None:
    """Return the ``exp`` claim (Unix timestamp) from a JWT, or ``None`` if absent."""
    exp = _decode_jwt_payload(token).get("exp")
    return float(exp) if exp is not None else None


class TokenExpiredError(Exception):
    """Raised when the stored access token has expired and no auto-refresh is available."""


class TokenRefreshError(Exception):
    """Raised when a token refresh operation fails."""


class TokenManager:
    """Manages user-delegated OAuth2 tokens for Microsoft Graph API access.

    Tokens are persisted in an encrypted local file (or OS keyring) so they
    survive process restarts.

    Typical flow
    ------------
    1. Call :meth:`store_token` with a token copied from the browser / Intune portal.
    2. On subsequent runs call :meth:`get_valid_token`.  A :exc:`TokenExpiredError`
       is raised when the token has expired — paste a fresh one via :meth:`store_token`.
    """

    def __init__(
        self,
        config: IPCSkillConfig,
        token_store: Optional[KeyringTokenStore | LocalTokenStore] = None,
        wam_provider: Optional[WamTokenProvider] = None,
        broci_provider: Optional[BrociTokenProvider] = None,
    ) -> None:
        self._config = config
        self._store = token_store or KeyringTokenStore(config.token_store.store_dir or None)
        self._wam = wam_provider or WamTokenProvider()
        self._broci = broci_provider or BrociTokenProvider()

    def store_token(
        self,
        access_token: str,
        expires_in: Optional[int] = None,
    ) -> None:
        """Persist a token encrypted on local disk or OS keyring.

        Parameters
        ----------
        access_token:
            The Bearer access token copied from the browser or Intune portal.
        expires_in:
            Lifetime of the access token in seconds from *now*.
            Ignored when the token contains a valid ``exp`` claim (always true
            for Azure AD tokens).
        """
        access_token = _normalize_access_token(access_token)

        jwt_exp = _extract_expiry_from_jwt(access_token)
        if jwt_exp is not None:
            expiry_ts = jwt_exp
        elif expires_in is not None:
            expiry_ts = datetime.now(tz=timezone.utc).timestamp() + expires_in
        else:
            expiry_ts = datetime.now(tz=timezone.utc).timestamp() + 3600

        # Preserve tenant_id when only updating the access token
        try:
            _, existing_meta = self._load_token()
            tenant_id = existing_meta.get("tenant_id")
        except (FileNotFoundError, KeyError):
            tenant_id = None

        self._store.save(
            access_token=access_token,
            metadata={
                "expires_at": expiry_ts,
                "tenant_id": tenant_id,
            },
        )

        logger.info(
            "Token stored (expiry: %s UTC)",
            datetime.fromtimestamp(expiry_ts, tz=timezone.utc).isoformat(),
        )

    def store_wam_auth(
        self,
        tenant_id: str,
        username: str | None = None,
    ) -> str:
        """Acquire a fresh token via the Windows WAM broker and store it.

        On success the acquired token is persisted (with WAM metadata so
        :meth:`get_valid_token` can silently refresh it later) and the
        resolved username is returned.

        Parameters
        ----------
        tenant_id:
            Your Entra ID tenant GUID.
        username:
            Optional UPN hint (e.g. ``user@contoso.com``).

        Returns
        -------
        str
            The UPN of the account that was used.

        Raises
        ------
        TokenRefreshError
            If WAM token acquisition fails.
        """
        try:
            result = self._wam.refresh(tenant_id=tenant_id, username=username)
        except WamTokenProviderError as exc:
            raise TokenRefreshError(str(exc)) from exc

        self._store.save(
            access_token=result.access_token,
            metadata={
                "expires_at": result.expires_at,
                "tenant_id": tenant_id,
                "wam_tenant_id": tenant_id,
                "wam_username": result.account_username,
            },
        )
        logger.info(
            "WAM token stored for %s (expiry: %s UTC)",
            result.account_username,
            datetime.fromtimestamp(result.expires_at, tz=timezone.utc).isoformat(),
        )
        return result.account_username

    def store_broci_auth(
        self,
        tenant_id: str,
        broker_refresh_token: str,
    ) -> str:
        """Perform a BroCI exchange, store the result, and return the resolved UPN.

        The broker refresh token (from the Azure Portal / ``c44b4083`` session)
        is exchanged for an Intune access token via the NAA/BroCI flow.  Both
        the new access token and the updated broker RT are persisted so
        :meth:`get_valid_token` can silently re-exchange whenever the access
        token expires.

        Parameters
        ----------
        tenant_id:
            Your Entra ID tenant GUID.
        broker_refresh_token:
            A refresh token belonging to the Azure Portal application
            (``c44b4083``).  Obtained from ``capture_portal_auth.py`` (saved
            to ``broker_rt.json``).

        Returns
        -------
        str
            The UPN extracted from the issued access token.

        Raises
        ------
        TokenRefreshError
            If the BroCI exchange fails.
        """
        try:
            result = self._broci.refresh(
                tenant_id=tenant_id,
                broker_refresh_token=broker_refresh_token,
            )
        except BrociTokenProviderError as exc:
            raise TokenRefreshError(str(exc)) from exc

        upn = _decode_jwt_payload(result.access_token).get(
            "upn"
        ) or _decode_jwt_payload(result.access_token).get("unique_name", "unknown")

        self._store.save(
            access_token=result.access_token,
            metadata={
                "expires_at": result.expires_at,
                "tenant_id": tenant_id,
                "broci_tenant_id": tenant_id,
                "broci_broker_rt": result.refresh_token,
            },
        )
        logger.info(
            "BroCI token stored for %s (expiry: %s UTC)",
            upn,
            datetime.fromtimestamp(result.expires_at, tz=timezone.utc).isoformat(),
        )
        return upn

    def clear_token(self) -> None:
        """Remove all stored token data (access token, refresh token, metadata).

        Use this to switch tenants or accounts.  After clearing, re-authenticate
        via :meth:`store_token` (manual paste) or :meth:`store_broci_auth` (BroCI).
        """
        self._store.clear()
        logger.info("Token store cleared.")

    def token_info(self) -> Optional[dict]:
        """Return human-readable info about the stored token, or ``None`` if none stored."""
        try:
            access_token, metadata = self._load_token()
        except FileNotFoundError:
            return None

        payload = _decode_jwt_payload(access_token)
        now = datetime.now(tz=timezone.utc).timestamp()
        expires_at_ts = metadata.get("expires_at") or payload.get("exp") or 0
        expires_at_dt = datetime.fromtimestamp(expires_at_ts, tz=timezone.utc)
        seconds_left = expires_at_ts - now

        last_refreshed_ts = metadata.get("last_refreshed")
        last_refreshed = (
            datetime.fromtimestamp(last_refreshed_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if last_refreshed_ts else None
        )

        return {
            "user": payload.get("upn") or payload.get("unique_name") or payload.get("preferred_username", "unknown"),
            "tenant": payload.get("tid") or metadata.get("tenant_id", "unknown"),
            "expires_at": expires_at_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "expires_in": f"{int(seconds_left // 3600)}h {int((seconds_left % 3600) // 60)}m" if seconds_left > 0 else "EXPIRED",
            "expired": seconds_left <= 0,
            "auto_refresh": (
                "WAM (Windows broker)" if metadata.get("wam_tenant_id")
                else "BroCI (NAA broker)" if metadata.get("broci_tenant_id")
                else "disabled"
            ),
            "last_refreshed": last_refreshed,
        }

    def get_valid_token(self) -> str:
        """Return a valid Bearer access token, auto-refreshing if necessary.

        If a ``portalAuthorization`` refresh token is stored and the access
        token is expired, the token is automatically refreshed via the Intune
        DelegationToken endpoint before returning.

        Raises
        ------
        TokenExpiredError
            If the stored token has expired and no auto-refresh is configured.
        TokenRefreshError
            If auto-refresh was attempted but failed.
        FileNotFoundError
            If no token has been stored yet.
        """
        access_token, metadata = self._load_token()
        access_token = _normalize_access_token(access_token)

        expires_at = metadata.get("expires_at", 0)
        now = datetime.now(tz=timezone.utc).timestamp()

        if now < expires_at:
            logger.debug("Using cached access token (expires in %.0f s).", expires_at - now)
            return access_token

        # Token expired — try WAM silent refresh if configured
        wam_tenant = metadata.get("wam_tenant_id")
        if wam_tenant:
            logger.info("Access token expired — attempting WAM silent refresh …")
            try:
                wam_username = metadata.get("wam_username")
                result = self._wam.refresh(tenant_id=wam_tenant, username=wam_username)
                self._store.save(
                    access_token=result.access_token,
                    metadata={
                        "expires_at": result.expires_at,
                        "tenant_id": wam_tenant,
                        "wam_tenant_id": wam_tenant,
                        "wam_username": result.account_username,
                        "last_refreshed": time.time(),
                    },
                )
                logger.info("WAM silent refresh succeeded.")
                print("[info] Token auto-refreshed via WAM.")
                return result.access_token
            except WamTokenProviderError as exc:
                raise TokenRefreshError(f"WAM auto-refresh failed: {exc}") from exc

        # Token expired — try BroCI broker exchange if configured
        broci_tenant = metadata.get("broci_tenant_id")
        broci_rt = metadata.get("broci_broker_rt")
        if broci_tenant and broci_rt:
            logger.info("Access token expired — attempting BroCI silent refresh …")
            try:
                result = self._broci.refresh(
                    tenant_id=broci_tenant,
                    broker_refresh_token=broci_rt,
                )
                self._store.save(
                    access_token=result.access_token,
                    metadata={
                        "expires_at": result.expires_at,
                        "tenant_id": broci_tenant,
                        "broci_tenant_id": broci_tenant,
                        "broci_broker_rt": result.refresh_token,
                        "last_refreshed": time.time(),
                    },
                )
                logger.info("BroCI silent refresh succeeded.")
                print("[info] Token auto-refreshed via BroCI.")
                return result.access_token
            except BrociTokenProviderError as exc:
                raise TokenRefreshError(f"BroCI auto-refresh failed: {exc}") from exc

        raise TokenExpiredError(
            "Access token has expired. Use option 1 to paste a fresh token, "
            "option 1b to enable WAM auto-refresh (Windows), "
            "or option 1c to enable BroCI auto-refresh (cross-platform)."
        )

    def _load_token(self) -> tuple[str, dict]:
        data = self._store.load()
        return data["access_token"], data.get("metadata", {})
