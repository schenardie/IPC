"""
IPCSkill – Microsoft Graph API client.

A thin wrapper around *requests* that injects a valid Bearer token on every
call and surfaces Graph API errors as Python exceptions.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Optional

import requests

from .config import IPCSkillConfig
from .token_manager import TokenManager

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = (10, 30)

# JSON batch endpoint limits and retry behaviour.
_BATCH_SIZE = 20          # Graph API maximum requests per batch call
_MAX_BATCH_RETRIES = 5    # Maximum retry attempts for throttled items
_DEFAULT_RETRY_AFTER = 30.0  # Seconds to wait when no Retry-After header is present


def _parse_retry_after(headers: dict, default: float = _DEFAULT_RETRY_AFTER) -> float:
    """Return the Retry-After delay (seconds) from response headers."""
    for key in ("Retry-After", "retry-after"):
        val = headers.get(key)
        if val is not None:
            try:
                return max(1.0, float(val))
            except (ValueError, TypeError):
                pass
    return default


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

    def batch(
        self,
        requests_list: list[dict],
        *,
        on_chunk: Optional[Callable[[int, int], None]] = None,
    ) -> dict[str, dict]:
        """Execute multiple Graph API requests using the JSON batch endpoint.

        Requests are automatically chunked into groups of up to 20 (the Graph
        API maximum). Each chunk is retried up to ``_MAX_BATCH_RETRIES`` times
        when items or the entire batch are throttled (HTTP 429), honouring the
        ``Retry-After`` delay from the response headers.

        Parameters
        ----------
        requests_list:
            Each element must have keys ``id`` (str), ``method`` (str), and
            ``url`` (str, relative to the Graph version root, e.g.
            ``/deviceManagement/managedDevices(...)``).
        on_chunk:
            Optional callback invoked after each chunk of up to 20 requests
            completes (including any throttle retries for that chunk). Called
            as ``on_chunk(completed_so_far, total)`` using request counts.

        Returns
        -------
        dict[str, dict]
            ``{id: {"status": <int>, "body": <dict>}}`` for every request in
            ``requests_list``.
        """
        if not requests_list:
            return {}

        results: dict[str, dict] = {}
        total = len(requests_list)
        completed = 0

        for chunk_start in range(0, total, _BATCH_SIZE):
            chunk = requests_list[chunk_start : chunk_start + _BATCH_SIZE]
            pending: dict[str, dict] = {req["id"]: req for req in chunk}

            for attempt in range(_MAX_BATCH_RETRIES):
                if not pending:
                    break

                batch_body = {"requests": list(pending.values())}
                try:
                    envelope = self.post("/$batch", json_body=batch_body)
                except GraphAPIError as exc:
                    if exc.status_code == 429:
                        if attempt < _MAX_BATCH_RETRIES - 1:
                            logger.warning(
                                "Batch envelope throttled (429); retrying in %.0fs (attempt %d/%d)",
                                _DEFAULT_RETRY_AFTER, attempt + 1, _MAX_BATCH_RETRIES,
                            )
                            time.sleep(_DEFAULT_RETRY_AFTER)
                            continue
                    raise

                throttled_ids: list[str] = []
                max_retry_after: float = 0.0

                for item in (envelope or {}).get("responses", []):
                    item_id = str(item.get("id", ""))
                    status = item.get("status", 200)
                    body = item.get("body") or {}
                    item_headers = item.get("headers") or {}

                    if status == 429:
                        retry_after = _parse_retry_after(item_headers)
                        max_retry_after = max(max_retry_after, retry_after)
                        throttled_ids.append(item_id)
                    else:
                        results[item_id] = {"status": status, "body": body}
                        pending.pop(item_id, None)

                if throttled_ids:
                    pending = {id_: pending[id_] for id_ in throttled_ids if id_ in pending}
                    if attempt < _MAX_BATCH_RETRIES - 1:
                        logger.warning(
                            "%d item(s) throttled; retrying in %.0fs (attempt %d/%d)",
                            len(throttled_ids), max_retry_after, attempt + 1, _MAX_BATCH_RETRIES,
                        )
                        time.sleep(max_retry_after)
                    else:
                        logger.error(
                            "%d item(s) still throttled after %d attempts",
                            len(throttled_ids), _MAX_BATCH_RETRIES,
                        )
                        for id_ in throttled_ids:
                            results[id_] = {
                                "status": 429,
                                "body": {
                                    "error": {
                                        "code": "TooManyRequests",
                                        "message": "Exceeded retry limit",
                                    }
                                },
                            }
                else:
                    break

            completed += len(chunk)
            if on_chunk:
                on_chunk(min(completed, total), total)

        return results

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
