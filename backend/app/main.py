"""
backend/app/main.py

Purpose
-------
FastAPI application entry point for CodeXray.

Responsibility
--------------
- Build the ASGI app, wire logging from config, mount the API router under `/api`.
- Initialise the SQLite schema on startup so a fresh checkout works immediately.
- Expose a `/health` probe and redirect `/` to the interactive docs.
- Enable permissive CORS for `localhost` only (the React UI in Sprint 5) — the
  system is local-only by design, nothing is exposed off-box.

Run:  uvicorn backend.app.main:app --reload --port 8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from backend.app.api.routes import router
from backend.app.config.settings import get_settings
from backend.app.models.database import init_db

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.logging_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="CodeXray — Local Project Intelligence AI",
    version="0.1.0-sprint1",
    description="Deterministic Java+SQL code intelligence, indexed locally. LLM reasoning arrives in Sprint 3.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.on_event("startup")
def _startup() -> None:
    path = init_db()
    logging.getLogger("codexray").info("index database ready at %s", path)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version, "local_only": settings.security.local_only}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")
