"""
app/main.py

FastAPI application factory and lifecycle management.

This file:
  1. Creates the FastAPI app instance
  2. Registers startup/shutdown lifecycle hooks
  3. Adds middleware (CORS, logging, instance-ID header)
  4. Mounts all routers under /api/v1/
  5. Provides a health check endpoint

Every feature is in its own router (app/api/v1/).
Adding a new feature = creating a new router file + one line here.
"""
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.db.mongodb import close_mongo_connection, create_mongo_indexes
from app.db.redis_client import close_redis
from app.services.kafka_producer import stop_producer

# Routers — Phase 1
from app.api.v1 import auth, notes, users, trash, admin

settings = get_settings()
logger = logging.getLogger(__name__)


# ── Showing logs ─────────────────────────────────────────────────
# logging.basicConfig(
#     level=logging.DEBUG if settings.debug else logging.INFO,
#     format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
# )


# ── Lifespan (startup + shutdown) ────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code before `yield` runs on startup.
    Code after `yield` runs on shutdown.
    This replaces the old @app.on_event("startup") pattern.
    """
    logger.info("Starting DevPad API [instance=%s]", settings.instance_id)

    # Create MongoDB indexes (idempotent — safe on every restart)
    await create_mongo_indexes()
    logger.info("MongoDB indexes ensured.")

    yield  # ← application runs here

    logger.info("Shutting down DevPad API [instance=%s]", settings.instance_id)
    await stop_producer()
    await close_redis()
    await close_mongo_connection()


# ── App instance ─────────────────────────────────────────────────

app = FastAPI(
    title="DevPad API",
    description="Production-grade collaborative notes API",
    version="1.0.0",
    docs_url="/docs" if settings.is_development else None,   # hide Swagger in prod
    redoc_url="/redoc" if settings.is_development else None,
    lifespan=lifespan,
)


# ── Middleware ───────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],   # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_instance_header(request: Request, call_next):
    """
    Stamps every response with X-Instance-ID so Nginx round-robin
    can be verified by the super admin (and in Phase 5 acceptance tests).
    """
    response = await call_next(request)
    response.headers["X-Instance-ID"] = settings.instance_id
    return response


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """
    Logs every incoming request with a correlation ID.
    Useful for tracing a single request across log lines.
    """
    req_id = str(uuid.uuid4())[:8]
    logger.info("[%s] %s %s", req_id, request.method, request.url.path)
    response = await call_next(request)
    logger.info("[%s] → %s", req_id, response.status_code)
    return response


# ── Routers ──────────────────────────────────────────────────────

API_PREFIX = "/api/v1"

app.include_router(auth.router,   prefix=API_PREFIX)
app.include_router(notes.router,  prefix=API_PREFIX)
app.include_router(users.router,  prefix=API_PREFIX)
app.include_router(trash.router,  prefix=API_PREFIX)
app.include_router(admin.router,  prefix=API_PREFIX)


# ── Health check ─────────────────────────────────────────────────

@app.get("/health", tags=["health"])
async def health_check():
    """
    Used by Nginx upstream health checks and the GitHub Actions smoke test.
    Returns 200 as long as the process is alive.
    Instance ID lets you verify which container responded.
    """
    return {
        "status": "ok",
        "instance": settings.instance_id,
        "env": settings.app_env,
    }
