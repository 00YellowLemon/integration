"""Connection-string helpers for the LangGraph PostgresSaver checkpointer.

The SQLAlchemy engine speaks the ``postgresql+asyncpg://`` dialect, while the
``psycopg``-backed ``AsyncPostgresSaver`` expects a plain ``postgresql://`` DSN.
This module is the single place that converts between the two so the
``+asyncpg`` driver is never accidentally passed to ``psycopg``.
"""
from urllib.parse import urlsplit


def to_psycopg_dsn(database_url: str) -> str:
    """Convert a SQLAlchemy PostgreSQL URL into a plain ``psycopg`` DSN.

    - Strips ``+asyncpg`` / ``+psycopg`` driver suffixes.
    - Normalises ``postgres://`` to ``postgresql://``.
    - Rejects non-PostgreSQL URLs with a clear error; the checkpointer is never
      constructed for SQLite tests.

    Args:
        database_url: SQLAlchemy-style DATABASE_URL (e.g.
            ``postgresql+asyncpg://user:pass@host:5432/db``).

    Returns:
        A plain ``postgresql://`` DSN suitable for ``AsyncPostgresSaver``.

    Raises:
        ValueError: If ``database_url`` is not a PostgreSQL URL.
    """
    if not database_url:
        raise ValueError("DATABASE_URL is empty; cannot derive a psycopg DSN")

    url_lower = database_url.lower()
    if not (url_lower.startswith("postgresql://") or url_lower.startswith("postgres://")):
        raise ValueError(
            "AsyncPostgresSaver requires a PostgreSQL DSN; "
            f"got non-PostgreSQL URL: {database_url.split('://')[0]}://..."
        )

    # Normalise the scheme first: postgres:// -> postgresql://
    if database_url.lower().startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]

    # Strip the SQLAlchemy driver suffix (+asyncpg / +psycopg / +psycopg2 ...)
    if "+" in database_url:
        # e.g. postgresql+asyncpg://... -> postgresql://...
        scheme, rest = database_url.split("://", 1)
        base_scheme = scheme.split("+", 1)[0]
        database_url = f"{base_scheme}://{rest}"

    # Sanity-check the result parses cleanly.
    parts = urlsplit(database_url)
    if parts.scheme != "postgresql" or not parts.hostname:
        raise ValueError(f"Refusing to use malformed psycopg DSN: {database_url!r}")

    return database_url
