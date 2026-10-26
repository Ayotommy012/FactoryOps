"""Tests for Meterless Runtime Rate-Limiting, Retries, and Explanation Caching.

Verifies:
1. HTTP 429 Too Many Requests triggers rate-limit aware backoff and retries.
2. HTTP 502 / 503 wrapping upstream 429 errors is detected and retried.
3. Retry-After header is honored when present.
4. Retry budget / max attempt exhaustion raises MeterlessRateLimitError.
5. Recovery explanation gracefully falls back to deterministic text when rate-limited.
6. In-memory explanation caching prevents burst re-querying of Meterless.
7. Polling get_job retries transient rate limits without resubmitting inference.
"""

from __future__ import annotations

import io
import json
import time
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from app.providers.meterless import (
    MeterlessClient,
    MeterlessRateLimitError,
)
from app.services.recovery import (
    DisruptionEvidence,
    ImpactAnalysis,
    RecoveryPlan,
    _EXPLANATION_CACHE,
    explain_recovery_plan,
)


def _make_http_error(code: int, msg: str, body: bytes, headers: dict | None = None) -> urllib.error.HTTPError:
    fp = io.BytesIO(body)
    hdrs = MagicMock()
    hdrs.get = lambda k, d=None: (headers or {}).get(k, d)
    err = urllib.error.HTTPError(
        url="http://127.0.0.1:8088/v1/chat/completions",
        code=code,
        msg=msg,
        hdrs=hdrs,
        fp=fp,
    )
    err.headers = hdrs
    return err


def test_successful_retry_after_429(monkeypatch):
    """Client detects HTTP 429 and retries with backoff, succeeding on attempt 2."""
    monkeypatch.setenv("METERLESS_MAX_RETRIES", "2")
    monkeypatch.setenv("METERLESS_RETRY_BUDGET_SEC", "5.0")

    err_429 = _make_http_error(429, "Too Many Requests", b'{"error":{"message":"Rate limit exceeded"}}')
    success_resp = MagicMock()
    success_resp.read.return_value = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "Recovery explanation from Meterless."}}]
    }).encode("utf-8")
    success_resp.__enter__.return_value = success_resp
    success_resp.__exit__.return_value = None

    client = MeterlessClient(base_url="http://127.0.0.1:8088/v1", token="test-token-valid")

    with patch("urllib.request.urlopen", side_effect=[err_429, success_resp]) as mock_open:
        with patch("time.sleep", return_value=None):
            result = client.chat_completion([{"role": "user", "content": "Hello"}])

    assert result == "Recovery explanation from Meterless."
    assert mock_open.call_count == 2


def test_502_wrapping_429_retried_successfully(monkeypatch):
    """Client detects HTTP 502 with wrapped 429 in response body and retries."""
    monkeypatch.setenv("METERLESS_MAX_RETRIES", "2")
    monkeypatch.setenv("METERLESS_RETRY_BUDGET_SEC", "5.0")

    body_502 = b'{"error":{"message":"429: {\'detail\': \'Too Many Requests\'}"}}'
    err_502 = _make_http_error(502, "Bad Gateway", body_502)

    success_resp = MagicMock()
    success_resp.read.return_value = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "Success after upstream 429 cleared."}}]
    }).encode("utf-8")
    success_resp.__enter__.return_value = success_resp
    success_resp.__exit__.return_value = None

    client = MeterlessClient(base_url="http://127.0.0.1:8088/v1", token="test-token-valid")

    with patch("urllib.request.urlopen", side_effect=[err_502, success_resp]) as mock_open:
        with patch("time.sleep", return_value=None):
            result = client.chat_completion([{"role": "user", "content": "Hello"}])

    assert result == "Success after upstream 429 cleared."
    assert mock_open.call_count == 2


def test_retry_after_header_parsing(monkeypatch):
    """Client parses and respects Retry-After header on 429."""
    monkeypatch.setenv("METERLESS_MAX_RETRIES", "2")
    monkeypatch.setenv("METERLESS_RETRY_BUDGET_SEC", "10.0")

    err_429 = _make_http_error(
        429, "Too Many Requests", b'{"error":"slow down"}', headers={"Retry-After": "2"}
    )
    success_resp = MagicMock()
    success_resp.read.return_value = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "Success after wait."}}]
    }).encode("utf-8")
    success_resp.__enter__.return_value = success_resp
    success_resp.__exit__.return_value = None

    client = MeterlessClient(base_url="http://127.0.0.1:8088/v1", token="test-token-valid")

    slept = []
    with patch("urllib.request.urlopen", side_effect=[err_429, success_resp]):
        with patch("time.sleep", side_effect=lambda s: slept.append(s)):
            result = client.chat_completion([{"role": "user", "content": "Hello"}])

    assert result == "Success after wait."
    assert len(slept) == 1
    assert slept[0] == 2.0


def test_retry_budget_exhaustion_raises_rate_limit_error(monkeypatch):
    """Client raises MeterlessRateLimitError after retries exhausted."""
    monkeypatch.setenv("METERLESS_MAX_RETRIES", "2")
    monkeypatch.setenv("METERLESS_RETRY_BUDGET_SEC", "5.0")

    err_429 = _make_http_error(429, "Too Many Requests", b'{"error":"exceeded limit"}')
    client = MeterlessClient(base_url="http://127.0.0.1:8088/v1", token="test-token-valid")

    with patch("urllib.request.urlopen", side_effect=err_429) as mock_open:
        with patch("time.sleep", return_value=None):
            with pytest.raises(MeterlessRateLimitError) as exc_info:
                client.chat_completion([{"role": "user", "content": "Hello"}])

    assert "exceeded" in str(exc_info.value).lower()
    assert mock_open.call_count == 3  # Initial + 2 retries


