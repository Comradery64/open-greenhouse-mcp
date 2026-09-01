"""Shared async HTTP client for all Greenhouse API tool modules.

Never raises exceptions to the LLM — all errors are returned as structured dicts.
"""

from __future__ import annotations

import asyncio
import base64
import random
import re
import time
from typing import Any
from urllib.parse import unquote

import httpx

from greenhouse_mcp.errors import build_error
from greenhouse_mcp.logging import log_api_call

# Harvest v3. v1 and v2 are unavailable after 2026-08-31; see
# docs/harvest-v3-migration.md. Job Board and Ingestion are separate products that
# were not part of the Harvest sunset and stay on their own v1 paths.
HARVEST_BASE = "https://harvest.greenhouse.io/v3"
HARVEST_TOKEN_URL = "https://auth.greenhouse.io/token"
BOARD_BASE = "https://boards-api.greenhouse.io/v1/boards"
INGESTION_BASE = "https://api.greenhouse.io/v1/partner"

_CACHE_TTL = 300  # 5 minutes
_MAX_RETRIES = 3
_INTER_PAGE_DELAY = 0.2  # seconds

# Refresh this far before the token actually expires, so a request never leaves
# with a credential that dies in flight.
_TOKEN_SAFETY_MARGIN = 60  # seconds

_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
_CURSOR_RE = re.compile(r'[?&]cursor=([^&]+)')


def _cursor_from_url(url: str | None) -> str | None:
    """Pull the opaque `cursor` value out of a server-issued next-page URL.

    The cursor is handed back to the model rather than the whole URL, so a
    resumed call goes through the same endpoint and auth as the first one.
    """
    if not url:
        return None
    m = _CURSOR_RE.search(url)
    return unquote(m.group(1)) if m else None


