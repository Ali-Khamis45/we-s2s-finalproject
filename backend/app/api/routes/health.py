"""Health and readiness."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app import __version__
from app.core.config import settings
from app.schemas.acoustic import SCHEMA_VERSION, json_schema
from app.services.orchestrator import orchestrator

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, Any]:
    """Liveness. Deliberately cheap — no model is touched."""
    return {"status": "ok", "app": settings.app_name, "version": __version__}


@router.get("/api/status")
async def status() -> dict[str, Any]:
    """What the system can actually do right now.

    The UI reads this on load to decide whether to offer the live coach, and it
    is the fastest way to see which half of the stack is up.
    """
    return await orchestrator.health()


@router.get("/api/schema/acoustic")
async def acoustic_schema() -> dict[str, Any]:
    """The S1/M5 contract, served as JSON Schema.

    Track M validates the classifier's output against this, so the two tracks
    cannot drift apart without a test failing.
    """
    return {"version": SCHEMA_VERSION, "schema": json_schema()}
