"""
IPCSkill – BroCI Token Provider.

Acquires Microsoft Graph tokens via the NAA/BroCI (Nested App Authentication /
Brokered Client ID) flow — a standard OAuth2 refresh-token exchange to
``login.microsoftonline.com`` with extra broker parameters.

This is the cross-platform alternative to the Windows WAM broker.  It requires
a refresh token belonging to a Microsoft portal host application (e.g. Azure
Portal, ``c44b4083-3bb0-49c1-b47d-974e53cbdf3c``) which acts as the broker.

Token lifetime
--------------
BroCI tokens use the SPA single-day refresh-token window.  Each successful
exchange returns a *new* refresh token, so as long as this provider is called at
least once every 24 hours the session stays alive indefinitely — exactly what
the browser portal does.

Reference
---------
https://specterops.io/blog/2025/08/13/going-for-brokering-offensive-walkthrough-for-nested-app-authentication/
https://specterops.io/blog/2025/10/15/naa-or-broci-let-me-explain/
"""
from __future__ import annotations

import logging
import time
from typing import NamedTuple

import requests

logger = logging.getLogger(__name__)

# Azure Portal – acts as the broker (host application)
AZURE_PORTAL_CLIENT_ID = "c44b4083-3bb0-49c1-b47d-974e53cbdf3c"
AZURE_PORTAL_URL = "https://portal.azure.com/"

# Intune portal extension – the target (nested application)
INTUNE_CLIENT_ID = "5926fc8e-304e-4f59-8bed-58ca97cc39a4"

GRAPH_SCOPE = "https://graph.microsoft.com/.default"

_TOKEN_ENDPOINT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


class BrociTokenResult(NamedTuple):
    access_token: str
    refresh_token: str          # New RT — store this for the next refresh
    expires_at: float           # Unix timestamp
    tenant_id: str


class BrociTokenProviderError(Exception):
    """Raised when a BroCI token exchange fails."""


class BrociTokenProvider:
    """Refreshes Intune Graph tokens via the NAA/BroCI broker flow.

    The broker is the Azure Portal application (``c44b4083``).  Its refresh
    token is exchanged for a fresh Intune access token (``5926fc8e``) using the
    BroCI broker parameters in the POST body.

    The response also contains a new refresh token, which should be persisted
    and used for the next call — this keeps the 24-hour SPA window rolling.

    Example
    -------
    >>> provider = BrociTokenProvider()
    >>> result = provider.refresh(
    ...     tenant_id="your-tenant-guid",
    ...     broker_refresh_token=stored_rt,
    ... )
    >>> print(result.access_token[:40])
    """

    def __init__(
        self,
        broker_client_id: str = AZURE_PORTAL_CLIENT_ID,
        broker_url: str = AZURE_PORTAL_URL,
        target_client_id: str = INTUNE_CLIENT_ID,
        scope: str = GRAPH_SCOPE,
        timeout: int = 30,
    ) -> None:
        self._broker_client_id = broker_client_id
        self._broker_url = broker_url
        self._target_client_id = target_client_id
        self._scope = scope
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Origin": broker_url.rstrip("/"),
                "Referer": broker_url if broker_url.endswith("/") else broker_url + "/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
                ),
            }
        )

    def refresh(
        self,
        tenant_id: str,
        broker_refresh_token: str,
    ) -> BrociTokenResult:
        """Exchange a broker refresh token for a fresh Intune access token.

        Parameters
        ----------
        tenant_id:
            Your Entra ID tenant GUID.
        broker_refresh_token:
            A valid refresh token for the broker application (Azure Portal,
            ``c44b4083``).  Obtained from ``capture_portal_auth.py`` or stored
            from a previous :meth:`refresh` call.

        Returns
        -------
        BrociTokenResult
            Contains the new access token, the new refresh token (persist it!),
            and expiry information.

        Raises
        ------
        BrociTokenProviderError
            On any auth error or unexpected response.
        """
        url = _TOKEN_ENDPOINT.format(tenant=tenant_id)

        # The BroCI POST body:
        #   client_id     = target app (what token we want)
        #   redirect_uri  = brk-<broker_client_id>://<broker_host>
        #   brk_client_id = broker (who holds the RT we're redeeming)
        #   brk_redirect_uri = broker URL
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._target_client_id,
            "scope": self._scope,
            "refresh_token": broker_refresh_token,
            "redirect_uri": f"brk-{self._broker_client_id}://{_host(self._broker_url)}",
            "brk_client_id": self._broker_client_id,
            "brk_redirect_uri": self._broker_url
            if self._broker_url.endswith("/")
            else self._broker_url + "/",
        }

        logger.debug(
            "BroCI exchange: broker=%s target=%s tenant=%s",
            self._broker_client_id[:8],
            self._target_client_id[:8],
            tenant_id[:8],
        )

        try:
            resp = self._session.post(
                url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            result = resp.json()
        except requests.RequestException as exc:
            raise BrociTokenProviderError(
                f"HTTP request to token endpoint failed: {exc}"
            ) from exc

        if "access_token" not in result:
            error = result.get("error", "unknown_error")
            desc = result.get("error_description", "")
            raise BrociTokenProviderError(
                f"BroCI token exchange failed: {error} — {desc}"
            )

        expires_at = time.time() + int(result.get("expires_in", 3600))

        return BrociTokenResult(
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token", broker_refresh_token),
            expires_at=expires_at,
            tenant_id=tenant_id,
        )


def _host(url: str) -> str:
    """Extract the hostname from a URL, e.g. ``https://portal.azure.com/`` → ``portal.azure.com``."""
    from urllib.parse import urlparse  # noqa: PLC0415
    return urlparse(url).netloc
