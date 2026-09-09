"""Result types returned by TLDRapi SDK methods.

These are thin dataclasses that mirror the server's response shape.
Keeping them as dataclasses (not dicts) means callers get IDE
autocomplete, static-type checking, and don't have to memorize field
names — critical for developer-experience on the primary product
surface (the API IS the product).

Everything the server returns is preserved on `.raw` for callers who
need a field we haven't hoisted yet — no need to fork the SDK to read
one obscure header.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Usage:
    """Per-call usage details from POST /summarize."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0
    model_used: str = ""


@dataclass
class SummarizeResult:
    """Result of `client.summarize(...)`.

    Fields:
      summary       — the summarized text
      session_id    — server-issued session id; pass to a future call
                      via `session_id=` to keep session-model + session
                      state stable across calls
      usage         — token counts + model used for THIS call
      request_id    — X-Request-ID; attach when reporting an issue
      credits       — {balance, charged, tier} snapshot from response headers
      raw           — full parsed JSON body, for anything not hoisted
    """
    summary: str
    session_id: str = ""
    usage: Usage = field(default_factory=Usage)
    request_id: str = ""
    credits: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class UsageStats:
    """Return of `client.usage()`."""
    period: str = ""
    calls: int = 0
    credits_charged: int = 0
    credits_remaining: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class Rates:
    """Return of `client.rates()` — current credits/call per tier."""
    quick: int = 1
    standard: int = 5
    deep: int = 30
    premium: int = 110
    ultra: int = 400
    updated_at: Optional[str] = None
    raw: dict = field(default_factory=dict)
