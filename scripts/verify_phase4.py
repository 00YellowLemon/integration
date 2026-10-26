"""Interactive End-to-End Verification Walkthrough for Phase 4.

Verifies:
1. Constitutional Principle 1 Read-Only Safety Guardrail.
2. Live Supabase PostgreSQL database views:
   - ``v_urgent_triage_feed``
   - ``v_pending_action_items``
   - ``v_today_digest``
3. Strategic composite database indexes:
   - ``ix_email_analyses_urgency_created``
   - ``ix_action_items_status_deadline``
4. Live FastAPI REST endpoints:
   - ``GET /api/v1/digest/today``
   - ``GET /api/v1/action-items``
   - ``GET /api/v1/threads/{thread_id}/triage``

Usage:
    python scripts/verify_phase4.py
"""
import asyncio
import sys

sys.path.insert(0, ".")

import httpx
from sqlalchemy import select, text

from app.core.guardrails import verify_read_only_safety
from app.db.session import get_engine, get_session_maker, init_db
from app.main import app
from app.models.thread import Thread


def banner():
    print("\n" + "=" * 75)
    print("   GMAIL AI ASSISTANT - PHASE 4 VERIFICATION (VIEWS, DIGEST & GUARDRAILS)")
    print("=" * 75 + "\n")


async def step_1_guardrails():
    print("[1/4] Enforcing Constitutional Read-Only Safety Guardrail...")
    try:
        verify_read_only_safety()
        print("  [OK] Read-only safety guardrail PASSED:")
        print("       - All Google OAuth scopes strictly read-only (gmail.readonly, userinfo.email)")
        print("       - Dynamic GmailClient introspection confirmed ZERO mutation methods (send/draft/delete/modify)")
        return True
    except Exception as e:
        print(f"  [FAIL] Read-only guardrail violation: {e}")
        return False


async def step_2_database_views_and_indexes():
    print("\n[2/4] Verifying live database views and composite indexes...")
    engine = get_engine()
    try:
        await init_db(engine)
        print("  [OK] init_db() completed (tables and views ensured).")

        async with engine.connect() as conn:
            # Query v_urgent_triage_feed
            urgent_res = await conn.execute(text("SELECT COUNT(*) FROM v_urgent_triage_feed"))
            urgent_cnt = urgent_res.scalar()
            print(f"  [OK] View 'v_urgent_triage_feed' queryable (row count: {urgent_cnt})")

            # Query v_pending_action_items
            action_res = await conn.execute(text("SELECT COUNT(*) FROM v_pending_action_items"))
            action_cnt = action_res.scalar()
            print(f"  [OK] View 'v_pending_action_items' queryable (row count: {action_cnt})")

            # Query v_today_digest
            digest_res = await conn.execute(text("SELECT COUNT(*) FROM v_today_digest"))
            digest_cnt = digest_res.scalar()
            print(f"  [OK] View 'v_today_digest' queryable (categories: {digest_cnt})")

            # Verify PostgreSQL indexes if connected to PostgreSQL
            if engine.dialect.name == "postgresql":
                idx_res = await conn.execute(text("""
                    SELECT indexname FROM pg_indexes
                    WHERE tablename IN ('email_analyses', 'action_items')
                """))
                indexes = [row[0] for row in idx_res.fetchall()]
                print("  [OK] Active PostgreSQL indexes on triage tables:")
                for idx in indexes:
                    print(f"       - {idx}")
                assert "ix_email_analyses_urgency_created" in indexes, "Missing ix_email_analyses_urgency_created"
                assert "ix_action_items_status_deadline" in indexes, "Missing ix_action_items_status_deadline"

        return True
    except Exception as e:
        print(f"  [FAIL] Database view / index verification error: {e}")
        return False


