# 29 — Provider Resilience Contract

## The problem

The `LLMProvider` protocol (contract 01) defines the interface but says nothing about failure. LLM providers fail — they rate-limit, return 5xx errors, time out, and occasionally go down entirely. Without a resilience layer, any provider failure immediately surfaces to the user and stops all agent work.

This contract defines the retry policy, error taxonomy, circuit breaker, and fallback pattern that sit between `AgentRunner` and the raw provider adapter.

---

## Error taxonomy

Provider errors are classified before any retry decision is made. Classification determines whether to retry, wait, or fail immediately.

```python
# ai/providers/errors.py

class ProviderError(Exception):
    """Base class for all LLM provider errors."""

class RateLimitError(ProviderError):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after  # seconds from Retry-After header, or None

class ProviderServerError(ProviderError):
    """5xx responses — transient, retryable."""

class ProviderTimeoutError(ProviderError):
    """Request timed out — retryable once."""

class ContextWindowError(ProviderError):
    """Input exceeds the model's context window — not retryable."""

class ContentPolicyError(ProviderError):
    """Provider rejected the request for content policy reasons — not retryable."""

class AuthError(ProviderError):
    """Invalid or expired API key — not retryable. Alert immediately."""

class ProviderUnavailableError(ProviderError):
    """Circuit breaker is open — provider is considered down."""
```

| Error | Retryable | Action |
|---|---|---|
| `RateLimitError` | Yes | Wait `retry_after` seconds (or backoff), then retry |
| `ProviderServerError` | Yes | Exponential backoff, then retry |
| `ProviderTimeoutError` | Yes, once | Retry after base delay |
| `ContextWindowError` | No | Return `AgentResult(status="failed")` immediately |
| `ContentPolicyError` | No | Return `AgentResult(status="failed")` immediately |
| `AuthError` | No | Log alert, return 500 — never surface API key details to user |
| `ProviderUnavailableError` | No | Circuit breaker is open — return 503 |

---

## Retry policy

```python
# ai/providers/resilience.py
import time
import random
from my_app.ai.providers.errors import (
    RateLimitError, ProviderServerError, ProviderTimeoutError,
    ContextWindowError, ContentPolicyError, AuthError,
)

MAX_RETRIES = 3
BASE_DELAY = 1.0    # seconds
MAX_DELAY = 30.0    # seconds


def _backoff(attempt: int, base: float = BASE_DELAY, cap: float = MAX_DELAY) -> float:
    """Exponential backoff with full jitter."""
    delay = min(cap, base * (2 ** attempt))
    return random.uniform(0, delay)


NON_RETRYABLE = (ContextWindowError, ContentPolicyError, AuthError)
RETRYABLE = (RateLimitError, ProviderServerError, ProviderTimeoutError)
```

---

## `ResilientProvider`

`ResilientProvider` wraps any `LLMProvider` with retry logic, circuit breaking, and optional fallback. It implements the same `LLMProvider` protocol — callers cannot tell the difference.

```python
# ai/providers/resilience.py
from my_app.ai.providers.base import LLMProvider, LLMResponse, LLMConfig, Message
from my_app.ai.providers.circuit_breaker import CircuitBreaker, CircuitOpenError


class ResilientProvider:

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None = None,
        max_retries: int = MAX_RETRIES,
        base_delay: float = BASE_DELAY,
        max_delay: float = MAX_DELAY,
    ):
        self._primary = primary
        self._fallback = fallback
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._circuit = CircuitBreaker(failure_threshold=5, reset_timeout=300)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict],
        config: LLMConfig,
    ) -> LLMResponse:
        if self._circuit.is_open():
            if self._fallback:
                return self._fallback.chat(messages, tools, config)
            raise ProviderUnavailableError("Primary provider circuit is open.")

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = self._primary.chat(messages, tools, config)
                self._circuit.record_success()
                return response

            except NON_RETRYABLE as e:
                self._circuit.record_failure()
                raise  # fail immediately — no retry

            except RateLimitError as e:
                last_error = e
                wait = e.retry_after if e.retry_after else _backoff(attempt, self._base_delay, self._max_delay)
                _log_retry(attempt, "rate_limit", wait)
                time.sleep(wait)

            except (ProviderServerError, ProviderTimeoutError) as e:
                last_error = e
                self._circuit.record_failure()
                if attempt == self._max_retries:
                    break
                wait = _backoff(attempt, self._base_delay, self._max_delay)
                _log_retry(attempt, type(e).__name__, wait)
                time.sleep(wait)

        # All retries exhausted — try fallback if available
        if self._fallback:
            _log_fallback_attempt()
            return self._fallback.chat(messages, tools, config)

        raise last_error

    def stream(self, messages, tools, config):
        # Same retry pattern as chat() — omitted for brevity
        ...

    def count_tokens(self, text: str) -> int:
        return self._primary.count_tokens(text)
```

---

## Circuit breaker

The circuit breaker prevents the system from hammering a failing provider and gives it time to recover.

