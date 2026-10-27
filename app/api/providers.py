"""API Router for LLM Providers including Meterless Runtime."""

import logging
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.providers.meterless import (
    DEFAULT_METERLESS_URL,
    MeterlessClient,
    ProviderStatus,
    auto_detect_meterless_runtime,
    get_rate_limit_state,
    normalize_meterless_url,
    parse_connect_link,
)

logger = logging.getLogger("factoryops.api.providers")

router = APIRouter(prefix="/providers", tags=["Providers"])


class ConnectLinkRequest(BaseModel):
    connect_link: str = Field(..., json_schema_extra={"example": "http://127.0.0.1:8088/connect#k=token123"})


class TestChatRequest(BaseModel):
    prompt: str = Field(default="Assess roaster health status.", json_schema_extra={"example": "Evaluate plan A"})
    model: str = Field(default="meterless/auto", json_schema_extra={"example": "meterless/auto"})


@router.get("/meterless/status")
def get_meterless_status(
    base_url: str = Query(default=DEFAULT_METERLESS_URL),
    token: Optional[str] = Query(default=None),
):
    """Checks Meterless Runtime connectivity, authorization, and discovered routing models."""
    try:
        norm_url = normalize_meterless_url(base_url)
        client = MeterlessClient(base_url=norm_url, token=token)
        status = client.get_status_and_models()
        return {
            "provider_id": "meterless",
            "display_name": "Meterless Runtime",
            "base_url": status.base_url,
            "status": status.status,
            "message": status.message,
            "available_models": status.available_models,
            "tested_at": status.tested_at,
            "cloud_jobs_allowed": os.getenv("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "false").strip().lower() in ("true", "1", "yes"),
            # Shared back-pressure state (cooldown after upstream 429s)
            "rate_limit": get_rate_limit_state(status.base_url),
        }
    except Exception as e:
        logger.warning(f"Meterless status check failed: {e}")
        return {
            "provider_id": "meterless",
            "display_name": "Meterless Runtime",
            "base_url": base_url,
            "status": "offline",
            "message": str(e),
            "available_models": [],
        }


@router.post("/meterless/detect")
def auto_detect_runtime(token: Optional[str] = Query(default=None)):
    """Automatically scans standard candidate ports (8088, 8090, 8787, 11434) for Meterless Runtime."""
    status = auto_detect_meterless_runtime(token=token)
    return {
        "provider_id": "meterless",
        "display_name": "Meterless Runtime",
        "base_url": status.base_url,
        "status": status.status,
        "message": status.message,
        "available_models": status.available_models,
        "tested_at": status.tested_at,
    }


@router.post("/meterless/connect-link")
def parse_and_connect(payload: ConnectLinkRequest):
    """Parses a connect link 'http://<host>:<port>/connect#k=<key>' safely without leaking credentials."""
    try:
        base_url, token = parse_connect_link(payload.connect_link)
        client = MeterlessClient(base_url=base_url, token=token)
        status = client.get_status_and_models()
        return {
            "provider_id": "meterless",
            "base_url": base_url,
            "status": status.status,
            "message": status.message,
            "available_models": status.available_models,
            "token_configured": bool(token),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
