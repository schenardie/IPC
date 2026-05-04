"""
IPCSkill – WAM Token Provider.

Uses the Windows Account Manager (WAM) broker via MSAL to silently acquire
tokens using the Windows Primary Refresh Token (PRT) — the same mechanism
the Intune portal uses to keep itself signed in indefinitely.

Requirements
------------
- Windows 10/11 with the user signed in to their AAD/Entra account
- ``pip install "msal[broker]"``  (adds ``pymsalruntime``)

Non-Windows platforms
---------------------
Import succeeds but :meth:`WamTokenProvider.refresh` raises
:exc:`WamTokenProviderError` with a clear message.
"""
from __future__ import annotations

import logging
import sys
from typing import NamedTuple

logger = logging.getLogger(__name__)

INTUNE_CLIENT_ID = "5926fc8e-304e-4f59-8bed-58ca97cc39a4"
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]


class WamTokenResult(NamedTuple):
    access_token: str
    expires_at: float          # Unix timestamp
    account_username: str
    tenant_id: str


class WamTokenProviderError(Exception):
    """Raised when WAM token acquisition fails."""


class WamTokenProvider:
    """Acquires Microsoft Graph tokens via the Windows WAM broker (PRT).

    On first use :meth:`refresh` will open a WAM login dialog if the account
    is not already known.  On subsequent calls it acquires tokens silently
    with no user interaction.

    Example
    -------
    >>> provider = WamTokenProvider()
    >>> result = provider.refresh(tenant_id="your-tenant-guid")
    >>> print(result.access_token[:40])
    """

    def __init__(self, client_id: str = INTUNE_CLIENT_ID) -> None:
        self._client_id = client_id
        self._app = None          # lazily created

    def _get_app(self, tenant_id: str):
        """Return (or lazily create) the MSAL PublicClientApplication."""
        if self._app is not None:
            return self._app
        try:
            import msal  # noqa: PLC0415
        except ImportError as exc:
            raise WamTokenProviderError(
                "msal is not installed. Run: pip install \"msal[broker]\""
            ) from exc

        if sys.platform != "win32":
            raise WamTokenProviderError(
                "WAM broker is only supported on Windows. "
                "Use option 1 to paste a bearer token manually."
            )

        try:
            import pymsalruntime  # noqa: F401, PLC0415
        except ImportError as exc:
            raise WamTokenProviderError(
                "pymsalruntime is not installed. Run: pip install \"msal[broker]\""
            ) from exc

        authority = f"https://login.microsoftonline.com/{tenant_id}"
        self._app = msal.PublicClientApplication(
            self._client_id,
            authority=authority,
            enable_broker_on_windows=True,
        )
        return self._app

    def get_accounts(self, tenant_id: str = "organizations") -> list[dict]:
        """Return WAM accounts known to MSAL for this client."""
        try:
            app = self._get_app(tenant_id)
            return app.get_accounts() or []
        except WamTokenProviderError:
            return []

    def refresh(
        self,
        tenant_id: str,
        username: str | None = None,
    ) -> WamTokenResult:
        """Acquire a fresh access token via the Windows WAM broker.

        Tries silent acquisition first (no UI).  Falls back to interactive
        WAM dialog if no cached account is found.

        Parameters
        ----------
        tenant_id:
            Your Entra ID tenant GUID (e.g. ``"73925f60-..."``) or
            ``"organizations"`` to match any work account.
        username:
            Optional UPN hint to select the right account when multiple
            are available.

        Returns
        -------
        WamTokenResult

        Raises
        ------
        WamTokenProviderError
            On any failure (not Windows, missing library, auth error, etc.).
        """
        app = self._get_app(tenant_id)

        # Try silent first
        accounts = app.get_accounts(username=username) if username else app.get_accounts()
        result = None

        if accounts:
            account = accounts[0]
            logger.debug("WAM: silent acquire for %s", account.get("username"))
            result = app.acquire_token_silent(GRAPH_SCOPES, account=account)

        if not result:
            logger.info("WAM: no cached account — launching interactive WAM dialog")
            result = app.acquire_token_interactive(
                GRAPH_SCOPES,
                login_hint=username,
            )

        if "access_token" not in result:
            error = result.get("error", "unknown")
            desc = result.get("error_description", "")
            raise WamTokenProviderError(
                f"WAM token acquisition failed: {error} — {desc}"
            )

        import time  # noqa: PLC0415
        expires_at = time.time() + int(result.get("expires_in", 3600))

        # Resolve account details from the token cache
        all_accounts = app.get_accounts() or []
        matched = next(
            (a for a in all_accounts if username and a.get("username") == username),
            all_accounts[0] if all_accounts else {},
        )
        resolved_username = matched.get("username", "unknown")
        resolved_tid = (
            matched.get("home_account_id", ".").split(".")[-1]
            or tenant_id
        )

        return WamTokenResult(
            access_token=result["access_token"],
            expires_at=expires_at,
            account_username=resolved_username,
            tenant_id=resolved_tid,
        )
