"""Typed exceptions for the TLDRapi SDK.

The base class is TLDRapiError. Every failure produced by the client
subclasses it, so a caller who wants a blanket safety net can do:

    try:
        client.summarize(text)
    except TLDRapiError as e:
        # handle any SDK-originated failure
        ...

Specific subclasses map to specific server response shapes so that
callers can branch on failure mode without inspecting error strings.
The `.response_body` attribute holds the parsed JSON error body
whenever the server sent one — useful for surfacing the upgrade URL
on RateLimit / InsufficientCredits, etc.
"""


class TLDRapiError(Exception):
    """Base class for all TLDRapi SDK errors."""

    def __init__(self, message: str, *, status_code: int = 0,
                 request_id: str = "", response_body: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body or {}


class AuthenticationError(TLDRapiError):
    """API key missing, invalid, or lacks permission (401 / 403)."""


class InsufficientCreditsError(TLDRapiError):
    """402 — not enough credits. `response_body` includes `downgrade` +
    `top_up` options the caller can present to the user."""


class RateLimitError(TLDRapiError):
    """429 — plan rate-limit exceeded. `retry_after_seconds` is set from
    the Retry-After header when the server provides it."""

    def __init__(self, *args, retry_after_seconds: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class LanguageNotSupportedError(TLDRapiError):
    """400 with error_code=LANGUAGE_NOT_SUPPORTED — input wasn't English
    (only supported language at launch). `detected_language` /
    `detected_language_name` are on `response_body`."""


class QualitySelectionRequiresPaidPlanError(TLDRapiError):
    """400 — free-tier caller passed X-Quality other than 'quick'."""


class InvalidRequestError(TLDRapiError):
    """400 for other reasons — malformed body, missing input_text, etc."""


class ServerError(TLDRapiError):
    """5xx — provider outage, our bug, or a downstream failure the
    router couldn't work around."""


class NetworkError(TLDRapiError):
    """Transport-layer failure — DNS, connection reset, TLS handshake."""


class TimeoutError(TLDRapiError):  # noqa: A001 — deliberately shadows builtin
    """The HTTP request exceeded the client's per-request timeout.
    Retry with `timeout=` bumped if the input is legitimately long."""


def _from_response(status: int, body: dict, request_id: str = "") -> TLDRapiError:
    """Server-response → typed exception mapper. Central so every code
    path (sync + async) produces the same typed error for the same
    server shape.
    """
    err_code = (body.get("error_code") or body.get("error") or "").lower() if isinstance(body, dict) else ""
    msg = _extract_message(body, status)

    kwargs = dict(status_code=status, request_id=request_id, response_body=body if isinstance(body, dict) else {})

    if status == 401 or status == 403:
        return AuthenticationError(msg, **kwargs)
    if status == 402:
        return InsufficientCreditsError(msg, **kwargs)
    if status == 429:
        # Retry-After is a top-level header, not in the body; caller
        # supplies it via subclass constructor after this function returns
        # if it wants to enrich. We still return a RateLimitError here so
        # the shape is correct.
        return RateLimitError(msg, **kwargs)
    if status == 400:
        if err_code == "language_not_supported" or "language" in err_code:
            return LanguageNotSupportedError(msg, **kwargs)
        if err_code == "quality_selection_requires_paid_plan":
            return QualitySelectionRequiresPaidPlanError(msg, **kwargs)
        return InvalidRequestError(msg, **kwargs)
    if 500 <= status < 600:
        return ServerError(msg, **kwargs)
    # Unknown status — surface as generic
    return TLDRapiError(msg, **kwargs)


def _extract_message(body, status: int) -> str:
    if isinstance(body, dict):
        for k in ("message", "detail", "error", "reason"):
            v = body.get(k)
            if isinstance(v, str) and v:
                return v
    if isinstance(body, str) and body:
        return body[:400]
    return f"HTTP {status}"
