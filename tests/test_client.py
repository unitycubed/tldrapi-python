"""Unit tests for the sync + async TLDRapi client.

All tests use `respx` to mock the HTTP layer — no real API calls.
Real-network verification happens in a separate integration script
run by hand before publishing to PyPI.
"""

import pytest
import httpx
import respx

from tldrapi import (
    TLDRapi,
    AsyncTLDRapi,
    AuthenticationError,
    InsufficientCreditsError,
    RateLimitError,
    LanguageNotSupportedError,
    QualitySelectionRequiresPaidPlanError,
    ServerError,
    TLDRapiError,
    InvalidRequestError,
)
from tldrapi.client import DEFAULT_BASE_URL


API_KEY = "tldr_testfaketestfaketestfaketestfaketestfake"
SUM_URL = f"{DEFAULT_BASE_URL}/summarize"


# ---- happy path ----

@respx.mock
def test_summarize_returns_typed_result():
    respx.post(SUM_URL).mock(return_value=httpx.Response(
        200,
        json={
            "summary": "Short summary.",
            "session_id": "sess_abc",
            "usage": {
                "input_tokens": 42, "output_tokens": 7,
                "total_cost": 0.00005, "model_used": "home:llama-3.1-8b",
            },
        },
        headers={"X-Request-ID": "rid-123", "X-Credits-Charged": "1", "X-Credits-Remaining": "99"},
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c:
        r = c.summarize("Some input text longer than 20 chars.")
    assert r.summary == "Short summary."
    assert r.session_id == "sess_abc"
    assert r.usage.input_tokens == 42
    assert r.usage.model_used == "home:llama-3.1-8b"
    assert r.request_id == "rid-123"
    assert r.credits.get("charged") == "1"
    assert r.credits.get("remaining") == "99"


@respx.mock
def test_summarize_sends_x_quality_and_auth_headers():
    route = respx.post(SUM_URL).mock(return_value=httpx.Response(
        200,
        json={"summary": "ok", "usage": {"model_used": "x"}}
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c:
        c.summarize("input", tier="deep")
    req = route.calls[0].request
    assert req.headers["X-RapidAPI-Key"] == API_KEY
    assert req.headers["X-RapidAPI-Host"] == "tldrapi.p.rapidapi.com"
    assert req.headers["X-Quality"] == "deep"


@respx.mock
def test_summarize_omits_x_quality_when_no_tier():
    route = respx.post(SUM_URL).mock(return_value=httpx.Response(
        200, json={"summary": "ok", "usage": {"model_used": "x"}}
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c:
        c.summarize("input")
    assert "X-Quality" not in route.calls[0].request.headers


@respx.mock
def test_summarize_forwards_session_id_and_model_alias_in_body():
    route = respx.post(SUM_URL).mock(return_value=httpx.Response(
        200, json={"summary": "ok", "session_id": "s2", "usage": {"model_used": "x"}}
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c:
        c.summarize("input", session_id="s1", model_alias="premium-claude-4-5")
    req = route.calls[0].request
    import json
    body = json.loads(req.content)
    assert body["session_id"] == "s1"
    assert body["model_alias"] == "premium-claude-4-5"


# ---- validation ----

def test_summarize_rejects_empty_input():
    c = TLDRapi(rapidapi_key=API_KEY, retries=0)
    with pytest.raises(ValueError, match="non-empty string"):
        c.summarize("")


def test_summarize_rejects_invalid_tier():
    c = TLDRapi(rapidapi_key=API_KEY, retries=0)
    with pytest.raises(ValueError, match="invalid tier"):
        c.summarize("hello world", tier="bogus")


def test_constructor_rejects_empty_key():
    with pytest.raises(ValueError, match="rapidapi_key is required"):
        TLDRapi(rapidapi_key="")


# ---- error mapping ----

@respx.mock
def test_401_raises_authentication_error():
    respx.post(SUM_URL).mock(return_value=httpx.Response(
        401, json={"error": "API key missing"}
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c, \
         pytest.raises(AuthenticationError) as excinfo:
        c.summarize("some input")
    assert excinfo.value.status_code == 401


@respx.mock
def test_402_raises_insufficient_credits_with_body():
    respx.post(SUM_URL).mock(return_value=httpx.Response(
        402,
        json={
            "error": "insufficient_credits",
            "credits_available": 3,
            "credits_required": 5,
            "options": {"top_up": {"url": "https://x/topup"}},
        },
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c, \
         pytest.raises(InsufficientCreditsError) as excinfo:
        c.summarize("some input", tier="standard")
    assert excinfo.value.response_body["credits_available"] == 3
    assert "top_up" in excinfo.value.response_body["options"]


@respx.mock
def test_429_carries_retry_after_seconds():
    respx.post(SUM_URL).mock(return_value=httpx.Response(
        429, json={"error": "rate_limit_exceeded"}, headers={"Retry-After": "60"}
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c, \
         pytest.raises(RateLimitError) as excinfo:
        c.summarize("some input")
    assert excinfo.value.retry_after_seconds == 60


@respx.mock
def test_400_language_not_supported():
    respx.post(SUM_URL).mock(return_value=httpx.Response(
        400,
        json={
            "error": "Language not yet supported",
            "error_code": "LANGUAGE_NOT_SUPPORTED",
            "detected_language": "spa",
            "detected_language_name": "Spanish",
        },
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c, \
         pytest.raises(LanguageNotSupportedError) as excinfo:
        c.summarize("El perro corre.")
    assert excinfo.value.response_body["detected_language"] == "spa"


@respx.mock
def test_400_quality_selection_requires_paid():
    respx.post(SUM_URL).mock(return_value=httpx.Response(
        400, json={"error": "quality_selection_requires_paid_plan"}
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c, \
         pytest.raises(QualitySelectionRequiresPaidPlanError):
        c.summarize("input", tier="deep")


@respx.mock
def test_500_raises_server_error_after_retries_exhausted():
    respx.post(SUM_URL).mock(return_value=httpx.Response(500, text="oops"))
    with TLDRapi(rapidapi_key=API_KEY, retries=1) as c, \
         pytest.raises(ServerError):
        c.summarize("some input")


@respx.mock
def test_500_then_200_retries_and_succeeds():
    """Verifies exponential-backoff retry actually retries on 5xx."""
    respx.post(SUM_URL).mock(side_effect=[
        httpx.Response(500, text="transient"),
        httpx.Response(200, json={"summary": "recovered", "usage": {"model_used": "x"}}),
    ])
    with TLDRapi(rapidapi_key=API_KEY, retries=2) as c:
        r = c.summarize("some input")
    assert r.summary == "recovered"


@respx.mock
def test_4xx_not_retried():
    """4xx must not be retried — retrying an insufficient_credits would
    burn additional guaranteed-fail calls."""
    route = respx.post(SUM_URL).mock(return_value=httpx.Response(
        402, json={"error": "insufficient_credits"}
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=3) as c, \
         pytest.raises(InsufficientCreditsError):
        c.summarize("some input")
    assert route.call_count == 1


# ---- misc / secondary endpoints ----

@respx.mock
def test_rates_returns_typed_result():
    respx.get(f"{DEFAULT_BASE_URL}/rates").mock(return_value=httpx.Response(
        200,
        json={"quick": 1, "standard": 5, "deep": 30, "premium": 110, "ultra": 400,
              "updated_at": "2026-08-30T00:00:00Z"},
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c:
        r = c.rates()
    assert r.quick == 1 and r.ultra == 400
    assert r.updated_at == "2026-08-30T00:00:00Z"


@respx.mock
def test_usage_returns_typed_result():
    respx.get(f"{DEFAULT_BASE_URL}/usage").mock(return_value=httpx.Response(
        200,
        json={"period": "2026-08", "calls": 42, "credits_charged": 210, "credits_remaining": 790},
    ))
    with TLDRapi(rapidapi_key=API_KEY, retries=0) as c:
        u = c.usage()
    assert u.calls == 42 and u.credits_remaining == 790


# ---- async ----

@pytest.mark.asyncio
@respx.mock
async def test_async_summarize_happy_path():
    respx.post(SUM_URL).mock(return_value=httpx.Response(
        200, json={"summary": "async ok", "usage": {"model_used": "x"}}
    ))
    async with AsyncTLDRapi(rapidapi_key=API_KEY, retries=0) as c:
        r = await c.summarize("some input")
    assert r.summary == "async ok"


@pytest.mark.asyncio
@respx.mock
async def test_async_402_raises_insufficient_credits():
    respx.post(SUM_URL).mock(return_value=httpx.Response(
        402, json={"error": "insufficient_credits", "credits_available": 0}
    ))
    async with AsyncTLDRapi(rapidapi_key=API_KEY, retries=0) as c:
        with pytest.raises(InsufficientCreditsError):
            await c.summarize("some input")


@pytest.mark.asyncio
@respx.mock
async def test_async_500_then_200_retries():
    respx.post(SUM_URL).mock(side_effect=[
        httpx.Response(500),
        httpx.Response(200, json={"summary": "recovered", "usage": {"model_used": "x"}}),
    ])
    async with AsyncTLDRapi(rapidapi_key=API_KEY, retries=2) as c:
        r = await c.summarize("some input")
    assert r.summary == "recovered"