def test_meterless_rate_limit_graceful_fallback_in_explain_recovery_plan(monkeypatch):
    """When Meterless raises MeterlessRateLimitError, explain_recovery_plan returns deterministic fallback."""
    monkeypatch.setenv("AI_PROVIDER", "meterless")

    evidence = [
        DisruptionEvidence(
            metric="bearing_temperature_c",
            observed_value=142.5,
            threshold_value=120.0,
            unit="°C",
            severity="high",
            message="Temperature elevated",
        )
    ]
    impact = ImpactAnalysis(
        scenario_id="scn_test_rate_limit_01",
        disrupted_machine_id="machine:plant:ROAST-01",
        affected_batch_id="production_batch:mes:BATCH-101",
        affected_order_id="customer_order:erp:ORD-9402",
        affected_customer="Artisan Chocolatier BV",
        order_deadline="2026-10-22T14:00:00+00:00",
        unintervened_completion="2026-10-22T18:00:00+00:00",
        delay_hours=4.0,
        sla_breached=True,
        potential_penalty_usd=350.0,
        remaining_quantity_kg=2500.0,
        evidence=evidence,
        severity="high",
        disruption_type="roaster_bearing_overheat",
    )
    plan = RecoveryPlan(
        plan_id="plan_a_fast_maint",
        title="Immediate Bearing Replacement",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=["Safety interlock cleared"],
        estimated_downtime_hours=0.75,
        projected_completion="2026-10-22T11:45:00+00:00",
        meets_deadline=True,
        buffer_hours=2.25,
        total_cost_usd=120.0,
        cost_breakdown={"parts": 120.0},
        action_steps=["Replace bearing"],
    )

    with patch("app.providers.meterless.resolve_meterless_token", return_value="dummy-valid-token"):
        with patch.object(
            MeterlessClient,
            "chat_completion",
            side_effect=MeterlessRateLimitError("Rate limit exceeded", attempts=3),
        ):
            explanation = explain_recovery_plan(impact, plan, enabled=True, provider="meterless")

    assert "Operational Rationale" in explanation
    assert "Immediate Bearing Replacement" in explanation
    assert "Deterministic cost-benefit optimization" in explanation.lower() or "ranked #1" in explanation.lower()


def test_explanation_caching_prevents_duplicate_calls(monkeypatch):
    """Repeated calls with identical scenario/plan return cached result without re-querying Meterless."""
    _EXPLANATION_CACHE.clear()
    monkeypatch.setenv("AI_PROVIDER", "meterless")

    evidence = [
        DisruptionEvidence(
            metric="bearing_temperature_c",
            observed_value=142.5,
            threshold_value=120.0,
            unit="°C",
            severity="high",
            message="Temperature elevated",
        )
    ]
    impact = ImpactAnalysis(
        scenario_id="scn_test_cache_01",
        disrupted_machine_id="machine:plant:ROAST-01",
        affected_batch_id="production_batch:mes:BATCH-101",
        affected_order_id="customer_order:erp:ORD-9402",
        affected_customer="Artisan Chocolatier BV",
        order_deadline="2026-10-22T14:00:00+00:00",
        unintervened_completion="2026-10-22T18:00:00+00:00",
        delay_hours=4.0,
        sla_breached=True,
        potential_penalty_usd=350.0,
        remaining_quantity_kg=2500.0,
        evidence=evidence,
        severity="high",
        disruption_type="roaster_bearing_overheat",
    )
    plan = RecoveryPlan(
        plan_id="plan_a_fast_maint",
        title="Immediate Bearing Replacement",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=["Safety interlock cleared"],
        estimated_downtime_hours=0.75,
        projected_completion="2026-10-22T11:45:00+00:00",
        meets_deadline=True,
        buffer_hours=2.25,
        total_cost_usd=120.0,
        cost_breakdown={"parts": 120.0},
        action_steps=["Replace bearing"],
    )

    call_count = 0

    def mock_chat(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return "This is a detailed Meterless AI explanation for the plan."

    with patch("app.providers.meterless.resolve_meterless_token", return_value="dummy-valid-token"):
        with patch.object(MeterlessClient, "chat_completion", side_effect=mock_chat):
            res1 = explain_recovery_plan(impact, plan, enabled=True, provider="meterless")
            res2 = explain_recovery_plan(impact, plan, enabled=True, provider="meterless")

    assert res1 == res2
    assert "[Meterless Runtime AI]" in res1
    assert call_count == 1  # Only called once; second call was cached!


def test_get_job_retries_transient_rate_limit(monkeypatch):
    """Polling get_job retries on transient 429 without resubmitting inference."""
    monkeypatch.setenv("METERLESS_MAX_RETRIES", "2")

    err_429 = _make_http_error(429, "Too Many Requests", b'{"error":"Rate limited on get_job"}')
    success_resp = MagicMock()
    success_resp.read.return_value = json.dumps({
        "id": "job_12345",
        "state": "completed",
        "progress": 1.0,
    }).encode("utf-8")
    success_resp.__enter__.return_value = success_resp
    success_resp.__exit__.return_value = None

    client = MeterlessClient(base_url="http://127.0.0.1:8088/v1", token="test-token-valid")

    with patch("urllib.request.urlopen", side_effect=[err_429, success_resp]) as mock_open:
        with patch("time.sleep", return_value=None):
            job = client.get_job("job_12345")

    assert job["id"] == "job_12345"
    assert job["state"] == "completed"
    assert mock_open.call_count == 2
