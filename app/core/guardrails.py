"""Formal Read-Only Safety Guardrails.

Enforces Constitutional Principle 1 (Least Privilege & Read-Only Safety):
- Asserts that active Google OAuth scopes only include read-only permissions
  (gmail.readonly and userinfo.email).
- Dynamically introspects GmailClient to guarantee absence of send, draft, delete,
  or modify methods.
- Halts application startup if any mutation capability is detected.
"""
import inspect
import logging
from typing import Iterable, Optional, Type

from app.services.auth_service import READONLY_SCOPES
from app.services.gmail import GmailClient

logger = logging.getLogger("gmail_assistant.core.guardrails")

# Expose GOOGLE_CLIENT_SCOPES alias for consistency
GOOGLE_CLIENT_SCOPES = READONLY_SCOPES

ALLOWED_SCOPE_PATTERNS = {
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "gmail.readonly",
    "userinfo.email",
}

FORBIDDEN_METHOD_PATTERNS = (
    "send",
    "draft",
    "delete",
    "modify",
    "trash",
    "insert",
)


class ReadOnlySafetyViolationError(Exception):
    """Raised when a forbidden mutation capability, method, or write scope is detected."""
    pass


def verify_read_only_safety(
    client_cls: Optional[Type] = None,
    scopes: Optional[Iterable[str]] = None,
) -> bool:
    """Validate system-wide read-only safety guarantees.

    Args:
        client_cls: Gmail client class to introspect (defaults to GmailClient).
        scopes: OAuth scopes to validate (defaults to GOOGLE_CLIENT_SCOPES).

    Raises:
        ReadOnlySafetyViolationError: If non-readonly scopes or mutation methods are found.

    Returns:
        True if all safety assertions hold.
    """
    effective_scopes = list(scopes if scopes is not None else GOOGLE_CLIENT_SCOPES)
    effective_client = client_cls or GmailClient

    logger.info("Executing Read-Only Safety Guardrail verification...")

    # 1. Scope Verification
    for s in effective_scopes:
        if s not in ALLOWED_SCOPE_PATTERNS:
            raise ReadOnlySafetyViolationError(
                f"Unauthorized scope detected: '{s}'. Only strictly read-only scopes "
                f"({ALLOWED_SCOPE_PATTERNS}) are permitted."
            )

    # 2. Dynamic Client Method Introspection
    # Inspect all callable attributes on the class and any instantiated properties
    client_methods = [
        name
        for name, member in inspect.getmembers(effective_client, predicate=callable)
        if not name.startswith("__")
    ]

    for method_name in client_methods:
        lower_name = method_name.lower()
        for forbidden in FORBIDDEN_METHOD_PATTERNS:
            if forbidden in lower_name:
                raise ReadOnlySafetyViolationError(
                    f"Security violation: Forbidden mutation method '{method_name}' "
                    f"detected on {effective_client.__name__}. The application must operate "
                    "strictly in read-only mode with zero mutation capabilities."
                )

    logger.info(
        "Read-Only Safety Guardrail PASSED: %d scopes verified, %d client methods verified.",
        len(effective_scopes),
        len(client_methods),
    )
    return True
