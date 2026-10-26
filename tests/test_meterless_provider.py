"""Unit tests for Meterless Runtime Provider."""

import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch
import urllib.error

from app.providers.meterless import (
    DEFAULT_METERLESS_URL,
    MeterlessAuthError,
    MeterlessClient,
    MeterlessValidationError,
    auto_detect_meterless_runtime,
    normalize_meterless_url,
    parse_connect_link,
    resolve_meterless_token,
)


def test_url_normalization_standard():
    assert normalize_meterless_url("http://127.0.0.1:8088") == "http://127.0.0.1:8088/v1"
    assert normalize_meterless_url("127.0.0.1:8088") == "http://127.0.0.1:8088/v1"
    assert normalize_meterless_url("http://127.0.0.1:8088/v1") == "http://127.0.0.1:8088/v1"
    assert normalize_meterless_url("http://127.0.0.1:8088/v1/chat/completions") == "http://127.0.0.1:8088/v1"
    assert normalize_meterless_url("http://127.0.0.1:8088/v1/models") == "http://127.0.0.1:8088/v1"
    assert normalize_meterless_url("http://127.0.0.1:8088/healthz") == "http://127.0.0.1:8088/v1"


def test_url_normalization_no_duplicate_v1():
    # Never produce /v1/v1
    assert normalize_meterless_url("http://127.0.0.1:8088/v1/v1") == "http://127.0.0.1:8088/v1"
    assert normalize_meterless_url("http://localhost:8088/v1/v1/v1") == "http://localhost:8088/v1"
    assert normalize_meterless_url("http://[::1]:8088/v1/v1/chat/completions") == "http://[::1]:8088/v1"


def test_url_normalization_invalid():
    with pytest.raises(MeterlessValidationError):
        normalize_meterless_url("")
    with pytest.raises(MeterlessValidationError):
        normalize_meterless_url("ftp://127.0.0.1:8088")


def test_connect_link_parsing():
    link = "http://127.0.0.1:8088/connect#k=sec_token_991823"
    base_url, token = parse_connect_link(link)
    assert base_url == "http://127.0.0.1:8088/v1"
    assert token == "sec_token_991823"
    assert "connect" not in base_url
    assert "#k=" not in base_url


def test_connect_link_rejects_rt_local():
    link = "http://127.0.0.1:8088/connect#k=rt-local"
    with pytest.raises(MeterlessAuthError, match="Placeholder token 'rt-local' is rejected"):
        parse_connect_link(link)


def test_resolve_token_rejects_rt_local(tmp_path):
    assert resolve_meterless_token("rt-local") is None
    token_file = tmp_path / "proxy-token"
    token_file.write_text("rt-local", encoding="utf-8")
    assert resolve_meterless_token(state_dir=tmp_path) is None


def test_resolve_token_reads_state_dir(tmp_path):
    token_file = tmp_path / "proxy-token"
    token_file.write_text("valid_runtime_token_123", encoding="utf-8")
    token = resolve_meterless_token(state_dir=tmp_path)
    assert token == "valid_runtime_token_123"


def test_headers_contract():
    client = MeterlessClient(token="test_tok", app_id="factoryops")
    headers = client._get_headers(scope="user")
    assert headers["meterless-app"] == "factoryops"
    assert headers["Authorization"] == "Bearer test_tok"
    assert headers["meterless-scope"] == "user"