```python
# ai/providers/circuit_breaker.py
import threading
from datetime import datetime, timedelta


class CircuitBreaker:
    """
    States:
      CLOSED   — normal operation, requests pass through
      OPEN     — too many failures, requests fail fast
      HALF_OPEN — testing recovery, one request allowed through
    """

    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 300):
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout   # seconds before moving OPEN → HALF_OPEN
        self._failure_count = 0
        self._last_failure_time: datetime | None = None
        self._state = "CLOSED"
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            if self._state == "OPEN":
                if (datetime.utcnow() - self._last_failure_time).seconds >= self._reset_timeout:
                    self._state = "HALF_OPEN"
                    return False
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = "CLOSED"

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.utcnow()
            if self._failure_count >= self._failure_threshold:
                if self._state != "OPEN":
                    _log_circuit_open(self._failure_count)
                self._state = "OPEN"
```

**Threshold defaults:**

| Setting | Default | Meaning |
|---|---|---|
| `failure_threshold` | 5 | Consecutive failures before circuit opens |
| `reset_timeout` | 300s (5 min) | Time before circuit allows a test request |

For multi-process deployments (multiple workers), use a Redis-backed circuit breaker instead of in-memory state. The in-memory version is sufficient for single-process or development.

---

## Fallback provider

A fallback provider is optional. When configured, it receives requests after all primary retries are exhausted or when the circuit is open.

```python
# config/default.py
PRIMARY_LLM_PROVIDER = "anthropic"    # provider adapter name
FALLBACK_LLM_PROVIDER = "openai"      # None if no fallback configured
```

```python
# ai/providers/__init__.py

def get_provider() -> LLMProvider:
    primary = _build_provider(current_app.config["PRIMARY_LLM_PROVIDER"])
    fallback_name = current_app.config.get("FALLBACK_LLM_PROVIDER")
    fallback = _build_provider(fallback_name) if fallback_name else None
    return ResilientProvider(primary=primary, fallback=fallback)
```

**Fallback constraints:**
- The fallback must implement the same `LLMProvider` protocol.
- The fallback uses its own API key — not the primary's.
- Tool schemas must be compatible with both providers. If the primary and fallback use different tool-call formats, the provider adapter normalises them before they reach `AgentRunner`.
- The fallback is transparent to `AgentRunner` — it never knows which provider handled the call.

---

## How errors surface to users

| Error | HTTP status | User-facing message |
|---|---|---|
| `RateLimitError` (after retries) | 429 | "The AI service is busy. Please try again in a moment." |
| `ProviderServerError` (after retries + fallback) | 503 | "The AI service is temporarily unavailable." |
| `ProviderUnavailableError` (circuit open, no fallback) | 503 | "The AI service is temporarily unavailable." |
| `ContextWindowError` | 422 | "Your request is too long for this model. Try breaking it into smaller steps." |
| `ContentPolicyError` | 422 | "The AI provider declined this request. Rephrase and try again." |
| `AuthError` | 500 | Generic server error — never expose key details. Alert on-call. |

`AuthError` must never surface provider or key information to the user. Log it at ERROR level and trigger an alert immediately — a bad API key stops all AI functionality.

---

## Telemetry

Log every retry and every circuit state change:

```python
def _log_retry(attempt: int, error_type: str, wait: float) -> None:
    logger.warning("provider_retry", extra={
        "event": "provider.retry",
        "attempt": attempt,
        "error_type": error_type,
        "wait_seconds": wait,
    })

def _log_fallback_attempt() -> None:
    logger.warning("provider_fallback", extra={
        "event": "provider.fallback",
    })

def _log_circuit_open(failure_count: int) -> None:
    logger.error("provider_circuit_open", extra={
        "event": "provider.circuit_open",
        "failure_count": failure_count,
    })
```

Track these as metrics (see [18_observability.md](18_observability.md)):
- `provider.retry_count` per provider, per error type
- `provider.fallback_count`
- `provider.circuit_open_count`
- `provider.error_rate` (errors / total calls, rolling 5 min window)

---

## Integration point

`ResilientProvider` is instantiated once and injected wherever `get_provider()` is called. No changes are required to `AgentRunner`, `IntentRouter`, or `PlanningOrchestrator` — they all call `get_provider()` and receive a resilient provider transparently.

```python
# Before (bare provider — no resilience):
def get_provider() -> LLMProvider:
    return AnthropicProvider(api_key=current_app.config["ANTHROPIC_API_KEY"])

# After (resilient provider):
def get_provider() -> LLMProvider:
    primary = AnthropicProvider(api_key=current_app.config["ANTHROPIC_API_KEY"])
    fallback = OpenAIProvider(api_key=current_app.config.get("OPENAI_API_KEY"))
    return ResilientProvider(primary=primary, fallback=fallback)
```

---

## What provider resilience must NOT do

- Retry non-retryable errors (`ContextWindowError`, `ContentPolicyError`, `AuthError`) — fail immediately.
- Hide `AuthError` from monitoring — alert immediately when auth fails.
- Share circuit breaker state across providers — each primary/fallback pair has its own circuit.
- Count retry token usage differently from normal usage — retried calls consume real tokens and must be tracked in `AgentSessionLog`.
- Use the fallback provider for cost-saving purposes — fallback is for resilience only, not model selection.
- Retry indefinitely — always enforce `MAX_RETRIES`.
