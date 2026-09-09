"""TLDRapi Python SDK.

Thin, dependency-light client for the TLDRapi summarization API.
Summarize any content, in one API call.
Wraps `POST /summarize` and the `/convert/*` family; exposes typed
exceptions so callers can react to specific failure modes without
string-matching error messages.

TLDRapi is distributed via the RapidAPI marketplace at launch. Subscribe
to the TLDRapi listing on RapidAPI and use the X-RapidAPI-Key it gives
you.

Basic use:

    from tldrapi import TLDRapi
    client = TLDRapi(rapidapi_key="YOUR_RAPIDAPI_KEY")
    result = client.summarize("Long text here...", tier="standard")
    print(result.summary)

Async variant:

    import asyncio
    from tldrapi import AsyncTLDRapi
    async def main():
        async with AsyncTLDRapi(rapidapi_key="YOUR_RAPIDAPI_KEY") as client:
            result = await client.summarize("Long text here...")
            print(result.summary)
    asyncio.run(main())
"""

from .client import TLDRapi, AsyncTLDRapi
from .models import SummarizeResult, Usage, UsageStats, Rates
from .errors import (
    TLDRapiError,
    AuthenticationError,
    InsufficientCreditsError,
    RateLimitError,
    LanguageNotSupportedError,
    QualitySelectionRequiresPaidPlanError,
    ServerError,
    NetworkError,
    TimeoutError,
    InvalidRequestError,
)

__version__ = "0.1.0"
__all__ = [
    "TLDRapi",
    "AsyncTLDRapi",
    "SummarizeResult",
    "Usage",
    "UsageStats",
    "Rates",
    "TLDRapiError",
    "AuthenticationError",
    "InsufficientCreditsError",
    "RateLimitError",
    "LanguageNotSupportedError",
    "QualitySelectionRequiresPaidPlanError",
    "ServerError",
    "NetworkError",
    "TimeoutError",
    "InvalidRequestError",
]