class _TokenError(Exception):
    """Internal: token acquisition failed, carrying the dict to return instead.

    Never escapes the client — every Harvest entry point converts it back into
    the structured error dict the LLM expects.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("error", "token error"))
        self.payload = payload


class GreenhouseClient:
    """Shared async HTTP client for Harvest, Job Board, and Ingestion APIs."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        board_token: str | None = None,
        on_behalf_of: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_id: str | None = None,
    ) -> None:
        has_harvest = client_id is not None and client_secret is not None
        if not has_harvest and board_token is None and api_key is None:
            raise ValueError(
                "Harvest v3 needs client_id and client_secret; "
                "otherwise provide board_token for the public Job Board."
            )
        # v1-only. Harvest no longer accepts it — retained for the Job Board and
        # Ingestion APIs, which were not part of the Harvest sunset.
        self.api_key = api_key
        self.board_token = board_token
        self.on_behalf_of = on_behalf_of
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_id = user_id
        self._http_client: httpx.AsyncClient | None = None
        # in-memory TTL cache: cache_key -> (data, expires_at)
        self._cache: dict[str, tuple[Any, float]] = {}
        # OAuth access token for Harvest v3, with its monotonic expiry.
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    def set_on_behalf_of(self, user_id: str) -> None:
        """Set the On-Behalf-Of user ID for write operation audit trail."""
        self.on_behalf_of = user_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_http_client(self) -> httpx.AsyncClient:
        """Lazy-initialise and return the shared httpx.AsyncClient."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                timeout=httpx.Timeout(30.0),
            )
        return self._http_client

    def _basic_auth_header(self) -> dict[str, str]:
        """Basic auth for the v1 Job Board and Ingestion APIs.

        Harvest v3 does not accept this — see `_bearer_header`.
        """
        if self.api_key is None:
            return {}
        token = base64.b64encode(f"{self.api_key}:".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _ingestion_headers(self) -> dict[str, str]:
        headers = self._basic_auth_header()
        if self.on_behalf_of:
            headers["On-Behalf-Of"] = self.on_behalf_of
        return headers

    # ------------------------------------------------------------------
    # Harvest v3 OAuth
    # ------------------------------------------------------------------

    def invalidate_token(self) -> None:
        """Drop the cached access token so the next call fetches a fresh one."""
        self._token = None
        self._token_expires_at = 0.0

    async def _bearer_header(self) -> dict[str, str]:
        """Return the Harvest v3 Authorization header, fetching a token if needed.

        Raises `_TokenError` carrying a relayable error dict; callers convert it
        rather than letting an exception reach the LLM.
        """
        token = await self._access_token()
        return {"Authorization": f"Bearer {token}"}

    async def _access_token(self) -> str:
        if self.client_id is None or self.client_secret is None:
            raise _TokenError(
                self._error_dict(
                    401,
                    "Harvest v3 requires a client ID and secret. Ask your Greenhouse "
                    "admin for API credentials and set greenhouse_client_id and "
                    "greenhouse_client_secret.",
                    "/token",
                )
            )
        # Serialised so a burst of concurrent calls fetches one token, not N.
        async with self._token_lock:
            if self._token is not None and time.monotonic() < self._token_expires_at:
                return self._token
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        """POST the client-credentials grant. Caller must hold `_token_lock`."""
        http = self._get_http_client()
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        data = {"grant_type": "client_credentials"}
        if self.user_id:
            data["sub"] = self.user_id
        start = time.monotonic()
        try:
            resp = await http.post(
                HARVEST_TOKEN_URL,
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=data,
            )
        except Exception as e:
            raise _TokenError(self._error_dict(0, f"Token request failed: {e}", "/token")) from e

        # Logged by fixed label, never by URL or body — the request carries the
        # client secret and the response carries the access token.
        log_api_call(method="POST", url="/token", status=resp.status_code, start_time=start)
        if resp.status_code >= 400:
            raise _TokenError(self._error_dict(resp.status_code, None, "/token"))

        body = self._parse_body(resp)
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise _TokenError(
                self._error_dict(502, "Token response contained no access_token.", "/token")
            )
        expires_in: Any = body.get("expires_in") if isinstance(body, dict) else None
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError):
            # A token with no usable lifetime still works; assume an hour and let
            # the 401-refresh path catch it if that guess is wrong.
            lifetime = 3600.0
        self._token = str(token)
        self._token_expires_at = time.monotonic() + max(lifetime - _TOKEN_SAFETY_MARGIN, 0.0)
        return self._token

    async def _harvest_headers(self, *, write: bool = False) -> dict[str, str]:
        headers = await self._bearer_header()
        if write and self.on_behalf_of:
            headers["On-Behalf-Of"] = self.on_behalf_of
        return headers

    @staticmethod
    def _parse_next_link(link_header: str | None) -> str | None:
        if not link_header:
            return None
        m = _LINK_RE.search(link_header)
        return m.group(1) if m else None

    @staticmethod
    def _error_dict(
        status_code: int, detail: Any = None, url: str | None = None
    ) -> dict[str, Any]:
        """Build a relayable error payload (plain-English message + support code)."""
        return build_error(status_code, detail, url)

    @staticmethod
    def _is_error(result: dict[str, Any]) -> bool:
        return "error" in result and "status_code" in result

    # ------------------------------------------------------------------
    # Low-level request with rate-limit retry
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        """Execute an HTTP request with up to _MAX_RETRIES on 429."""
        http = self._get_http_client()
        start = time.monotonic()
        for attempt in range(_MAX_RETRIES + 1):
            resp = await http.request(
                method,
                url,
                headers=headers or {},
                params=params,
                json=json,
            )
            if resp.status_code == 429:
                if attempt < _MAX_RETRIES:
                    retry_after = float(resp.headers.get("Retry-After", "1"))
                    jitter = random.uniform(0, min(retry_after * 0.5, 2.0))
                    await asyncio.sleep(retry_after + jitter)
                    continue
            log_api_call(method=method, url=url, status=resp.status_code, start_time=start)
            return resp
        # Exhausted retries — return the last 429 response
        log_api_call(method=method, url=url, status=resp.status_code, start_time=start)
        return resp

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_body(resp: httpx.Response) -> Any:
        """Return parsed JSON or empty dict on decode failure."""
        try:
            return resp.json()
        except Exception:
            return {}

    def _handle_response(self, resp: httpx.Response) -> dict[str, Any]:
        """Convert an httpx.Response to either the parsed body or an error dict."""
        # Every non-success status becomes a structured error. Enumerating
        # individual codes let 400 and 409 fall through as if they were success
        # bodies, which `_paginated_get` then wrapped as a bogus one-item result —
        # so a rejected filter looked like a real record.
        if resp.status_code >= 400:
            url = str(resp.request.url) if resp.request is not None else None
            return self._error_dict(resp.status_code, self._parse_body(resp), url)
        return self._parse_body(resp)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Harvest v3 request wrapper (token refresh on 401)
    # ------------------------------------------------------------------

    async def _harvest_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        write: bool = False,
    ) -> httpx.Response:
        """Harvest request that refreshes an expired token once and retries.

        Extends the existing retry path rather than adding a second mechanism:
        `_request` still owns 429/Retry-After, this owns 401/token.
        """
        headers = await self._harvest_headers(write=write)
        resp = await self._request(method, url, headers=headers, params=params, json=json)
        if resp.status_code == 401:
            # The token was accepted when issued, so a 401 means it expired early
            # or was revoked. One clean retry; a second 401 is a real auth failure.
            self.invalidate_token()
            headers = await self._harvest_headers(write=write)
            resp = await self._request(method, url, headers=headers, params=params, json=json)
        return resp

    # ------------------------------------------------------------------
    # Paginated GET helper (v3 cursor paging)
    # ------------------------------------------------------------------

    async def _paginated_get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        paginate: str = "single",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """GET one page (or all pages) of a Harvest v3 collection.

        v3 rejects a `cursor` combined with any other query parameter (422), so a
        cursor request sends the cursor alone and drops the caller's filters —
        they are already baked into the cursor the server issued.
        """
        if cursor:
            resp = await self._harvest_request("GET", url, params={"cursor": cursor})
        else:
            resp = await self._harvest_request("GET", url, params=params)
        parsed = self._handle_response(resp)

        if self._is_error(parsed):
            return parsed

        if paginate == "single":
            next_url = self._parse_next_link(resp.headers.get("link"))
            items = parsed if isinstance(parsed, list) else [parsed]
            return {
                "items": items,
                "has_next": next_url is not None,
                "next_cursor": _cursor_from_url(next_url),
            }

        # paginate="all" — follow the server's next links. The next URL already
        # carries the cursor and must be re-requested with no extra params.
        all_items: list[Any] = parsed if isinstance(parsed, list) else [parsed]
        next_url = self._parse_next_link(resp.headers.get("link"))
        while next_url:
            await asyncio.sleep(_INTER_PAGE_DELAY)
            resp = await self._harvest_request("GET", next_url)
            parsed = self._handle_response(resp)
            if self._is_error(parsed):
                break
            page_items = parsed if isinstance(parsed, list) else [parsed]
            all_items.extend(page_items)
            next_url = self._parse_next_link(resp.headers.get("link"))

        return {"items": all_items, "total": len(all_items)}

    # ------------------------------------------------------------------
    # Harvest API methods
    # ------------------------------------------------------------------

    async def harvest_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        paginate: str = "single",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        url = f"{HARVEST_BASE}{endpoint}"
        try:
            return await self._paginated_get(
                url, params=params, paginate=paginate, cursor=cursor
            )
        except _TokenError as e:
            return e.payload

    async def harvest_get_one(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Harvest GET for a single resource — returns the object directly."""
        return await self._harvest_simple("GET", endpoint, params=params)

    async def harvest_get_by_id(
        self,
        collection: str,
        resource_id: Any,
    ) -> dict[str, Any]:
        """Fetch one record from a collection by id.

        v3 has no `/{collection}/{id}` show endpoints — they return 404 even for
        an id the list endpoint just handed out. A single record is retrieved by
        filtering the collection on `ids`. Verified against a live instance for
        /jobs, /candidates and /applications.

        Returns the record, or a 404-shaped error when the id matches nothing,
        so callers keep the same success/error contract they had under v1.
        """
        result = await self.harvest_get(collection, params={"ids": resource_id})
        if self._is_error(result):
            return result
        items = result.get("items", [])
        if not items:
            return self._error_dict(404, {"message": "Resource not found"}, collection)
        first = items[0]
        if not isinstance(first, dict):
            return self._error_dict(502, {"message": "Unexpected record shape"}, collection)
        return first

    async def harvest_post(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._harvest_simple("POST", endpoint, json=json_data, write=True)

    async def harvest_patch(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._harvest_simple("PATCH", endpoint, json=json_data, write=True)

    async def harvest_delete(
        self,
        endpoint: str,
    ) -> dict[str, Any]:
        return await self._harvest_simple("DELETE", endpoint, write=True)

    async def harvest_put(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._harvest_simple("PUT", endpoint, json=json_data, write=True)

    async def _harvest_simple(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        write: bool = False,
    ) -> dict[str, Any]:
        """Single non-paginated Harvest call, with token errors converted."""
        url = f"{HARVEST_BASE}{endpoint}"
        try:
            resp = await self._harvest_request(
                method, url, params=params, json=json, write=write
            )
        except _TokenError as e:
            return e.payload
        return self._handle_response(resp)

    async def harvest_get_cached(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        paginate: str = "single",
        force_refresh: bool = False,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        url = f"{HARVEST_BASE}{endpoint}"
        sorted_params = sorted((params or {}).items())
        cache_key = f"GET:{url}:{sorted_params}:{paginate}:{cursor or ''}"
        now = time.monotonic()

        if not force_refresh and cache_key in self._cache:
            data, expires_at = self._cache[cache_key]
            if now < expires_at:
                return data  # type: ignore[no-any-return]

        result = await self.harvest_get(
            endpoint, params=params, paginate=paginate, cursor=cursor
        )

        # Only cache successful responses
        if not self._is_error(result):
            self._cache[cache_key] = (result, now + _CACHE_TTL)

        return result

    # ------------------------------------------------------------------
    # Job Board API methods
    # ------------------------------------------------------------------

    async def board_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Job Board GET — no auth header, board token is in the URL path."""
        url = f"{BOARD_BASE}/{self.board_token}{endpoint}"
        resp = await self._request("GET", url, params=params)
        return self._handle_response(resp)

    async def board_post(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Job Board POST — uses Harvest auth if api_key is set."""
        url = f"{BOARD_BASE}/{self.board_token}{endpoint}"
        resp = await self._request("POST", url, headers=self._basic_auth_header(), json=json_data)
        return self._handle_response(resp)

    # ------------------------------------------------------------------
    # Ingestion API methods
    # ------------------------------------------------------------------

    async def ingestion_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{INGESTION_BASE}{endpoint}"
        resp = await self._request("GET", url, headers=self._ingestion_headers(), params=params)
        return self._handle_response(resp)

    async def ingestion_post(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{INGESTION_BASE}{endpoint}"
        resp = await self._request("POST", url, headers=self._ingestion_headers(), json=json_data)
        return self._handle_response(resp)

    # ------------------------------------------------------------------
    # Attachment download
    # ------------------------------------------------------------------

    async def download_url(self, url: str) -> dict[str, Any]:
        """Download content from a URL (e.g. signed S3 attachment URL)."""
        http = self._get_http_client()
        try:
            resp = await http.get(url, follow_redirects=True)
            if resp.status_code >= 400:
                # Fixed label rather than the URL: attachment URLs are signed and
                # single-use, so their path adds nothing an engineer can act on.
                return self._error_dict(resp.status_code, url="/attachment-download")
            content_type = resp.headers.get("content-type", "")
            if "text" in content_type or "json" in content_type:
                return {"content": resp.text, "content_type": content_type}
            return {
                "content_base64": base64.b64encode(resp.content).decode(),
                "content_type": content_type,
                "size_bytes": len(resp.content),
            }
        except Exception as e:
            return self._error_dict(0, f"Download failed: {e}", "/attachment-download")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying httpx.AsyncClient and release connections."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None