async def step_3_rest_endpoints():
    print("\n[3/4] Verifying Phase 4 REST API endpoints...")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/v1/digest/today
        digest_resp = await client.get("/api/v1/digest/today")
        if digest_resp.status_code != 200:
            print(f"  [FAIL] GET /api/v1/digest/today returned status {digest_resp.status_code}: {digest_resp.text}")
            return False
        digest_data = digest_resp.json()
        print(f"  [OK] GET /api/v1/digest/today (Status 200)")
        print(f"       Total emails in lookback: {digest_data.get('total_emails')}")
        print(f"       Urgency distribution:    {digest_data.get('urgency_distribution')}")
        print(f"       Categories reported:     {len(digest_data.get('categories', []))}")
        print(f"       Urgent highlights:       {len(digest_data.get('urgent_highlights', []))}")

        # 2. GET /api/v1/action-items
        actions_resp = await client.get("/api/v1/action-items?status=pending")
        if actions_resp.status_code != 200:
            print(f"  [FAIL] GET /api/v1/action-items returned status {actions_resp.status_code}: {actions_resp.text}")
            return False
        actions_data = actions_resp.json()
        print(f"  [OK] GET /api/v1/action-items (Status 200)")
        print(f"       Pending action items:    {actions_data.get('total')}")

        # 3. GET /api/v1/threads/{thread_id}/triage
        session_maker = get_session_maker()
        thread_to_test = None
        async with session_maker() as db:
            first_thread = (await db.execute(select(Thread).limit(1))).scalar_one_or_none()
            if first_thread:
                thread_to_test = str(first_thread.id)

        if thread_to_test:
            th_resp = await client.get(f"/api/v1/threads/{thread_to_test}/triage")
            if th_resp.status_code != 200:
                print(f"  [FAIL] GET /api/v1/threads/{thread_to_test}/triage returned status {th_resp.status_code}")
                return False
            th_data = th_resp.json()
            print(f"  [OK] GET /api/v1/threads/{thread_to_test}/triage (Status 200)")
            print(f"       Subject:                 {th_data.get('subject')}")
            print(f"       Max urgency:             {th_data.get('max_urgency')}")
            print(f"       Executive summary:       {bool(th_data.get('executive_summary'))}")
        else:
            # Test 404 behavior with dummy UUID
            dummy_resp = await client.get("/api/v1/threads/00000000-0000-0000-0000-000000000000/triage")
            assert dummy_resp.status_code == 404
            print("  [OK] GET /api/v1/threads/{id}/triage properly returns 404 for unknown threads.")

    return True


async def step_4_openapi_schema():
    print("\n[4/4] Verifying OpenAPI documentation & route registration...")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi.json")
        if resp.status_code != 200:
            print(f"  [FAIL] Failed to load OpenAPI schema: {resp.status_code}")
            return False
        schema = resp.json()
        paths = schema.get("paths", {})
        required_paths = [
            "/api/v1/digest/today",
            "/api/v1/action-items",
            "/api/v1/threads/{thread_id}/triage",
        ]
        missing = [p for p in required_paths if p not in paths]
        if missing:
            print(f"  [FAIL] Missing required routes in OpenAPI schema: {missing}")
            return False

        print("  [OK] All Phase 4 endpoints registered in OpenAPI:")
        for p in required_paths:
            print(f"       - {p} [{', '.join(paths[p].keys()).upper()}]")

    return True


async def main():
    banner()
    ok1 = await step_1_guardrails()
    ok2 = await step_2_database_views_and_indexes()
    ok3 = await step_3_rest_endpoints()
    ok4 = await step_4_openapi_schema()

    engine = get_engine()
    await engine.dispose()

    if ok1 and ok2 and ok3 and ok4:
        print("\n" + "=" * 75)
        print("  >>> ALL PHASE 4 VERIFICATION CHECKS PASSED SUCCESSFULLY! <<<")
        print("=" * 75 + "\n")
        sys.exit(0)
    else:
        print("\n" + "=" * 75)
        print("  >>> VERIFICATION FAILED - SEE ERRORS ABOVE <<<")
        print("=" * 75 + "\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
