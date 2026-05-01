"""
IPCSkill – Ibiza (Intune Portal) Token Provider.

Exchanges a ``portalAuthorization`` refresh token for a fresh Graph access
token using the Intune portal's internal DelegationToken endpoint.

How to obtain the initial portalAuthorization
---------------------------------------------
1. Open https://intune.microsoft.com in your browser and sign in.
2. Open DevTools → Network tab and filter requests by "DelegationToken".
3. Click any matching POST request and inspect the **Response** body.
4. Copy the value of the ``portalAuthorization`` field.
5. Also note the ``tid`` (tenant ID) from the same response or from any
   previously captured Bearer token's JWT payload.

The endpoint rotates ``portalAuthorization`` on every call, so this class
always returns and expects callers to persist the *new* value.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import NamedTuple

import requests

logger = logging.getLogger(__name__)

_DELEGATION_URL = "https://intune.microsoft.com/api/DelegationToken"

# The portalId cookie value identifies the Intune portal to the delegation
# endpoint — this is the well-known Intune portal GUID, not tenant-specific.
_PORTAL_ID_COOKIE = "f4a17c62-20c9-44b4-bde0-9206b1578bd2"

_EXTENSION_NAME = "Microsoft_Intune_DeviceSettings"
_RESOURCE_NAME = "microsoft.graph"


class IbizaTokenResult(NamedTuple):
    """Result of a successful DelegationToken call."""

    portal_authorization: str
    """Rotated portalAuthorization (new refresh token) — must be persisted."""

    access_token: str
    """Fresh Graph Bearer access token."""

    expires_at: float
    """Unix timestamp (seconds) when the access token expires."""


class IbizaTokenProviderError(Exception):
    """Raised when the DelegationToken endpoint returns an unexpected response."""


class IbizaTokenProvider:
    """Exchanges a portalAuthorization token for a fresh Graph access token.

    The Intune portal DelegationToken API rotates ``portalAuthorization`` on
    every call. Callers **must** persist the new value from
    :attr:`IbizaTokenResult.portal_authorization` after each successful call.

    Example
    -------
    >>> provider = IbizaTokenProvider()
    >>> result = provider.refresh(portal_auth, tenant_id)
    >>> store(result.portal_authorization)   # persist rotated refresh token
    >>> use(result.access_token)
    """

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def refresh(self, portal_authorization: str, tenant_id: str) -> IbizaTokenResult:
        """Call the DelegationToken endpoint and return fresh tokens.

        Parameters
        ----------
        portal_authorization:
            Current portalAuthorization token from the Intune portal.
        tenant_id:
            Entra ID tenant GUID (e.g. ``"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"``).

        Returns
        -------
        IbizaTokenResult
            Rotated portalAuthorization, new access_token, and expires_at timestamp.

        Raises
        ------
        IbizaTokenProviderError
            If the endpoint is unreachable or returns an unexpected response.
        """
        body = {
            "portalAuthorization": portal_authorization,
            "extensionName": _EXTENSION_NAME,
            "resourceName": _RESOURCE_NAME,
            "tenant": tenant_id,
        }

        try:
            response = self._session.post(
                _DELEGATION_URL,
                json=body,
                cookies={"portalId": _PORTAL_ID_COOKIE},
                timeout=(10, 30),
            )
            response.raise_for_status()
            data = response.json()
        except requests.HTTPError as exc:
            raise IbizaTokenProviderError(
                f"DelegationToken request failed: HTTP {exc.response.status_code} — "
                f"{exc.response.text[:300]}"
            ) from exc
        except requests.RequestException as exc:
            raise IbizaTokenProviderError(
                f"DelegationToken request failed: {exc}"
            ) from exc

        new_portal_auth = data.get("portalAuthorization")
        value = data.get("value") or {}
        auth_header = value.get("authHeader", "")
        expires_at_raw = value.get("expiresAt")

        if not new_portal_auth:
            raise IbizaTokenProviderError(
                "DelegationToken response missing 'portalAuthorization'. "
                f"Response keys: {list(data.keys())}"
            )
        if not auth_header:
            raise IbizaTokenProviderError(
                "DelegationToken response missing 'value.authHeader'."
            )

        # Extract just the JWT from "Bearer <token>"
        access_token = auth_header.split()[-1]
        expires_at = _parse_expires_at(expires_at_raw)

        logger.info(
            "Ibiza token refreshed successfully (expires: %s UTC).",
            datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        )

        return IbizaTokenResult(
            portal_authorization=new_portal_auth,
            access_token=access_token,
            expires_at=expires_at,
        )


def _parse_expires_at(raw: object) -> float:
    """Normalise an expiresAt value to a Unix timestamp in seconds."""
    if raw is None:
        return datetime.now(tz=timezone.utc).timestamp() + 3600.0

    if isinstance(raw, (int, float)):
        ts = float(raw)
        # Guard against millisecond timestamps (> year 2100 if treated as seconds)
        if ts > 4_000_000_000:
            ts /= 1000.0
        return ts

    if isinstance(raw, str):
        # Numeric string
        try:
            return _parse_expires_at(float(raw))
        except ValueError:
            pass
        # ISO-8601 / RFC-3339
        try:
            normalised = raw.rstrip("Z")
            if "+" not in normalised and normalised.count("-") < 3:
                normalised += "+00:00"
            return datetime.fromisoformat(normalised).timestamp()
        except (ValueError, TypeError):
            pass

    logger.warning("Could not parse expiresAt value %r; defaulting to 1 hour.", raw)
    return datetime.now(tz=timezone.utc).timestamp() + 3600.0
