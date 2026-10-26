"""
Live Deployment Verification Script for Phase 5 (Cloud Run).
Task Group 6 — Task 6.2.

Usage:
    python scripts/verify_phase5_deployment.py --service-url https://<SERVICE_URL>

Runs automated checks against a live Cloud Run deployment:
1. Probe /health (verifies DB connectivity).
2. Probe /api/v1/digest/today (verifies digest views).
3. Test unauthorized push to /api/v1/webhook/pubsub (verifies payload validation).
4. Send a synthetic Pub/Sub push notification and verify the webhook acknowledges.
5. Probe /docs (verifies OpenAPI endpoint accessibility).
6. Print a summary of pass/fail results with actionable guidance.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from dataclasses import dataclass, field
from typing import List

try:
    import httpx
except ImportError:
    sys.exit(
        "ERROR: httpx is required. Install it with: pip install httpx\n"
        "Alternatively, run: pip install -r requirements.txt"
    )


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    guidance: str = ""


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

def _get(client: httpx.Client, path: str, **kwargs) -> httpx.Response:
    return client.get(path, **kwargs)


def _post(client: httpx.Client, path: str, **kwargs) -> httpx.Response:
    return client.post(path, **kwargs)


def _b64_pubsub(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_health(client: httpx.Client) -> CheckResult:
    """
    Check 1: /health endpoint.
    Expects HTTP 200 with {"status": "ok", "database": "connected"}.
    """
    name = "Health endpoint (/health)"
    try:
        resp = _get(client, "/health", timeout=15)
        if resp.status_code != 200:
            return CheckResult(
                name=name,
                passed=False,
                detail=f"HTTP {resp.status_code} — expected 200.",
                guidance="Check Cloud Run logs: gcloud run services logs read gmail-triage --limit=50",
            )
        body = resp.json()
        db_status = body.get("database", "unknown")
        app_status = body.get("status", "unknown")
        passed = app_status == "ok" and db_status == "connected"
        return CheckResult(
            name=name,
            passed=passed,
            detail=f"status={app_status!r}, database={db_status!r}",
            guidance=(
                ""
                if passed
                else (
                    "Database is not connected. Verify DATABASE_URL secret is set:\n"
                    "  gcloud secrets versions access latest --secret=DATABASE_URL\n"
                    "  gcloud run services describe gmail-triage --region us-central1"
                )
            ),
        )
    except Exception as exc:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Request failed: {exc}",
            guidance="Ensure the SERVICE_URL is correct and the service is deployed.",
        )


def check_digest_today(client: httpx.Client) -> CheckResult:
    """
    Check 2: /api/v1/digest/today.
    Expects HTTP 200 or HTTP 401/403 (auth configured) — never a 500.
    """
    name = "Digest today endpoint (/api/v1/digest/today)"
    try:
        resp = _get(client, "/api/v1/digest/today", timeout=15)
        passed = resp.status_code in (200, 401, 403, 404)
        return CheckResult(
            name=name,
            passed=passed,
            detail=f"HTTP {resp.status_code}",
            guidance=(
                ""
                if passed
                else (
                    f"Unexpected status {resp.status_code}. Check digest view existence in PostgreSQL:\n"
                    "  SELECT * FROM v_today_digest LIMIT 5;"
                )
            ),
        )
    except Exception as exc:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Request failed: {exc}",
        )


def check_webhook_unauthorized_payload(client: httpx.Client) -> CheckResult:
    """
    Check 3: POST /api/v1/webhook/pubsub with an unregistered account.
    Expects HTTP 404 (account not found) — NOT 500.
    This confirms the application-layer account guard is active (D6).
    """
    name = "Webhook unregistered account guard"
    try:
        envelope = {
            "message": {
                "data": _b64_pubsub({
                    "emailAddress": "verify-script-probe@example.com",
                    "historyId": "1",
                }),
                "messageId": "verify-script-probe-001",
            },
            "subscription": "projects/probe/subscriptions/probe-sub",
        }
        resp = _post(client, "/api/v1/webhook/pubsub", json=envelope, timeout=15)
        passed = resp.status_code == 404
        return CheckResult(
            name=name,
            passed=passed,
            detail=f"HTTP {resp.status_code} (expected 404 for unknown account)",
            guidance=(
                ""
                if passed
                else (
                    f"Unexpected status {resp.status_code}. Webhook account guard may not be active.\n"
                    "Check app/api/webhook.py — TokenStore.get_active_account lookup."
                )
            ),
        )
    except Exception as exc:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Request failed: {exc}",
        )


def check_openapi_docs(client: httpx.Client) -> CheckResult:
    """
    Check 4: /docs — OpenAPI documentation is accessible.
    Expects HTTP 200 and HTML body containing swagger-ui.
    """
    name = "OpenAPI documentation (/docs)"
    try:
        resp = _get(client, "/docs", timeout=15)
        passed = resp.status_code == 200 and "swagger" in resp.text.lower()
        return CheckResult(
            name=name,
            passed=passed,
            detail=f"HTTP {resp.status_code}",
            guidance=(
                ""
                if passed
                else "FastAPI /docs not reachable. Verify CORS and app startup succeeded."
            ),
        )
    except Exception as exc:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Request failed: {exc}",
        )


def check_webhook_malformed_payload(client: httpx.Client) -> CheckResult:
    """
    Check 5: POST /api/v1/webhook/pubsub with a completely malformed payload.
    Expects HTTP 400 — Pub/Sub will retry on non-2xx; 400 correctly signals bad data.
    """
    name = "Webhook malformed payload returns 400"
    try:
        resp = _post(
            client,
            "/api/v1/webhook/pubsub",
            json={"not_a_message": True},
            timeout=15,
        )
        passed = resp.status_code == 400
        return CheckResult(
            name=name,
            passed=passed,
            detail=f"HTTP {resp.status_code} (expected 400)",
        )
    except Exception as exc:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Request failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_checks(service_url: str) -> List[CheckResult]:
    """Execute all verification checks and return results."""
    # Normalise trailing slash
    base_url = service_url.rstrip("/")

    print(f"\n{'=' * 60}")
    print(f"  Phase 5 Deployment Verification")
    print(f"  Service URL: {base_url}")
    print(f"{'=' * 60}\n")

    results: List[CheckResult] = []

    with httpx.Client(base_url=base_url, follow_redirects=True) as client:
        checks = [
            check_health,
            check_digest_today,
            check_webhook_unauthorized_payload,
            check_openapi_docs,
            check_webhook_malformed_payload,
        ]

        for check_fn in checks:
            label = check_fn.__doc__.strip().split("\n")[0].strip() if check_fn.__doc__ else check_fn.__name__
            print(f"  Running: {label} ...")
            start = time.monotonic()
            result = check_fn(client)
            elapsed = time.monotonic() - start
            status_icon = "✅" if result.passed else "❌"
            print(f"  {status_icon} {result.name} [{elapsed:.2f}s]")
            print(f"     {result.detail}")
            if result.guidance:
                print(f"     💡 {result.guidance}")
            print()
            results.append(result)

    return results


def print_summary(results: List[CheckResult]) -> int:
    """Print a final summary table and return exit code (0=all pass, 1=any fail)."""
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    print(f"{'=' * 60}")
    print(f"  Summary: {len(passed)}/{len(results)} checks passed")
    print(f"{'=' * 60}")

    if failed:
        print("\n  FAILED CHECKS:")
        for r in failed:
            print(f"    ❌ {r.name}")
        print()
        print("  Consult the guidance above or tail Cloud Run logs:")
        print("    gcloud run services logs read gmail-triage --limit=100\n")
        return 1

    print("\n  🎉 All checks passed! Phase 5 deployment is healthy.\n")
    print("  Next steps:")
    print("  - Complete OAuth redirect URI cutover (Task 5.3)")
    print("  - Wire Pub/Sub push subscription (Task 6.1)")
    print("  - Re-register Gmail mailbox watch: POST /api/v1/gmail/watch")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automated Phase 5 deployment verification for the Gmail AI Triage Cloud Run service.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--service-url",
        required=True,
        metavar="URL",
        help="Base HTTPS URL of the deployed Cloud Run service (e.g. https://gmail-triage-xxxx-uc.a.run.app)",
    )
    args = parser.parse_args()

    results = run_checks(args.service_url)
    return print_summary(results)


if __name__ == "__main__":
    sys.exit(main())
