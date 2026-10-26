"""Central API router aggregating auth, gmail, and webhook sub-routers."""
from fastapi import APIRouter
from app.api.auth import router as auth_router
from app.api.gmail import router as gmail_router
from app.api.webhook import router as webhook_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(gmail_router)
api_router.include_router(webhook_router)
