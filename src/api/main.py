"""
Supply Chain Knowledge Graph — FastAPI application.

Run with:
    uvicorn src.api.main:app --reload
    # or
    python -m src.api.main
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.api.auth import router as auth_router
from src.api.cache import CacheMiddleware
from src.api.limiter import limiter
from src.api.routers import (
    boms_router,
    disruption_router,
    extraction_router,
    parts_router,
    reasoning_router,
    suppliers_router,
)
from src.api.routers.search import router as search_router
from src.api.routers.query import router as query_router
from src.api.schemas import HealthResponse
from src.config import get_settings
from src.graph.neo4j_client import Neo4jClient

settings = get_settings()


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure Neo4j constraints exist. Shutdown: nothing to do."""
    logger.info("Starting up — creating Neo4j constraints if needed…")
    try:
        with Neo4jClient() as client:
            client.create_constraints()
        logger.info("Neo4j constraints OK")
    except Exception as exc:
        logger.error(f"Could not connect to Neo4j on startup: {exc}")
    yield
    logger.info("Shutting down")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Neuro-symbolic supply chain intelligence API. "
        "Extract entities from documents (neural), validate with explicit rules (symbolic), "
        "and query a Neo4j knowledge graph."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── Rate limiting ────────────────────────────────────────────────────────────

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(CacheMiddleware)

# ─── CORS ─────────────────────────────────────────────────────────────────────

# Tighten allow_origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Global error handler ─────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    """Liveness probe — no auth required."""
    try:
        with Neo4jClient() as client:
            client.execute_query("RETURN 1")
        db_status = "connected"
    except Exception:
        db_status = "unavailable"

    from src.api.cache import get_stats as cache_stats
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        database=db_status,
        cache=cache_stats(),
    )


app.include_router(auth_router)          # POST /auth/token
app.include_router(parts_router)
app.include_router(suppliers_router)
app.include_router(boms_router)
app.include_router(reasoning_router)
app.include_router(search_router)
app.include_router(query_router)
app.include_router(disruption_router)
app.include_router(extraction_router)


# ─── Dev entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
        log_level=settings.api_log_level,
    )