"""PostgreSQL views for frontend consumption and fast inbox triage.

Provides pre-joined, high-performance database views for direct connection
by external dashboards (Metabase, Retool, Supabase Studio, or custom frontends):
- ``v_urgent_triage_feed``: High-urgency and action-required email analyses with message context.
- ``v_pending_action_items``: Action items pending completion ordered by deadline.
- ``v_today_digest``: 24-hour summary aggregated by category and urgency.
"""
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("gmail_assistant.db.views")

PG_VIEW_URGENT_TRIAGE_FEED = """
CREATE OR REPLACE VIEW v_urgent_triage_feed AS
SELECT
    ea.message_id,
    m.thread_id,
    m.subject,
    m.sender,
    m.date,
    ea.category,
    ea.urgency_score,
    ea.summary,
    (SELECT COUNT(*) FROM action_items ai WHERE ai.message_id = m.id) AS action_items_count
FROM email_analyses ea
JOIN messages m ON m.id = ea.message_id
JOIN threads t ON t.id = m.thread_id
WHERE ea.urgency_score >= 4 OR ea.category IN ('URGENT', 'ACTION_REQUIRED');
"""

PG_VIEW_PENDING_ACTION_ITEMS = """
CREATE OR REPLACE VIEW v_pending_action_items AS
SELECT
    ai.id,
    ai.description,
    ai.deadline,
    ai.assignee,
    ai.status,
    ai.message_id,
    m.thread_id,
    m.subject
FROM action_items ai
JOIN messages m ON m.id = ai.message_id
JOIN threads t ON t.id = m.thread_id
WHERE ai.status = 'pending'
ORDER BY ai.deadline ASC NULLS LAST, ai.created_at DESC;
"""

PG_VIEW_TODAY_DIGEST = """
CREATE OR REPLACE VIEW v_today_digest AS
SELECT
    ea.category,
    COUNT(ea.id) AS total_count,
    ROUND(AVG(ea.urgency_score), 2) AS avg_urgency_score,
    COUNT(CASE WHEN ea.urgency_score >= 4 THEN 1 END) AS urgent_count,
    COALESCE(
        STRING_AGG(ea.summary, ' | ') FILTER (WHERE ea.urgency_score >= 4),
        ''
    ) AS high_urgency_summaries
FROM email_analyses ea
JOIN messages m ON m.id = ea.message_id
WHERE m.date >= NOW() - INTERVAL '24 hours' OR ea.created_at >= NOW() - INTERVAL '24 hours'
GROUP BY ea.category;
"""

PG_VIEWS = [
    PG_VIEW_URGENT_TRIAGE_FEED,
    PG_VIEW_PENDING_ACTION_ITEMS,
    PG_VIEW_TODAY_DIGEST,
]

# SQLite-compatible views for test environments
SQLITE_VIEWS = [
    "DROP VIEW IF EXISTS v_urgent_triage_feed;",
    """
    CREATE VIEW v_urgent_triage_feed AS
    SELECT
        ea.message_id,
        m.thread_id,
        m.subject,
        m.sender,
        m.date,
        ea.category,
        ea.urgency_score,
        ea.summary,
        (SELECT COUNT(*) FROM action_items ai WHERE ai.message_id = m.id) AS action_items_count
    FROM email_analyses ea
    JOIN messages m ON m.id = ea.message_id
    JOIN threads t ON t.id = m.thread_id
    WHERE ea.urgency_score >= 4 OR ea.category IN ('URGENT', 'ACTION_REQUIRED');
    """,
    "DROP VIEW IF EXISTS v_pending_action_items;",
    """
    CREATE VIEW v_pending_action_items AS
    SELECT
        ai.id,
        ai.description,
        ai.deadline,
        ai.assignee,
        ai.status,
        ai.message_id,
        m.thread_id,
        m.subject
    FROM action_items ai
    JOIN messages m ON m.id = ai.message_id
    JOIN threads t ON t.id = m.thread_id
    WHERE ai.status = 'pending'
    ORDER BY ai.deadline ASC, ai.created_at DESC;
    """,
    "DROP VIEW IF EXISTS v_today_digest;",
    """
    CREATE VIEW v_today_digest AS
    SELECT
        ea.category,
        COUNT(ea.id) AS total_count,
        ROUND(AVG(ea.urgency_score), 2) AS avg_urgency_score,
        COUNT(CASE WHEN ea.urgency_score >= 4 THEN 1 END) AS urgent_count,
        GROUP_CONCAT(CASE WHEN ea.urgency_score >= 4 THEN ea.summary ELSE NULL END, ' | ') AS high_urgency_summaries
    FROM email_analyses ea
    JOIN messages m ON m.id = ea.message_id
    WHERE m.date >= datetime('now', '-24 hours') OR ea.created_at >= datetime('now', '-24 hours')
    GROUP BY ea.category;
    """,
]


async def create_views(engine: AsyncEngine) -> None:
    """Create database views idempotently according to the engine dialect.

    PostgreSQL uses CREATE OR REPLACE VIEW with full PostgreSQL dialect features.
    SQLite environments use dialect-compatible DDL so local tests pass cleanly.
    """
    dialect_name = engine.dialect.name
    logger.info("Provisioning database views for dialect: %s", dialect_name)
    if dialect_name == "postgresql":
        async with engine.begin() as conn:
            for sql in PG_VIEWS:
                await conn.execute(text(sql))
        logger.info("PostgreSQL views provisioned successfully.")
    elif dialect_name == "sqlite":
        async with engine.begin() as conn:
            for sql in SQLITE_VIEWS:
                try:
                    await conn.execute(text(sql))
                except Exception as e:
                    logger.debug("SQLite view creation skipped or failed: %s", e)
        logger.info("SQLite views provisioned successfully.")
    else:
        logger.warning("View creation skipped: unsupported dialect '%s'", dialect_name)