@patch("urllib.request.urlopen")
def test_status_online(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "data": [
            {"id": "meterless/auto"},
            {"id": "meterless/fast"},
            {"id": "meterless/deep"},
            {"id": "meterless/custom_experimental"},
        ]
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    client = MeterlessClient(token="valid_token")
    status = client.get_status_and_models()
    assert status.status == "online"
    assert "meterless/auto" in status.available_models
    assert "meterless/custom_experimental" in status.available_models


@patch("urllib.request.urlopen")
def test_status_unauthorized(mock_urlopen):
    # HTTP 401 must report 'unauthorized', NOT 'offline'
    err = urllib.error.HTTPError("http://127.0.0.1:8088/v1/models", 401, "Unauthorized", {}, None)
    mock_urlopen.side_effect = err

    client = MeterlessClient(token="invalid_token")
    status = client.get_status_and_models()
    assert status.status == "unauthorized"


@patch("urllib.request.urlopen")
def test_status_offline(mock_urlopen):
    err = urllib.error.URLError("Connection refused")
    mock_urlopen.side_effect = err

    client = MeterlessClient(token="any")
    status = client.get_status_and_models()
    assert status.status == "offline"


@patch("urllib.request.urlopen")
def test_chat_completion_string_content(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "choices": [
            {"message": {"role": "assistant", "content": "Recommendation: Proceed with Plan A."}}
        ]
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    client = MeterlessClient(token="valid_token")
    res = client.chat_completion(
        messages=[{"role": "user", "content": "Evaluate plan A"}],
        model="meterless/auto",
    )
    assert isinstance(res, str)
    assert "Plan A" in res

    # Verify plain-text message string was sent
    call_args = mock_urlopen.call_args
    req = call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["messages"][0]["content"] == "Evaluate plan A"
    assert isinstance(payload["messages"][0]["content"], str)


@patch("app.providers.meterless.MeterlessClient.get_status_and_models")
def test_auto_detect(mock_status):
    from app.providers.meterless import ProviderStatus
    # Simulate first port offline, second port online
    mock_status.side_effect = [
        ProviderStatus(status="offline", base_url="http://127.0.0.1:8088/v1", message="offline"),
        ProviderStatus(status="online", base_url="http://localhost:8088/v1", message="online", available_models=["meterless/auto"]),
    ]

    status = auto_detect_meterless_runtime(ports=[8088], hosts=["127.0.0.1", "localhost"])
    assert status.status == "online"
    assert status.base_url == "http://localhost:8088/v1"


def test_resolve_token_localappdata(tmp_path, monkeypatch):
    monkeypatch.delenv("METERLESS_API_KEY", raising=False)
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    monkeypatch.delenv("METERLESS_TOKEN", raising=False)
    monkeypatch.delenv("METERLESS_STATE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    runtime_dir = tmp_path / "ai.meterless.runtime"
    runtime_dir.mkdir(parents=True)
    token_file = runtime_dir / "proxy-token"
    token_file.write_text("test_localappdata_token_xyz", encoding="utf-8")

    token = resolve_meterless_token()
    assert token == "test_localappdata_token_xyz"


def test_resolve_token_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    runtime_dir = tmp_path / "ai.meterless.runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "proxy-token").write_text("file_token", encoding="utf-8")

    # METERLESS_TOKEN beats file
    monkeypatch.setenv("METERLESS_TOKEN", "token_env_3")
    assert resolve_meterless_token() == "token_env_3"

    # METERLESS_RUNTIME_TOKEN beats METERLESS_TOKEN
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "token_env_2")
    assert resolve_meterless_token() == "token_env_2"

    # METERLESS_API_KEY beats METERLESS_RUNTIME_TOKEN
    monkeypatch.setenv("METERLESS_API_KEY", "token_env_1")
    assert resolve_meterless_token() == "token_env_1"

    # Explicit argument beats all environment variables
    assert resolve_meterless_token(explicit_token="explicit_override") == "explicit_override"


def test_resolve_token_handles_empty_or_whitespace_file(tmp_path, monkeypatch):
    monkeypatch.delenv("METERLESS_API_KEY", raising=False)
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    monkeypatch.delenv("METERLESS_TOKEN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    runtime_dir = tmp_path / "ai.meterless.runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "proxy-token").write_text("   \n\t  ", encoding="utf-8")

    assert resolve_meterless_token() is None


def test_submit_job_safety_gate_disabled_by_default(monkeypatch):
    """Verifies that submit_job rejects cloud jobs when NIGHTSHIFT_ALLOW_CLOUD_JOBS is absent."""
    monkeypatch.delenv("NIGHTSHIFT_ALLOW_CLOUD_JOBS", raising=False)
    client = MeterlessClient(token="test_tok")

    with pytest.raises(Exception) as excinfo:
        client.submit_job(prompt="test prompt", privacy="cloud-ok")
    assert "NIGHTSHIFT_ALLOW_CLOUD_JOBS is disabled" in str(excinfo.value)


@pytest.mark.parametrize("invalid_val", ["0", "false", "no", "FALSE", "disabled", "random_string"])
def test_submit_job_safety_gate_rejects_non_enabling_values(monkeypatch, invalid_val):
    """Verifies that only strictly enabling values permit cloud-job submission."""
    monkeypatch.setenv("NIGHTSHIFT_ALLOW_CLOUD_JOBS", invalid_val)
    client = MeterlessClient(token="test_tok")

    with pytest.raises(Exception) as excinfo:
        client.submit_job(prompt="test prompt", privacy="cloud-ok")
    assert "NIGHTSHIFT_ALLOW_CLOUD_JOBS is disabled" in str(excinfo.value)


@pytest.mark.parametrize("valid_val", ["true", "TRUE", "1", "yes", "YES"])
@patch("urllib.request.urlopen")
def test_submit_job_safety_gate_permits_explicit_authorization(mock_urlopen, monkeypatch, valid_val):
    """Verifies that strictly valid enabling values permit cloud-job submission."""
    monkeypatch.setenv("NIGHTSHIFT_ALLOW_CLOUD_JOBS", valid_val)
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({"id": "job_authorized_001", "status": "queued"}).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    client = MeterlessClient(token="test_tok")
    job = client.submit_job(prompt="test prompt", privacy="cloud-ok")
    assert job["id"] == "job_authorized_001"


@patch("urllib.request.urlopen")
def test_execute_competing_attempts_unexpected_model_not_labeled_nemotron(mock_urlopen, monkeypatch):
    """Verifies that an unexpected model resolution is recorded faithfully and not silently labeled Nemotron."""
    monkeypatch.setenv("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "true")
    client = MeterlessClient(token="test_tok")

    # Mock submit_job, get_job (completed), get_job_attempt (resolved to unexpected model), get_outcome, get_receipt
    client.submit_job = MagicMock(return_value={"id": "job_unexpected_01"})
    client.get_job = MagicMock(return_value={"status": "completed", "winner_seq": 1, "runner_up_seq": 2})
    client.get_job_attempt = MagicMock(return_value={
        "seq": 1,
        "text": json.dumps({"selected_plan_id": "plan_a"}),
        "model": {"requested": "nvidia/nemotron-3-ultra-550b-a55b", "resolved": "meta/llama-3-8b-instruct", "family": "llama"},
        "receipt_id": "rcpt_llama_01",
    })
    client.get_job_outcome = MagicMock(return_value={"status": "accepted", "basis": "consensus"})
    client.get_job_receipt = MagicMock(return_value={"receipt_id": "rcpt_llama_01"})

    result = client.execute_competing_attempts_job(prompt="test prompt")
    assert result["resolved_model"] == "meta/llama-3-8b-instruct"
    assert result["resolved_model"] != "nvidia/nemotron-3-ultra-550b-a55b"
    assert result["winner_seq"] == 1
    assert result["runner_up_seq"] == 2
    assert result["receipt_id"] == "rcpt_llama_01"

