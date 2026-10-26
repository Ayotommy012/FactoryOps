"""FactoryOps FastAPI application entry point."""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.providers import router as providers_router
from app.api.simulations import router as simulations_router
from app.api.usl import router as usl_router
from app.config import settings
from app.database import check_db_connection
from app.services.auto_driver import auto_driver

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("factoryops")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages application lifecycle including background simulation driver."""
    auto_driver.start()
    yield
    await auto_driver.stop()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simulations_router)
app.include_router(providers_router)
app.include_router(usl_router)

FRONTEND_DIST_DIR = Path(__file__).resolve().parent / "static" / "dist"
CONSOLE_HTML_PATH = Path(__file__).resolve().parent / "static" / "console.html"


if FRONTEND_DIST_DIR.exists() and (FRONTEND_DIST_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST_DIR / "assets"), name="assets")


@app.get("/console", response_class=HTMLResponse, tags=["Console"])
@app.get("/", response_class=HTMLResponse, tags=["Console"])
def operator_console():
    """Serves the modern React operator console, falling back to static HTML if not built."""
    if FRONTEND_DIST_DIR.exists() and (FRONTEND_DIST_DIR / "index.html").exists():
        return HTMLResponse(content=(FRONTEND_DIST_DIR / "index.html").read_text(encoding="utf-8"))
    if CONSOLE_HTML_PATH.exists():
        return HTMLResponse(content=CONSOLE_HTML_PATH.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Console template not found.")


@app.get("/health", tags=["Health"])
def health_check():
    """Returns application status."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "environment": settings.app_env,
    }


@app.get("/health/db", tags=["Health"])
def health_db_check():
    """Executes SELECT 1 against the PostgreSQL database to verify connectivity.

    Returns:
        200 OK with connection status if successful.

    Raises:
        HTTPException: 503 Service Unavailable if database is unreachable.
        Credentials and internal exception details are never exposed.
    """
    try:
        check_db_connection()
        return {
            "status": "healthy",
            "database": "connected",
        }
    except Exception:
        # Log internally without leaking sensitive details to the client response
        logger.exception("Database connectivity check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection failed",
        )
