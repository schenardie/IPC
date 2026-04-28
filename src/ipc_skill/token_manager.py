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
from datetime import datetime, timezone
from typing import Optional

from .config import IPCSkillConfig
from .keyring_token_store import KeyringTokenStore
from .local_token_store import LocalTokenStore

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
    """Raised when the stored access token has expired."""


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
    ) -> None:
        self._config = config
        self._store = token_store or KeyringTokenStore(config.token_store.store_dir or None)

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

        self._store.save(
            access_token=access_token,
            metadata={"expires_at": expiry_ts},
        )

        logger.info(
            "Token stored (expiry: %s UTC)",
            datetime.fromtimestamp(expiry_ts, tz=timezone.utc).isoformat(),
        )

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

        return {
            "user": payload.get("upn") or payload.get("unique_name") or payload.get("preferred_username", "unknown"),
            "tenant": payload.get("tid", "unknown"),
            "expires_at": expires_at_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "expires_in": f"{int(seconds_left // 3600)}h {int((seconds_left % 3600) // 60)}m" if seconds_left > 0 else "EXPIRED",
            "expired": seconds_left <= 0,
        }

    def get_valid_token(self) -> str:
        """Return the stored Bearer access token if it is still valid.

        Raises
        ------
        TokenExpiredError
            If the stored token has expired.
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

        raise TokenExpiredError(
            "Access token has expired. Please paste a fresh token via option 1."
        )

    def _load_token(self) -> tuple[str, dict]:
        data = self._store.load()
        return data["access_token"], data.get("metadata", {})
