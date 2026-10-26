"""Unit tests for Read-Only Safety Guardrail and Resilience Retry Handler."""
import asyncio
import httplib2
import pytest
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, TooManyRequests
from googleapiclient.errors import HttpError

from app.core.guardrails import (
    ReadOnlySafetyViolationError,
    verify_read_only_safety,
)
from app.core.retry import is_retryable_error, with_retry
from app.services.gmail import GmailClient


# ==============================================================================
# Read-Only Safety Guardrail Tests
# ==============================================================================

def test_verify_read_only_safety_valid():
    """Verify read-only guardrail passes cleanly with active application client and scopes."""
    assert verify_read_only_safety() is True


def test_verify_read_only_safety_forbidden_scope():
    """Verify guardrail rejects forbidden mutation scopes like gmail.send or gmail.modify."""
    forbidden_scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    with pytest.raises(ReadOnlySafetyViolationError) as exc_info:
        verify_read_only_safety(scopes=forbidden_scopes)
    assert "Unauthorized scope detected" in str(exc_info.value)
    assert "gmail.send" in str(exc_info.value)


def test_verify_read_only_safety_mail_google_com_scope():
    """Verify guardrail rejects full mailbox access scope."""
    with pytest.raises(ReadOnlySafetyViolationError):
        verify_read_only_safety(scopes=["https://mail.google.com/"])


def test_verify_read_only_safety_forbidden_client_method():
    """Verify guardrail dynamically detects mutation methods on client classes."""
    class MaliciousClient:
        def get_profile(self):
            return {}

        def send_message(self, recipient: str, body: str):
            """Prohibited mutation method."""
            return {}

    with pytest.raises(ReadOnlySafetyViolationError) as exc_info:
        verify_read_only_safety(client_cls=MaliciousClient)
    assert "Security violation: Forbidden mutation method 'send_message'" in str(exc_info.value)

    class DraftClient:
        def create_draft(self):
            pass

    with pytest.raises(ReadOnlySafetyViolationError) as exc_info:
        verify_read_only_safety(client_cls=DraftClient)
    assert "Forbidden mutation method 'create_draft'" in str(exc_info.value)

    class DeleteClient:
        def delete_thread(self):
            pass

    with pytest.raises(ReadOnlySafetyViolationError) as exc_info:
        verify_read_only_safety(client_cls=DeleteClient)
    assert "Forbidden mutation method 'delete_thread'" in str(exc_info.value)


# ==============================================================================
# Resilience & Retry Backoff Tests
# ==============================================================================

def _make_http_error(status_code: int, reason: str = "Error") -> HttpError:
    resp = httplib2.Response({"status": str(status_code)})
    return HttpError(resp, f"HTTP {status_code} {reason}".encode("utf-8"))


def test_is_retryable_error_classification():
    """Verify error classifier accurately identifies rate limit and transient quota errors."""
    # Retryable: 429 and 503 HTTP errors
    assert is_retryable_error(_make_http_error(429, "Too Many Requests")) is True
    assert is_retryable_error(_make_http_error(503, "Service Unavailable")) is True
    assert is_retryable_error(_make_http_error(500, "Internal Server Error")) is True

    # Retryable: Google Cloud API Core quota errors
    assert is_retryable_error(ResourceExhausted("Quota exceeded for quota metric")) is True
    assert is_retryable_error(TooManyRequests("Rate limit reached")) is True
    assert is_retryable_error(ServiceUnavailable("Backend unavailable")) is True

    # Non-retryable: client errors or logic bugs
    assert is_retryable_error(_make_http_error(400, "Bad Request")) is False
    assert is_retryable_error(_make_http_error(404, "Not Found")) is False
    assert is_retryable_error(ValueError("Invalid argument")) is False
    assert is_retryable_error(KeyError("missing_key")) is False


@pytest.mark.asyncio
async def test_with_retry_succeeds_after_transient_failures():
    """Verify with_retry retries transient 429 errors and succeeds when an attempt passes."""
    call_count = 0

    @with_retry(max_attempts=4, min_wait=0.01, max_wait=0.05)
    async def transient_operation():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise _make_http_error(429, "Rate limit reached")
        return "success"

    result = await transient_operation()
    assert result == "success"
    assert call_count == 3


@pytest.mark.asyncio
async def test_with_retry_exhaustion():
    """Verify with_retry stops and raises after exceeding maximum attempts."""
    call_count = 0

    @with_retry(max_attempts=3, min_wait=0.01, max_wait=0.05)
    async def persistent_failure():
        nonlocal call_count
        call_count += 1
        raise ResourceExhausted("Rate quota exhausted")

    with pytest.raises(ResourceExhausted):
        await persistent_failure()

    assert call_count == 3


@pytest.mark.asyncio
async def test_with_retry_does_not_retry_non_retryable_error():
    """Verify with_retry fails immediately without retrying on non-retryable errors."""
    call_count = 0

    @with_retry(max_attempts=4, min_wait=0.01, max_wait=0.05)
    async def client_error_operation():
        nonlocal call_count
        call_count += 1
        raise ValueError("Invalid parameter value")

    with pytest.raises(ValueError):
        await client_error_operation()

    assert call_count == 1
