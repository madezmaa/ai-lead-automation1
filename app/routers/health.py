"""Health-check endpoint (database + Ollama reachability)."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db

router = APIRouter(tags=["health"])

SessionDep = Annotated[Session, Depends(get_db)]


@router.get("/health", summary="Liveness and dependency health")
def health(request: Request, db: SessionDep) -> JSONResponse:
    settings: Settings = request.app.state.settings

    database = "up"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database = "down"

    ollama = {"enabled": settings.ollama_enabled}
    if settings.ollama_enabled:
        try:
            response = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=1.5)
            ollama["reachable"] = response.status_code == 200
            if ollama["reachable"]:
                models = [item.get("name") for item in response.json().get("models", [])]
                ollama["models"] = models
                ollama["model_in_use"] = settings.ollama_model in models
        except httpx.HTTPError:
            ollama["reachable"] = False
            ollama["error"] = "connection failed"

    ok = database == "up"
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "database": database, "ollama": ollama},
    )
