# tldrapi — Python SDK for TLDRapi

Official Python client for [TLDRapi](https://tldrapi.com) — summarize any
content, in one API call. 5 quality tiers, 20+ built-in voice styles,
custom voices for paid tiers. Thin (one runtime dep: `httpx`), sync +
async, typed exceptions per error class.

**TLDRapi is distributed through the RapidAPI marketplace at launch.**
Subscribe to the TLDRapi listing on RapidAPI, get your `X-RapidAPI-Key`
from the RapidAPI dashboard, and pass it to the client.

```bash
pip install tldrapi
```

## Get your app's RapidAPI key

1. Sign in at [rapidapi.com](https://rapidapi.com)
2. Subscribe to the [TLDRapi Summarizer](https://rapidapi.com/thunderAPIs256/api/tldrapi-summarizer) listing (start with **BASIC** — free)
3. Go to **Console** (top nav) → **Applications** → **Add App** (or open an existing one)
4. In the App → **Authorizations** tab → click the copy icon next to your Authorization Key

That's the app's `X-RapidAPI-Key`. Pass it to the SDK constructor.

*Legacy path (deprecated): upper-right (?) → Legacy Developer Dashboard → Add New App → Authorization tab. The new Console path above is simpler.*

The Authorization Key field is the same value in both places — RapidAPI just labels it differently depending on which interface you use:

**New Console:**

![RapidAPI Console — Authorization Method labeled "RAPIDAPI"](https://raw.githubusercontent.com/unitycubed/tldrapi-docs/main/img/rapidapi-key-label-console.png)

**Legacy Developer Dashboard:**

![RapidAPI Legacy Developer Dashboard — Authorization Method labeled "API key"](https://raw.githubusercontent.com/unitycubed/tldrapi-docs/main/img/rapidapi-key-label-legacy.png)



## Quick start

```python
from tldrapi import TLDRapi

client = TLDRapi(rapidapi_key="YOUR_RAPIDAPI_KEY")
result = client.summarize("Long text here...", tier="standard")
print(result.summary)
print(f"Credits remaining: {result.credits.get('remaining')}")
```

## Async

```python
import asyncio
from tldrapi import AsyncTLDRapi

async def main():
    async with AsyncTLDRapi(rapidapi_key="YOUR_RAPIDAPI_KEY") as client:
        r = await client.summarize("Long text here...", tier="deep")
        print(r.summary)

asyncio.run(main())
```

## Quality tiers

Pass `tier="quick" | "standard" | "deep" | "premium" | "ultra"` per
call. Higher tier → higher quality, more credits, larger allowed input.

| Tier      | Credits/call | Max input tokens | Best for                          |
|-----------|-------------:|-----------------:|-----------------------------------|
| quick     |            1 |            4,000 | Short texts, previews             |
| standard  |            5 |           16,000 | Default                           |
| deep      |           30 |           32,000 | Longer content, deeper reasoning  |
| premium   |          110 |           64,000 | Substantial documents             |
| ultra     |          400 |          100,000 | Long-form / research-grade        |

Live rates: `client.rates()`.

## Session pinning

If you're processing a batch and want the same underlying model for
every call, pass `session_id` from the previous result:

```python
r1 = client.summarize("Doc 1")
r2 = client.summarize("Doc 2", session_id=r1.session_id)
r3 = client.summarize("Doc 3", session_id=r1.session_id)
```

## Error handling

All SDK exceptions inherit from `TLDRapiError`. Catch specifically:

```python
from tldrapi import (
    TLDRapi,
    InsufficientCreditsError,
    RateLimitError,
    LanguageNotSupportedError,
    QualitySelectionRequiresPaidPlanError,
    ServerError,
    TimeoutError,
)

client = TLDRapi(rapidapi_key="YOUR_RAPIDAPI_KEY")

try:
    r = client.summarize(user_text, tier="deep")
except InsufficientCreditsError as e:
    top_up_url = e.response_body.get("options", {}).get("top_up", {}).get("url")
    # …present top-up flow to your user…
except RateLimitError as e:
    time.sleep(e.retry_after_seconds or 60)
    # …retry once the plan window resets…
except LanguageNotSupportedError as e:
    lang = e.response_body.get("detected_language_name")
    # …English-only at launch; cross-lingual is a Month 2-3 feature…
except QualitySelectionRequiresPaidPlanError:
    # Free plan can't select tiers; retry without tier=
    r = client.summarize(user_text)
except TimeoutError:
    # Long inputs on deep+ can legitimately need >60s. Retry with a bigger timeout.
    r = client.summarize(user_text, tier="deep", timeout=120)
```

Every error carries `.status_code`, `.request_id` (X-Request-ID from
the response — attach when reporting issues), and `.response_body`
(the full parsed JSON error body).

## Configuration

```python
client = TLDRapi(
    rapidapi_key="YOUR_RAPIDAPI_KEY",
    rapidapi_host="tldrapi-summarization.p.rapidapi.com",   # override to point at a staging listing
    base_url=None,                           # default = https://{rapidapi_host}
    timeout=60.0,                            # default 60s per request
    retries=3,                               # 5xx + network retries, exponential backoff
)
```

## Retries

The client automatically retries **5xx** responses and transient
network failures with exponential backoff + jitter (default 3
attempts).

**Not retried**:
- **4xx responses** — permanent failures; retrying an
  `insufficient_credits` would waste more of the same failing call.
- **429 rate-limit** — honor the server's `Retry-After` header. The
  SDK exposes it on `RateLimitError.retry_after_seconds`.

## Usage & rates

```python
u = client.usage()           # this month's calls, credits charged/remaining
r = client.rates()           # current credits/call per tier
```

## Development

```bash
pip install -e '.[dev]'
pytest -q                    # 20 tests, all HTTP mocked via respx
```

## License

Released under the MIT License — see [LICENSE](LICENSE).

Copyright (c) 2026 Ehren Biglari / Unity Cubed.
