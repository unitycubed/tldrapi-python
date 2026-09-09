"""TLDRapi HTTP client (sync + async).

Design choices:
 - httpx is the ONE dependency. Ships sync + async out of one API,
   handles connection pooling + HTTP/2, well-known + well-supported.
 - Retries on 5xx and network blips with exponential backoff + jitter
   (default 3 attempts). NOT retried: 4xx (permanent) and 429 (caller
   should respect Retry-After — auto-retry would burn user credits +
   worsen the throttle).
 - Timeout defaults to 60s — /summarize can legitimately take 30s
   on the deep tier. Convert-async status calls default to 10s.
 - X-Request-ID is threaded onto every SummarizeResult so users
   can quote it when reporting issues.
 - The sync + async classes share the request-building logic below;
   they differ only in the transport call.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Optional

try:
    import httpx
except ImportError as e:  # pragma: no cover — install-time surface
    raise ImportError(
        "tldrapi requires the `httpx` package. Install with: pip install tldrapi[http] "
        "(or just `pip install httpx`)."
    ) from e

from .errors import (
    NetworkError,
    RateLimitError,
    ServerError,
    TimeoutError,
    _from_response,
)
from .models import Rates, SummarizeResult, Usage, UsageStats


# RapidAPI is the only auth path at launch. Customers subscribe to the
# TLDRapi listing on RapidAPI, get an X-RapidAPI-Key from their RapidAPI
# dashboard, and this SDK sends every request through the RapidAPI proxy.
# Direct-signup (bypass RapidAPI) is post-launch — when it ships we'll
# add a second constructor path.
#
# The RapidAPI host below is the marketplace-facing hostname; substitute
# the real one once the listing is published. It is intentionally NOT
# `tldrapi-summarization.p.rapidapi.com` (that hostname is behind the proxy).
DEFAULT_RAPIDAPI_HOST = "tldrapi-summarization.p.rapidapi.com"
DEFAULT_BASE_URL = f"https://{DEFAULT_RAPIDAPI_HOST}"
DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3
_RETRY_BASE_SEC = 0.5

VALID_TIERS = ("quick", "standard", "deep", "premium", "ultra")


def _headers(rapidapi_key: str, rapidapi_host: str, tier: Optional[str],
             extra: dict | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "User-Agent": "tldrapi-python/0.1.0",
        # RapidAPI expects both headers together — Key authenticates the
        # customer, Host disambiguates which listing they're calling
        # (RapidAPI proxies many APIs from the same p.rapidapi.com prefix).
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": rapidapi_host,
    }
    if tier:
        h["X-Quality"] = tier
    if extra:
        h.update(extra)
    return h


def _parse_body(resp) -> dict | str:
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            return resp.text
    return resp.text


def _build_summarize_result(body: dict, headers) -> SummarizeResult:
    usage_body = body.get("usage") or {}
    usage = Usage(
        input_tokens=int(usage_body.get("input_tokens") or 0),
        output_tokens=int(usage_body.get("output_tokens") or 0),
        total_cost=float(usage_body.get("total_cost") or 0),
        model_used=str(usage_body.get("model_used") or ""),
    )
    credits = {}
    for k in ("X-Credits-Charged", "X-Credits-Remaining", "X-Credits-Tier"):
        if k in headers:
            # normalize to lowercase-hyphens-to-underscores for python-idiomatic keys
            credits[k.lower().replace("x-credits-", "").replace("-", "_")] = headers[k]
    return SummarizeResult(
        summary=str(body.get("summary") or ""),
        session_id=str(body.get("session_id") or ""),
        usage=usage,
        request_id=headers.get("X-Request-ID", ""),
        credits=credits,
        raw=body if isinstance(body, dict) else {},
    )


def _validate_tier(tier: Optional[str]) -> Optional[str]:
    if tier is None:
        return None
    tier = str(tier).lower().strip()
    if tier not in VALID_TIERS:
        raise ValueError(
            f"invalid tier {tier!r}; must be one of {VALID_TIERS}"
        )
    return tier


def _classify_status(status: int) -> str:
    """'retry' | 'raise' | 'ok'"""
    if 200 <= status < 300:
        return "ok"
    if status >= 500:
        return "retry"
    # 4xx incl. 429 — not retried by the client. Caller decides for 429.
    return "raise"


def _backoff(attempt: int) -> float:
    return _RETRY_BASE_SEC * (2 ** attempt) + random.random() * 0.2  # noqa: S311


# ---------------- Sync client ----------------

class TLDRapi:
    """Synchronous TLDRapi API client.

    Simple use:
        client = TLDRapi(api_key="tldr_...")
        r = client.summarize("Some long text here")
        print(r.summary)

    Optional session pinning:
        r1 = client.summarize("First doc")
        r2 = client.summarize("Second doc", session_id=r1.session_id)
    """

    def __init__(
        self,
        rapidapi_key: str,
        *,
        rapidapi_host: str = DEFAULT_RAPIDAPI_HOST,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        http_client: Optional[httpx.Client] = None,
    ):
        if not rapidapi_key:
            raise ValueError("rapidapi_key is required (subscribe on RapidAPI to obtain one)")
        self.rapidapi_key = rapidapi_key
        self.rapidapi_host = rapidapi_host
        # base_url defaults to https://<rapidapi_host> so overriding the
        # host also moves the URL. Pass base_url explicitly for staging /
        # mock-server testing.
        self.base_url = (base_url or f"https://{rapidapi_host}").rstrip("/")
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self._own_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._own_client:
            self._http.close()

    def summarize(
        self,
        input_text: str,
        *,
        tier: Optional[str] = None,
        session_id: Optional[str] = None,
        model_alias: Optional[str] = None,
        allow_overage: bool = False,
        extra_headers: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> SummarizeResult:
        """Summarize `input_text`. See README for tier costs + caps."""
        if not input_text or not isinstance(input_text, str):
            raise ValueError("input_text must be a non-empty string")
        tier = _validate_tier(tier)
        payload: dict[str, Any] = {"input_text": input_text}
        if session_id:
            payload["session_id"] = session_id
        if model_alias:
            payload["model_alias"] = model_alias
        headers = _headers(self.rapidapi_key, self.rapidapi_host, tier, extra_headers)
        if allow_overage:
            headers["X-Allow-Overage"] = "true"

        resp = self._request(
            "POST", "/summarize", json=payload, headers=headers,
            timeout=timeout,
        )
        body = _parse_body(resp)
        if resp.status_code >= 400:
            self._raise_for_response(resp, body)
        if not isinstance(body, dict):
            raise ServerError(
                f"unexpected non-JSON summarize response: {str(body)[:200]}",
                status_code=resp.status_code,
                request_id=resp.headers.get("X-Request-ID", ""),
            )
        return _build_summarize_result(body, resp.headers)

    def rates(self) -> Rates:
        resp = self._request("GET", "/rates", headers=_headers(self.rapidapi_key, self.rapidapi_host, None))
        body = _parse_body(resp)
        if resp.status_code >= 400:
            self._raise_for_response(resp, body)
        body = body if isinstance(body, dict) else {}
        return Rates(
            quick=int(body.get("quick") or 1),
            standard=int(body.get("standard") or 5),
            deep=int(body.get("deep") or 30),
            premium=int(body.get("premium") or 110),
            ultra=int(body.get("ultra") or 400),
            updated_at=body.get("updated_at"),
            raw=body,
        )

    def usage(self) -> UsageStats:
        resp = self._request("GET", "/usage", headers=_headers(self.rapidapi_key, self.rapidapi_host, None))
        body = _parse_body(resp)
        if resp.status_code >= 400:
            self._raise_for_response(resp, body)
        body = body if isinstance(body, dict) else {}
        return UsageStats(
            period=str(body.get("period") or ""),
            calls=int(body.get("calls") or 0),
            credits_charged=int(body.get("credits_charged") or 0),
            credits_remaining=int(body.get("credits_remaining") or 0),
            raw=body,
        )

    # --- internal ---

    def _request(self, method: str, path: str, *, json=None, headers=None,
                 params=None, timeout=None):
        url = f"{self.base_url}{path}"
        eff_timeout = timeout if timeout is not None else self.timeout
        last_exc = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._http.request(
                    method, url, json=json, headers=headers,
                    params=params, timeout=eff_timeout,
                )
            except httpx.TimeoutException as e:
                last_exc = TimeoutError(str(e))
                if attempt < self.retries:
                    time.sleep(_backoff(attempt))
                    continue
                raise last_exc from e
            except httpx.HTTPError as e:
                last_exc = NetworkError(str(e))
                if attempt < self.retries:
                    time.sleep(_backoff(attempt))
                    continue
                raise last_exc from e

            action = _classify_status(resp.status_code)
            if action == "retry" and attempt < self.retries:
                time.sleep(_backoff(attempt))
                continue
            return resp
        raise last_exc  # unreachable

    def _raise_for_response(self, resp, body):
        exc = _from_response(
            resp.status_code,
            body if isinstance(body, dict) else {},
            request_id=resp.headers.get("X-Request-ID", ""),
        )
        if isinstance(exc, RateLimitError):
            ra = resp.headers.get("Retry-After")
            if ra:
                try:
                    exc.retry_after_seconds = int(ra)
                except (TypeError, ValueError):
                    pass
        raise exc


# ---------------- Async client ----------------

class AsyncTLDRapi:
    """Async TLDRapi client. Same API as `TLDRapi` with `await`."""

    def __init__(
        self,
        rapidapi_key: str,
        *,
        rapidapi_host: str = DEFAULT_RAPIDAPI_HOST,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        if not rapidapi_key:
            raise ValueError("rapidapi_key is required (subscribe on RapidAPI to obtain one)")
        self.rapidapi_key = rapidapi_key
        self.rapidapi_host = rapidapi_host
        self.base_url = (base_url or f"https://{rapidapi_host}").rstrip("/")
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self._own_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        if self._own_client:
            await self._http.aclose()

    async def summarize(
        self,
        input_text: str,
        *,
        tier: Optional[str] = None,
        session_id: Optional[str] = None,
        model_alias: Optional[str] = None,
        allow_overage: bool = False,
        extra_headers: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> SummarizeResult:
        if not input_text or not isinstance(input_text, str):
            raise ValueError("input_text must be a non-empty string")
        tier = _validate_tier(tier)
        payload: dict[str, Any] = {"input_text": input_text}
        if session_id:
            payload["session_id"] = session_id
        if model_alias:
            payload["model_alias"] = model_alias
        headers = _headers(self.rapidapi_key, self.rapidapi_host, tier, extra_headers)
        if allow_overage:
            headers["X-Allow-Overage"] = "true"

        resp = await self._request(
            "POST", "/summarize", json=payload, headers=headers, timeout=timeout
        )
        body = _parse_body(resp)
        if resp.status_code >= 400:
            self._raise_for_response(resp, body)
        if not isinstance(body, dict):
            raise ServerError(
                f"unexpected non-JSON summarize response: {str(body)[:200]}",
                status_code=resp.status_code,
                request_id=resp.headers.get("X-Request-ID", ""),
            )
        return _build_summarize_result(body, resp.headers)

    async def rates(self) -> Rates:
        resp = await self._request("GET", "/rates", headers=_headers(self.rapidapi_key, self.rapidapi_host, None))
        body = _parse_body(resp)
        if resp.status_code >= 400:
            self._raise_for_response(resp, body)
        body = body if isinstance(body, dict) else {}
        return Rates(
            quick=int(body.get("quick") or 1),
            standard=int(body.get("standard") or 5),
            deep=int(body.get("deep") or 30),
            premium=int(body.get("premium") or 110),
            ultra=int(body.get("ultra") or 400),
            updated_at=body.get("updated_at"),
            raw=body,
        )

    async def usage(self) -> UsageStats:
        resp = await self._request("GET", "/usage", headers=_headers(self.rapidapi_key, self.rapidapi_host, None))
        body = _parse_body(resp)
        if resp.status_code >= 400:
            self._raise_for_response(resp, body)
        body = body if isinstance(body, dict) else {}
        return UsageStats(
            period=str(body.get("period") or ""),
            calls=int(body.get("calls") or 0),
            credits_charged=int(body.get("credits_charged") or 0),
            credits_remaining=int(body.get("credits_remaining") or 0),
            raw=body,
        )

    # --- internal ---

    async def _request(self, method: str, path: str, *, json=None, headers=None,
                       params=None, timeout=None):
        url = f"{self.base_url}{path}"
        eff_timeout = timeout if timeout is not None else self.timeout
        last_exc = None
        for attempt in range(self.retries + 1):
            try:
                resp = await self._http.request(
                    method, url, json=json, headers=headers,
                    params=params, timeout=eff_timeout,
                )
            except httpx.TimeoutException as e:
                last_exc = TimeoutError(str(e))
                if attempt < self.retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise last_exc from e
            except httpx.HTTPError as e:
                last_exc = NetworkError(str(e))
                if attempt < self.retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise last_exc from e

            action = _classify_status(resp.status_code)
            if action == "retry" and attempt < self.retries:
                await asyncio.sleep(_backoff(attempt))
                continue
            return resp
        raise last_exc

    def _raise_for_response(self, resp, body):
        exc = _from_response(
            resp.status_code,
            body if isinstance(body, dict) else {},
            request_id=resp.headers.get("X-Request-ID", ""),
        )
        if isinstance(exc, RateLimitError):
            ra = resp.headers.get("Retry-After")
            if ra:
                try:
                    exc.retry_after_seconds = int(ra)
                except (TypeError, ValueError):
                    pass
        raise exc
