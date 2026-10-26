"""Resilience and rate-limiting retry handler with exponential backoff and randomized jitter."""
import asyncio
from functools import wraps
import logging
from typing import Callable, Optional

from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, TooManyRequests
from googleapiclient.errors import HttpError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

import inspect

logger = logging.getLogger("gmail_assistant.core.retry")



def is_retryable_error(exc: BaseException) -> bool:
    """Determine whether an exception is a rate limit or transient error that should be retried.

    Catches:
    - Google API HttpError with status 429 (rate limit) or 503 (service unavailable)
    - Vertex AI / Google Cloud ResourceExhausted, ServiceUnavailable, TooManyRequests
    - Generic HTTP 429/503 errors and rate limit error signatures
    """
    if isinstance(exc, (ResourceExhausted, ServiceUnavailable, TooManyRequests)):
        return True

    if isinstance(exc, HttpError):
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (429, 500, 502, 503, 504):
            return True

    status_code = getattr(exc, "status_code", getattr(exc, "code", None))
    if status_code in (429, 500, 502, 503, 504):
        return True

    exc_name = type(exc).__name__
    if any(k in exc_name for k in ("ResourceExhausted", "RateLimit", "QuotaExceeded", "TooManyRequests")):
        return True

    msg = str(exc).lower()
    if any(k in msg for k in ("resource_exhausted", "resourceexhausted", "quota exceeded", "rate limit", "429")):
        return True

    return False


def _log_retry(retry_state: RetryCallState) -> None:
    """Log retry attempt with backoff delay details without exposing sensitive payload data."""
    attempt = retry_state.attempt_number
    next_action = retry_state.next_action
    sleep_time = round(next_action.sleep, 2) if next_action else 0.0
    fn_name = getattr(retry_state.fn, "__name__", str(retry_state.fn))
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Rate limit / transient error on %s (attempt %d). Backing off for %.2fs. Cause: %s",
        fn_name,
        attempt,
        sleep_time,
        type(exc).__name__ if exc else "Unknown",
    )


def with_retry(
    max_attempts: int = 4,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    retry_predicate: Optional[Callable[[BaseException], bool]] = None,
):
    """Reusable exponential backoff and jitter decorator using tenacity.

    Supports both asynchronous and synchronous functions.
    Catches Google API 429/503 quota errors and Vertex AI ResourceExhausted.
    """
    predicate = retry_predicate or is_retryable_error

    def decorator(fn: Callable):
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)

            async def async_wrapper(*args, **kwargs):
                retrying = AsyncRetrying(
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_random_exponential(min=min_wait, max=max_wait),
                    retry=retry_if_exception(predicate),
                    before_sleep=_log_retry,
                    reraise=True,
                )
                async for attempt in retrying:
                    with attempt:
                        return await fn(*args, **kwargs)

            return async_wrapper
        else:
            @wraps(fn)
            def sync_wrapper(*args, **kwargs):
                retrying = Retrying(
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_random_exponential(min=min_wait, max=max_wait),
                    retry=retry_if_exception(predicate),
                    before_sleep=_log_retry,
                    reraise=True,
                )
                for attempt in retrying:
                    with attempt:
                        return fn(*args, **kwargs)

            return sync_wrapper

    return decorator
