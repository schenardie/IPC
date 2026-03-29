"""
IPCSkill – Keyring-backed token store.

Stores tokens in the OS native credential store:
  - macOS  : Keychain
  - Windows: Credential Manager (DPAPI-protected)
  - Linux  : Secret Service (libsecret / gnome-keyring)

If no keyring backend is available (Docker, CI, headless servers) it
transparently falls back to the Fernet-encrypted local file store.

The keyring service name is ``explorer-skill`` — the same used by ExplorerSkill —
so tokens stored by either skill are automatically available to the other.
"""
from __future__ import annotations

import json
import logging

import keyring
import keyring.errors

from .local_token_store import LocalTokenStore

logger = logging.getLogger(__name__)

_SERVICE = "explorer-skill"
_KEY_ACCESS = "access-token"
_KEY_REFRESH = "refresh-token"
_KEY_METADATA = "token-metadata"


class KeyringTokenStore:
    """Token store backed by the OS native credential manager.

    Falls back to :class:`LocalTokenStore` automatically when no keyring
    backend is available (e.g. inside Docker).
    """

    def __init__(self, store_dir: str | None = None) -> None:
        self._fallback = LocalTokenStore(store_dir)
        self._use_keyring = self._keyring_available()

    def save(
        self,
        access_token: str,
        refresh_token: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        if not self._use_keyring:
            self._fallback.save(access_token, refresh_token=refresh_token, metadata=metadata)
            return

        try:
            keyring.set_password(_SERVICE, _KEY_ACCESS, access_token)
            keyring.set_password(_SERVICE, _KEY_METADATA, json.dumps(metadata or {}))
            if refresh_token:
                keyring.set_password(_SERVICE, _KEY_REFRESH, refresh_token)
            elif self._refresh_stored():
                keyring.delete_password(_SERVICE, _KEY_REFRESH)
            logger.info("Token stored in OS keyring (%s).", keyring.get_keyring().__class__.__name__)
        except Exception as exc:
            logger.warning("Keyring write failed (%s); falling back to encrypted file.", exc)
            self._use_keyring = False
            self._fallback.save(access_token, refresh_token=refresh_token, metadata=metadata)

    def load(self) -> dict:
        if not self._use_keyring:
            return self._fallback.load()

        try:
            access_token = keyring.get_password(_SERVICE, _KEY_ACCESS)
        except Exception as exc:
            logger.warning("Keyring read failed (%s); falling back to encrypted file.", exc)
            self._use_keyring = False
            return self._fallback.load()

        if access_token is None:
            try:
                return self._fallback.load()
            except FileNotFoundError:
                pass
            raise FileNotFoundError(
                "No token found in OS keyring or local store. "
                "Store a token first via store_token()."
            )

        raw_meta = keyring.get_password(_SERVICE, _KEY_METADATA) or "{}"
        refresh_token = keyring.get_password(_SERVICE, _KEY_REFRESH)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "metadata": json.loads(raw_meta),
        }

    def clear(self) -> None:
        if not self._use_keyring:
            self._fallback.clear()
            return

        for key in (_KEY_ACCESS, _KEY_REFRESH, _KEY_METADATA):
            try:
                keyring.delete_password(_SERVICE, key)
            except keyring.errors.PasswordDeleteError:
                pass

    @staticmethod
    def _keyring_available() -> bool:
        try:
            backend = keyring.get_keyring()
            backend_name = type(backend).__name__
            if "fail" in backend_name.lower() or "null" in backend_name.lower():
                logger.info(
                    "No keyring backend available (%s); using encrypted file store.", backend_name
                )
                return False
            return True
        except Exception:
            return False

    @staticmethod
    def _refresh_stored() -> bool:
        try:
            return keyring.get_password(_SERVICE, _KEY_REFRESH) is not None
        except Exception:
            return False
