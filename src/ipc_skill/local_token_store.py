"""
ExplorerSkill – Local encrypted token store.

Tokens are stored in a single Fernet (AES-128-CBC + HMAC-SHA256) encrypted
JSON file.  The encryption key lives in a separate file with owner-only
permissions so other users on the same machine cannot read it.

File layout (all inside ``store_dir``, default ``~/.explorer_skill``):
    key.bin    – Fernet key, owner read/write only (chmod 600 on Unix, icacls on Windows)
    tokens.enc – Encrypted JSON payload
"""
from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet

_DEFAULT_STORE_DIR = Path.home() / ".explorer_skill"
_KEY_FILENAME = "key.bin"
_TOKEN_FILENAME = "tokens.enc"


def _restrict_to_owner(path: Path) -> None:
    """Restrict *path* to be readable/writable by the current user only.

    Uses ``chmod 600`` on macOS/Linux and ``icacls`` on Windows.
    Failures are silently swallowed so a permissions error never breaks the
    main flow — the file is still encrypted even without tight ACLs.
    """
    if platform.system() == "Windows":
        try:
            username = os.environ.get("USERNAME", "")
            if username:
                subprocess.run(
                    [
                        "icacls", str(path),
                        "/inheritance:r",          # remove inherited ACEs
                        "/grant:r", f"{username}:F",  # owner: full control
                    ],
                    check=True,
                    capture_output=True,
                )
        except Exception:
            pass
    else:
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        except (NotImplementedError, AttributeError, OSError):
            pass


class LocalTokenStore:
    """Fernet-encrypted local token store.

    Example
    -------
    >>> store = LocalTokenStore()
    >>> store.save("my-access-token", refresh_token="my-refresh-token", metadata={"expires_at": 9999999999})
    >>> data = store.load()
    >>> data["access_token"]
    'my-access-token'
    """

    def __init__(self, store_dir: str | Path | None = None) -> None:
        self._dir = Path(store_dir).expanduser() if store_dir else _DEFAULT_STORE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_or_create_key())

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def save(
        self,
        access_token: str,
        refresh_token: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Encrypt and persist token data to disk."""
        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "metadata": metadata or {},
        }
        encrypted = self._fernet.encrypt(json.dumps(data).encode())
        (self._dir / _TOKEN_FILENAME).write_bytes(encrypted)

    def load(self) -> dict:
        """Decrypt and return stored token data.

        Returns a dict with keys: ``access_token``, ``refresh_token`` (may be
        ``None``), and ``metadata``.

        Raises
        ------
        FileNotFoundError
            If no token has been stored yet.
        """
        token_path = self._dir / _TOKEN_FILENAME
        if not token_path.exists():
            raise FileNotFoundError(
                f"No token found at {token_path}. "
                "Store a token first via store_token() or acquire_token_device_flow()."
            )
        decrypted = self._fernet.decrypt(token_path.read_bytes())
        return json.loads(decrypted)

    def clear(self) -> None:
        """Delete all stored token data (but keep the encryption key)."""
        token_path = self._dir / _TOKEN_FILENAME
        if token_path.exists():
            token_path.unlink()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_or_create_key(self) -> bytes:
        key_path = self._dir / _KEY_FILENAME
        if key_path.exists():
            return key_path.read_bytes()
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        _restrict_to_owner(key_path)
        return key
