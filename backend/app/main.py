from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import Base, engine
from .routers import auth as auth_router
from .routers import questions as questions_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 0 creates tables directly. Alembic is in requirements for when the
    # schema starts changing under real data (phase 2 onwards).
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="TCF Practice Center API",
    version="0.1.0",
    description="Phase 0: question bank for Expression Orale tasks 2 and 3, plus accounts.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(questions_router.router)
app.include_router(auth_router.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "env": settings.env, "google_auth": settings.google_enabled}
