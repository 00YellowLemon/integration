"""FastAPI application entrypoint with lifespan context, middleware, and routers."""
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.gmail import router as gmail_router
from app.api.webhook import router as webhook_router
from app.api.triage import router as triage_router
from app.api.digest import router as digest_router
from app.core.config import get_settings

from app.db.session import get_engine, init_db
from app.triage.checkpointer import close_checkpointer, init_checkpointer


logger = logging.getLogger("gmail_assistant")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context initializing database tables on startup."""
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    # Enforce Constitutional Principle 1: Least Privilege & Read-Only Safety Guardrail
    from app.core.guardrails import verify_read_only_safety
    verify_read_only_safety()

    try:
        await init_db()

        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.warning("Database initialization skipped or encountered error: %s", e)

    try:
        await init_checkpointer()
    except Exception as e:
        logger.warning("Checkpointer initialization skipped or encountered error: %s", e)

    yield

    logger.info("Shutting down Gmail AI Assistant application...")
    await close_checkpointer()
    engine = get_engine()
    await engine.dispose()


app = FastAPI(
    title="Gmail AI Assistant",
    description="Intelligent, secure, read-only Gmail assistant with Google OAuth 2.0 and Supabase storage.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount application routers
app.include_router(auth_router)
app.include_router(gmail_router)
app.include_router(webhook_router)
app.include_router(triage_router)
app.include_router(digest_router)




@app.get("/health", summary="Health check endpoint", tags=["System"])
async def health_check():
    """Health check endpoint verifying application and database connectivity."""
    db_status = "connected"
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Health check database connection error: %s", e)
        db_status = "disconnected"

    return {
        "status": "ok",
        "database": db_status,
    }


@app.get("/", summary="Root endpoint", tags=["System"])
async def root():
    """Root endpoint welcoming API consumers and pointing to documentation."""
    return {
        "name": "Gmail AI Assistant API",
        "version": "0.1.0",
        "docs_url": "/docs",
        "status_url": "/api/v1/gmail/status",
        "login_url": "/auth/login",
    }
