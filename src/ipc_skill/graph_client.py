"""
IPCSkill – Microsoft Graph API client.

A thin wrapper around *requests* that injects a valid Bearer token on every
call and surfaces Graph API errors as Python exceptions.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from .config import IPCSkillConfig
from .token_manager import TokenManager

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = (10, 30)


class GraphAPIError(Exception):
    """Raised when Microsoft Graph returns a non-2xx HTTP response."""

    def __init__(self, status_code: int, message: str, code: str = "") -> None:
        self.status_code = status_code
        self.graph_code = code
        super().__init__(f"Graph API error {status_code} [{code}]: {message}")


class GraphClient:
    """Authenticated client for Microsoft Graph API."""

    def __init__(
        self,
        config: IPCSkillConfig,
        token_manager: Optional[TokenManager] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._config = config
        self._token_manager = token_manager or TokenManager(config)
        self._session = session or requests.Session()

    def get(self, path: str, **kwargs: Any) -> Any:
        """Perform an authenticated GET request."""
        return self._request("GET", path, **kwargs)

    def post(self, path: str, json_body: Optional[dict] = None, **kwargs: Any) -> Any:
        """Perform an authenticated POST request."""
        return self._request("POST", path, json=json_body, **kwargs)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        token = self._token_manager.get_valid_token()
        url = self._config.graph_base_url.rstrip("/") + "/" + path.lstrip("/")

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")

        timeout = kwargs.pop("timeout", _DEFAULT_TIMEOUT)

        logger.debug("%s %s", method, url)
        response = self._session.request(
            method, url, headers=headers, timeout=timeout, **kwargs,
        )

        self._raise_for_graph_error(response)

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @staticmethod
    def _raise_for_graph_error(response: requests.Response) -> None:
        if response.ok:
            return
        try:
            body = response.json()
            error = body.get("error", {})
            message = error.get("message", response.text)
            code = error.get("code", "")
        except Exception:
            message = response.text
            code = ""
        raise GraphAPIError(response.status_code, message, code)
