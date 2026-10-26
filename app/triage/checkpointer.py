"""PostgreSQL short-term memory checkpointer lifecycle for the triage agent.

Owns a single ``AsyncPostgresSaver`` (``langgraph-checkpoint-postgres``) via an
``AsyncExitStack`` so the connection is opened lazily and torn down cleanly on
shutdown. The checkpointer is keyed per Gmail thread so successive messages in
a conversation resume with prior triage context.

Boot-resilience contract (NFR-2): when triage is disabled, no GCP project is
resolvable, or the ``DATABASE_URL`` is not PostgreSQL (e.g. SQLite tests), the
lifecycle functions are no-ops that return ``None`` and log a warning instead
of raising — OAuth, ingestion, and ``/health`` keep working.
"""
import logging
from contextlib import AsyncExitStack
from typing import Optional

from app.core.config import get_settings
from app.db.dsn import to_psycopg_dsn

logger = logging.getLogger("gmail_assistant.triage.checkpointer")

_saver: Optional[object] = None
_exit_stack: Optional[AsyncExitStack] = None
_setup_done: bool = False


def memory_thread_id(gmail_thread_id: str) -> str:
    """Return the LangGraph ``thread_id`` for a Gmail conversation thread.

    Convention: ``gmail-thread:<gmail_thread_id>``. Phase 4 and re-runs reuse
    the same key so checkpointed triage state resumes across messages and runs.
    """
    return f"gmail-thread:{gmail_thread_id}"


async def init_checkpointer() -> Optional[object]:
    """Lazily open the ``AsyncPostgresSaver`` and run ``saver.setup()``.

    Idempotent: safe to call multiple times. Returns ``None`` (and logs once)
    when triage is disabled, no project resolves, or the URL is not PostgreSQL.
    Never raises so application boot is unaffected.
    """
    global _saver, _exit_stack, _setup_done

    if _saver is not None:
        return _saver

    settings = get_settings()

    if not settings.triage_ready:
        logger.warning(
            "Triage checkpointer disabled (TRIAGE_ENABLED=%s or GCP project unresolved). "
            "Agent memory will use a no-op / in-memory fallback.",
            settings.TRIAGE_ENABLED,
        )
        return None

    if not settings.database_is_postgres:
        logger.warning(
            "Triage checkpointer disabled: DATABASE_URL is not PostgreSQL "
            "(required for AsyncPostgresSaver). Agent memory unavailable."
        )
        return None

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except Exception as e:  # pragma: no cover - import guard
        logger.error("Could not import AsyncPostgresSaver: %s", e)
        return None

    try:
        dsn = to_psycopg_dsn(settings.DATABASE_URL)
    except ValueError as e:
        logger.warning("Skipping checkpointer: %s", e)
        return None

    _exit_stack = AsyncExitStack()
    try:
        saver = await _exit_stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(dsn)
        )
    except Exception as e:
        logger.warning("Failed to open AsyncPostgresSaver: %s", e)
        await _exit_stack.aclose()
        _exit_stack = None
        return None

    _saver = saver

    if not _setup_done:
        try:
            await saver.setup()
            _setup_done = True
            logger.info("PostgresSaver.setup() complete; checkpoint tables ready.")
        except Exception as e:
            logger.warning("PostgresSaver.setup() failed (continuing): %s", e)

    return _saver


def get_checkpointer() -> Optional[object]:
    """Return the currently-opened saver, or ``None`` if not initialised.

    Tests substitute an ``InMemorySaver`` by calling ``set_checkpointer``.
    """
    return _saver


def set_checkpointer(saver: Optional[object]) -> None:
    """Inject a checkpointer (used by tests with ``InMemorySaver``)."""
    global _saver, _setup_done
    _saver = saver
    _setup_done = True


def clear_checkpointer() -> None:
    """Reset module-level state (used by tests between runs)."""
    global _saver, _exit_stack, _setup_done
    _saver = None
    _exit_stack = None
    _setup_done = False


async def close_checkpointer() -> None:
    """Close the saver connection. Disposes the AsyncExitStack on shutdown."""
    global _saver, _exit_stack, _setup_done
    if _exit_stack is not None:
        try:
            await _exit_stack.aclose()
        except Exception as e:
            logger.warning("Error closing checkpointer: %s", e)
    _saver = None
    _exit_stack = None
    _setup_done = False
