"""Meterless Runtime Provider.

OpenAI-compatible LLM provider implementing the Meterless Runtime specification:
- Base URL normalization with duplicate '/v1' prevention and connect-link handling.
- Secure token resolution from '<stateDir>/proxy-token' or configuration.
- Header contracts ('meterless-app: <app_id>', 'meterless-scope: user').
- Model discovery across routing modes ('meterless/auto', 'meterless/fast', etc.).
- Multi-port Auto Detect (8088, 8090, 8787, 11434) across loopback hosts.
- 180-second timeout with resilient error handling.
- Process-wide rate-limit gate: bounded concurrency, shared 429 cooldown,
  exponential full-jitter backoff honoring Retry-After, and single-flight
  de-duplication of identical in-flight chat completions.

Rate-limit background: 'meterless/auto' routes to a cloud lane whose upstream
(NVIDIA) answers bursts with 429. The runtime surfaces that as HTTP 502 with
'nvidia_429' in the body and no Retry-After header, so every caller in this
process must share one view of the upstream's back-pressure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import parse_qs, urlparse
import urllib.request
import urllib.error

logger = logging.getLogger("factoryops.providers.meterless")

DEFAULT_METERLESS_URL = "http://127.0.0.1:8088/v1"
STABLE_APP_ID = "factoryops"
METERLESS_TIMEOUT_SECONDS = 180.0
PROBE_TIMEOUT_SECONDS = 1.5

KNOWN_DISPOSITIONS = {"auto", "fast", "deep", "cheap", "local", "fusion"}
AUTO_DETECT_PORTS = [8088, 8090, 8787, 11434]
AUTO_DETECT_HOSTS = ["127.0.0.1", "localhost", "[::1]"]

# Retry / back-pressure defaults (overridable via environment)
DEFAULT_MAX_RETRIES = 4
DEFAULT_RETRY_BUDGET_SEC = 45.0
DEFAULT_BACKOFF_BASE_SEC = 2.0
DEFAULT_BACKOFF_CAP_SEC = 20.0
DEFAULT_MAX_CONCURRENCY = 1

_RATE_LIMIT_BODY_MARKERS = ("429", "too many requests", "rate limit", "rate_limit")


class MeterlessError(Exception):
    """Base exception for Meterless Runtime provider errors."""
    pass


class MeterlessAuthError(MeterlessError):
    """Raised when authentication credentials are missing or invalid."""
    pass


class MeterlessConnectionError(MeterlessError):
    """Raised when the runtime is offline or unreachable."""
    pass


class MeterlessValidationError(MeterlessError):
    """Raised when URL or input parameters are malformed."""
    pass


class MeterlessRateLimitError(MeterlessError):
    """Raised when upstream runtime or model returns 429 Too Many Requests."""
    def __init__(self, message: str, retry_after: Optional[float] = None, attempts: int = 1):
        super().__init__(message)
        self.retry_after = retry_after
        self.attempts = attempts


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def is_rate_limited_response(code: int, body: str) -> bool:
    """True for a direct 429 or a gateway error (502/503) wrapping an upstream 429."""
    if code == 429:
        return True
    if code in (502, 503):
        lowered = (body or "").lower()
        return any(marker in lowered for marker in _RATE_LIMIT_BODY_MARKERS)
    return False


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parses a Retry-After header given as delta-seconds or an HTTP-date."""
    if not value:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def compute_backoff(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter in [base/2, min(cap, base * 2**attempt)]."""
    ceiling = min(cap, base * (2 ** attempt))
    return random.uniform(min(base / 2.0, ceiling), ceiling)


def strip_reasoning(text: str) -> str:
    """Removes <think> reasoning blocks some Nemotron/DeepSeek lanes emit into content."""
    if not text:
        return ""
    cleaned = text
    if "</think>" in cleaned:
        cleaned = cleaned.rsplit("</think>", 1)[1]
    cleaned = re.sub(r"<think>.*?(</think>|$)", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


class _RuntimeGate:
    """Shared back-pressure state for every client talking to one runtime URL.

    - A bounded semaphore caps concurrent chat completions (default 1), so bursts
      from parallel API requests queue locally instead of tripping the upstream.
    - After any 429, a cooldown deadline is published; other callers wait it out
      (or fail fast when it exceeds their budget) instead of hammering upstream.
    """

    def __init__(self, max_concurrency: int):
        self.semaphore = threading.BoundedSemaphore(max(1, max_concurrency))
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self.consecutive_rate_limits = 0
        self.last_rate_limited_at: Optional[float] = None

    def cooldown_deadline(self) -> float:
        with self._lock:
            return self._cooldown_until

    def note_rate_limited(self, wait_sec: float) -> float:
        with self._lock:
            now = time.monotonic()
            self._cooldown_until = max(self._cooldown_until, now + wait_sec)
            self.consecutive_rate_limits += 1
            self.last_rate_limited_at = time.time()
            return self._cooldown_until

    def note_success(self) -> None:
        with self._lock:
            self.consecutive_rate_limits = 0

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            remaining = max(0.0, self._cooldown_until - time.monotonic())
            return {
                "cooldown_remaining_sec": round(remaining, 2),
                "consecutive_rate_limits": self.consecutive_rate_limits,
                "last_rate_limited_at": (
                    datetime.fromtimestamp(self.last_rate_limited_at, timezone.utc).isoformat()
                    if self.last_rate_limited_at else None
                ),
            }


class _Flight:
    """One in-flight request that identical concurrent callers wait on."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Optional[str] = None
        self.error: Optional[BaseException] = None


_GATES: Dict[str, _RuntimeGate] = {}
_INFLIGHT: Dict[str, _Flight] = {}
_REGISTRY_LOCK = threading.Lock()


def _gate_for(base_url: str) -> _RuntimeGate:
    with _REGISTRY_LOCK:
        gate = _GATES.get(base_url)
        if gate is None:
            gate = _RuntimeGate(_env_int("METERLESS_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY))
            _GATES[base_url] = gate
        return gate


def get_rate_limit_state(base_url: str = DEFAULT_METERLESS_URL) -> Dict[str, Any]:
    """Read-only view of the shared back-pressure state for a runtime URL."""
    return _gate_for(normalize_meterless_url(base_url)).snapshot()


def reset_rate_limit_state() -> None:
    """Clears shared gates and in-flight registries (used by tests)."""
    with _REGISTRY_LOCK:
        _GATES.clear()
        _INFLIGHT.clear()


@dataclass
class MeterlessModelInfo:
    id: str
    display_name: str
    disposition: Optional[str] = None
    is_routing_mode: bool = True


@dataclass
class ProviderStatus:
    status: Literal["online", "unauthorized", "offline"]
    base_url: str
    message: str
    available_models: List[str] = field(default_factory=list)
    tested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def normalize_meterless_url(raw_url: str) -> str:
    """Normalizes an arbitrary user-supplied URL to '<scheme>://<host>:<port>/v1'.

    Supports:
    - 'http://127.0.0.1:8088' -> 'http://127.0.0.1:8088/v1'
    - '127.0.0.1:8088' -> 'http://127.0.0.1:8088/v1'
    - 'http://127.0.0.1:8088/v1' -> 'http://127.0.0.1:8088/v1'
    - 'http://127.0.0.1:8088/v1/chat/completions' -> 'http://127.0.0.1:8088/v1'
    - 'http://127.0.0.1:8088/v1/models' -> 'http://127.0.0.1:8088/v1'
    - 'http://127.0.0.1:8088/healthz' -> 'http://127.0.0.1:8088/v1'
    - Strips duplicate '/v1/v1' -> '/v1'
    - Preserves IPv6 brackets, e.g. 'http://[::1]:8088/v1'
    """
    if not raw_url or not isinstance(raw_url, str):
        raise MeterlessValidationError("URL must be a non-empty string.")

    cleaned = raw_url.strip()

    # Prepend scheme if missing
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", cleaned):
        cleaned = "http://" + cleaned

    try:
        parsed = urlparse(cleaned)
    except Exception as e:
        raise MeterlessValidationError(f"Invalid URL structure: {e}")

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise MeterlessValidationError(f"Unsupported scheme '{scheme}'. Must be 'http' or 'https'.")

    netloc = parsed.netloc
    if not netloc:
        raise MeterlessValidationError("URL is missing a valid host:port.")

    # Process and sanitize path
    path = parsed.path.rstrip("/")

    # Strip known endpoints
    strip_suffixes = [
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/models",
        "/models",
        "/healthz",
        "/connect",
    ]
    for suffix in strip_suffixes:
        if path.endswith(suffix):
            path = path[:-len(suffix)].rstrip("/")
            break

    # Strip any trailing /v1 segments iteratively to avoid /v1/v1
    while path.endswith("/v1"):
        path = path[:-3].rstrip("/")

    # Reconstruct normalized URL strictly ending in /v1
    normalized = f"{scheme}://{netloc}{path}/v1"
    # Ensure no double slashes in path (e.g. //v1)
    normalized = re.sub(r"(?<!:)//+", "/", normalized)
    return normalized


def parse_connect_link(link: str) -> Tuple[str, str]:
    """Parses a Meterless connect-link of the form:
    'http://<host>:<port>/connect#k=<key>'

    Returns:
        (normalized_base_url, token_key)

    Guarantees:
        - Never sends fragment to server.
        - Never logs or exposes token key.
    """
    if not link or not isinstance(link, str):
        raise MeterlessValidationError("Connect link must be a non-empty string.")

    cleaned = link.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", cleaned):
        cleaned = "http://" + cleaned

    parsed = urlparse(cleaned)
    if not parsed.netloc:
        raise MeterlessValidationError("Connect link is missing host:port.")

    # Parse fragment for 'k=<key>'
    fragment = parsed.fragment
    params = parse_qs(fragment)
    key_values = params.get("k", [])
    if not key_values or not key_values[0].strip():
        raise MeterlessValidationError("Connect link missing valid key parameter 'k' in fragment.")

    token = key_values[0].strip()
    if token == "rt-local":
        raise MeterlessAuthError("Placeholder token 'rt-local' is rejected as an invalid credential.")

    # Base URL from origin
    origin = f"{parsed.scheme}://{parsed.netloc}"
    base_url = normalize_meterless_url(origin)
    return base_url, token


def resolve_meterless_token(
    explicit_token: Optional[str] = None,
    state_dir: Optional[Path] = None,
) -> Optional[str]:
    """Resolves the Meterless runtime authentication token from:
    1. Explicit argument
    2. Environment variable 'METERLESS_API_KEY'
    3. Environment variable 'METERLESS_RUNTIME_TOKEN'
    4. Environment variable 'METERLESS_TOKEN'
    5. State directory proxy token: '<stateDir>/proxy-token'
    
    Rejects placeholder 'rt-local'. When METERLESS_DISABLED is truthy, only an
    explicit token is honored, so live inference can be switched off globally.
    """
    token: Optional[str] = None
    disabled = os.getenv("METERLESS_DISABLED", "").strip().lower() in ("1", "true", "yes")

    if explicit_token and explicit_token.strip():
        token = explicit_token.strip()
    elif disabled:
        return None
    elif os.getenv("METERLESS_API_KEY") and os.getenv("METERLESS_API_KEY", "").strip():
        token = os.getenv("METERLESS_API_KEY", "").strip()
    elif os.getenv("METERLESS_RUNTIME_TOKEN") and os.getenv("METERLESS_RUNTIME_TOKEN", "").strip():
        token = os.getenv("METERLESS_RUNTIME_TOKEN", "").strip()
    elif os.getenv("METERLESS_TOKEN") and os.getenv("METERLESS_TOKEN", "").strip():
        token = os.getenv("METERLESS_TOKEN", "").strip()
    else:
        # Check state directory candidates
        candidates = []
        if state_dir:
            candidates.append(state_dir / "proxy-token")

        if os.getenv("METERLESS_STATE_DIR") and os.getenv("METERLESS_STATE_DIR", "").strip():
            candidates.append(Path(os.environ["METERLESS_STATE_DIR"].strip()) / "proxy-token")

        # Standard Windows Nightshift runtime state directory (%LOCALAPPDATA%\ai.meterless.runtime)
        local_app_data = os.getenv("LOCALAPPDATA")
        user_home = Path.home()
        if local_app_data and local_app_data.strip():
            candidates.append(Path(local_app_data.strip()) / "ai.meterless.runtime" / "proxy-token")
        elif user_home:
            candidates.append(user_home / "AppData" / "Local" / "ai.meterless.runtime" / "proxy-token")

        # macOS / Linux standard Nightshift runtime state directories
        if user_home:
            candidates.append(user_home / "Library" / "Application Support" / "ai.meterless.runtime" / "proxy-token")
        candidates.append(Path("/var/lib/meterless/proxy-token"))

        # User-level config directories and legacy paths
        if user_home:
            candidates.extend([
                user_home / ".meterless" / "proxy-token",
                user_home / ".config" / "meterless" / "proxy-token",
                user_home / ".gemini" / "antigravity" / "meterless" / "proxy-token",
            ])

        for path in candidates:
            if path.exists() and path.is_file():
                try:
                    content = path.read_text(encoding="utf-8").strip()
                    if content:
                        token = content
                        break
                except Exception:
                    continue

    if token == "rt-local":
        # Placeholder rejected per spec
        return None

    return token


class MeterlessClient:
    """Client for Meterless Runtime OpenAI-compatible endpoints."""

    def __init__(
        self,
        base_url: str = DEFAULT_METERLESS_URL,
        token: Optional[str] = None,
        app_id: str = STABLE_APP_ID,
        timeout: float = METERLESS_TIMEOUT_SECONDS,
    ):
        self.base_url = normalize_meterless_url(base_url)
        self.token = resolve_meterless_token(token)
        self.app_id = app_id.lower()
        self.timeout = timeout

    def _get_headers(self, scope: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "meterless-app": self.app_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if scope:
            headers["meterless-scope"] = scope
        return headers

    def check_liveness(self) -> bool:
        """Hits GET /healthz as a quick process liveness probe."""
        parsed = urlparse(self.base_url)
        health_url = f"{parsed.scheme}://{parsed.netloc}/healthz"
        try:
            req = urllib.request.Request(health_url, headers={"User-Agent": "FactoryOps/1.0"})
            with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:
                return resp.status == 200
        except Exception:
            return False

    def check_health(self) -> ProviderStatus:
        """Checks runtime status and returns ProviderStatus."""
        return self.get_status_and_models()

    def get_status_and_models(self) -> ProviderStatus:
        """Queries GET /v1/models to determine status:
        - 200 with Meterless model IDs: online
        - 401/403: unauthorized
        - Other/unreachable: offline
        """
        models_url = f"{self.base_url}/models"
        req = urllib.request.Request(models_url, headers=self._get_headers())

        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
                    
                    # Ensure Meterless routing modes are present or preserved
                    meterless_models = [m for m in models if m.startswith("meterless/")]
                    if not meterless_models and "meterless/auto" not in models:
                        # Fallback inclusion if runtime returns generic list
                        meterless_models = ["meterless/auto", "meterless/fast", "meterless/deep"]

                    return ProviderStatus(
                        status="online",
                        base_url=self.base_url,
                        message="Meterless Runtime online and responsive.",
                        available_models=meterless_models or ["meterless/auto"],
                    )
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return ProviderStatus(
                    status="unauthorized",
                    base_url=self.base_url,
                    message="Meterless Runtime requires a valid authorization token.",
                )
            return ProviderStatus(
                status="offline",
                base_url=self.base_url,
                message=f"HTTP {e.code} received from runtime.",
            )
        except Exception as e:
            return ProviderStatus(
                status="offline",
                base_url=self.base_url,
                message=f"Runtime unreachable: {type(e).__name__}",
            )

        return ProviderStatus(
            status="offline",
            base_url=self.base_url,
            message="No response received from runtime.",
        )

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "meterless/auto",
        temperature: float = 0.2,
        max_tokens: int = 500,
        retry_budget_sec: Optional[float] = None,
    ) -> str:
        """Sends an OpenAI-compatible chat completion request.

        Identical concurrent requests share one upstream call (single-flight).
        Rate limits (429, or 502/503 wrapping an upstream 429) are retried with
        bounded exponential full-jitter backoff that honors Retry-After, within
        `retry_budget_sec` (default METERLESS_RETRY_BUDGET_SEC). Returned content
        has <think> reasoning blocks stripped.
        """
        if not self.token:
            raise MeterlessAuthError("Cannot execute chat request: No Meterless token configured.")

        # Ensure content is plain string
        sanitized_messages = []
        for msg in messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            sanitized_messages.append({
                "role": msg.get("role", "user"),
                "content": content,
            })

        payload = {
            "model": model,
            "messages": sanitized_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        flight_key = hashlib.sha256(self.base_url.encode("utf-8") + b"|" + data_bytes).hexdigest()

        # Single-flight: identical in-flight requests wait for the leader's result.
        with _REGISTRY_LOCK:
            flight = _INFLIGHT.get(flight_key)
            is_leader = flight is None
            if is_leader:
                flight = _Flight()
                _INFLIGHT[flight_key] = flight

        if not is_leader:
            budget = retry_budget_sec if retry_budget_sec is not None else _env_float(
                "METERLESS_RETRY_BUDGET_SEC", DEFAULT_RETRY_BUDGET_SEC
            )
            logger.info("Meterless request de-duplicated: joining identical in-flight completion.")
            if not flight.done.wait(timeout=budget + self.timeout):
                raise MeterlessError("Timed out waiting for identical in-flight Meterless request.")
            if flight.error is not None:
                raise flight.error
            return flight.result or ""

        try:
            flight.result = self._send_chat_with_backoff(data_bytes, retry_budget_sec)
            return flight.result
        except BaseException as e:
            flight.error = e
            raise
        finally:
            flight.done.set()
            with _REGISTRY_LOCK:
                _INFLIGHT.pop(flight_key, None)

    def _send_chat_with_backoff(self, data_bytes: bytes, retry_budget_sec: Optional[float]) -> str:
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(url, data=data_bytes, headers=self._get_headers(scope="user"))

        max_retries = _env_int("METERLESS_MAX_RETRIES", DEFAULT_MAX_RETRIES)
        budget = retry_budget_sec if retry_budget_sec is not None else _env_float(
            "METERLESS_RETRY_BUDGET_SEC", DEFAULT_RETRY_BUDGET_SEC
        )
        base = _env_float("METERLESS_BACKOFF_BASE_SEC", DEFAULT_BACKOFF_BASE_SEC)
        cap = _env_float("METERLESS_BACKOFF_CAP_SEC", DEFAULT_BACKOFF_CAP_SEC)
        gate = _gate_for(self.base_url)

        # Budget accounting is logical (request time + waits we chose), so a
        # patched time.sleep in tests cannot turn a bounded loop unbounded.
        spent = 0.0
        honored_deadline = 0.0
        last_body = ""

        for attempt in range(max_retries + 1):
            # 1. Respect a cooldown published by any caller after a 429.
            deadline = gate.cooldown_deadline()
            if deadline > honored_deadline:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining > 0:
                    if spent + remaining > budget:
                        raise MeterlessRateLimitError(
                            f"Meterless upstream cooling down for {remaining:.1f}s after rate limiting; "
                            f"exceeds remaining retry budget ({max(0.0, budget - spent):.1f}s).",
                            retry_after=remaining,
                            attempts=attempt,
                        )
                    logger.info("Meterless cooldown active; waiting %.2fs before attempt %d.", remaining, attempt + 1)
                    time.sleep(remaining)
                    spent += remaining
                honored_deadline = deadline

            # 2. Bounded concurrency toward the runtime.
            acquire_timeout = max(1.0, budget - spent + self.timeout)
            if not gate.semaphore.acquire(timeout=acquire_timeout):
                raise MeterlessRateLimitError(
                    "Timed out queueing for a Meterless request slot.", attempts=attempt
                )
            started = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    res_data = json.loads(resp.read().decode("utf-8"))
                choices = res_data.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    content = (choices[0].get("message") or {}).get("content", "")
                    if isinstance(content, str):
                        gate.note_success()
                        return strip_reasoning(content)
                raise MeterlessError("Malformed response structure from Meterless Runtime.")
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise MeterlessAuthError("Unauthorized: Token rejected by Meterless Runtime.")
                body = e.read().decode("utf-8", errors="ignore")
                if not is_rate_limited_response(e.code, body):
                    raise MeterlessError(f"HTTP {e.code} error from Meterless Runtime: {body[:140]}")
                last_body = body
                headers = getattr(e, "headers", None)
                retry_after = parse_retry_after(headers.get("Retry-After") if headers is not None else None)
                wait_sec = retry_after if retry_after is not None else compute_backoff(attempt, base, cap)
                honored_deadline = gate.note_rate_limited(wait_sec)
                spent += time.monotonic() - started

                if attempt < max_retries and spent + wait_sec <= budget:
                    logger.warning(
                        "Meterless rate limited (HTTP %d, attempt %d/%d); backing off %.2fs.",
                        e.code, attempt + 1, max_retries + 1, wait_sec,
                    )
                    time.sleep(wait_sec)
                    spent += wait_sec
                    continue
                raise MeterlessRateLimitError(
                    f"Meterless rate limit exceeded after {attempt + 1} attempts: {body[:140]}",
                    retry_after=wait_sec,
                    attempts=attempt + 1,
                )
            except urllib.error.URLError as e:
                raise MeterlessConnectionError(f"Connection failed: {e.reason}")
            except MeterlessError:
                raise
            except Exception as e:
                raise MeterlessError(f"Chat completion failed: {e}")
            finally:
                gate.semaphore.release()

        raise MeterlessRateLimitError(
            f"Meterless rate limit exceeded after {max_retries + 1} attempts: {last_body[:140]}",
            attempts=max_retries + 1,
        )

    def preview_route(
        self,
        prompt: str,
        mode: str = "auto",
        width: int = 2,
        app: str = STABLE_APP_ID,
    ) -> Dict[str, Any]:
        """Queries the Nightshift routing preview endpoint without calling models or spending tokens.

        Returns route analysis including raced models, lane candidate, and fit verdict.
        """
        payload = {
            "prompt": prompt,
            "app": app,
            "mode": mode,
            "width": width,
        }
        url = f"{self.base_url}/meterless/route/preview"
        headers = self._get_headers(scope="user")
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise MeterlessAuthError("Unauthorized: Token rejected by Meterless Runtime.")
            body = e.read().decode("utf-8", errors="ignore")
            raise MeterlessError(f"Route preview failed (HTTP {e.code}): {body}")
        except Exception as e:
            raise MeterlessError(f"Route preview failed: {e}")

    def submit_job(
        self,
        prompt: str,
        n: int = 2,
        judge_mode: str = "accuracy",
        kind: str = "best_of_n",
        window: str = "now",
        privacy: str = "cloud-ok",
        app: str = STABLE_APP_ID,
    ) -> Dict[str, Any]:
        """Submits a genuine Nightshift job with competing attempts to POST /v1/jobs."""
        if not self.token:
            raise MeterlessAuthError("Cannot submit Nightshift job: No Meterless token configured.")

        # Strict safety check: Never submit cloud jobs without explicit user authorization
        allow_cloud = (os.getenv("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "false").strip().lower() in ("true", "1", "yes"))
        if privacy == "cloud-ok" and not allow_cloud:
            raise MeterlessError(
                "Nightshift cloud execution blocked: NIGHTSHIFT_ALLOW_CLOUD_JOBS is disabled. "
                "Explicit user authorization is required before submitting cloud jobs or consuming quota."
            )

        payload = {
            "kind": kind,
            "app": app,
            "window": window,
            "n": n,
            "judge_mode": judge_mode,
            "privacy": privacy,
            "messages": [{"role": "user", "content": prompt}],
        }
        url = f"{self.base_url}/jobs"
        headers = self._get_headers(scope="user")
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise MeterlessAuthError("Unauthorized: Token rejected by Meterless Runtime.")
            body = e.read().decode("utf-8", errors="ignore")
            raise MeterlessError(f"Job submission failed (HTTP {e.code}): {body}")
        except Exception as e:
            raise MeterlessError(f"Job submission failed: {e}")

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Retrieves the authoritative job record from GET /v1/jobs/{job_id}."""
        url = f"{self.base_url}/jobs/{job_id}"
        headers = self._get_headers(scope="user")
        req = urllib.request.Request(url, headers=headers, method="GET")
        max_retries = _env_int("METERLESS_MAX_RETRIES", DEFAULT_MAX_RETRIES)
        for attempt in range(max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise MeterlessAuthError("Unauthorized: Token rejected by Meterless Runtime.")
                body = e.read().decode("utf-8", errors="ignore")
                if is_rate_limited_response(e.code, body) and attempt < max_retries:
                    headers = getattr(e, "headers", None)
                    retry_after = parse_retry_after(headers.get("Retry-After") if headers is not None else None)
                    wait_sec = retry_after if retry_after is not None else compute_backoff(
                        attempt,
                        _env_float("METERLESS_BACKOFF_BASE_SEC", DEFAULT_BACKOFF_BASE_SEC),
                        _env_float("METERLESS_BACKOFF_CAP_SEC", DEFAULT_BACKOFF_CAP_SEC),
                    )
                    time.sleep(wait_sec)
                    continue
                raise MeterlessError(f"Failed to retrieve job '{job_id}' (HTTP {e.code}): {body[:140]}")
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(0.2 * (2 ** attempt))
                    continue
                raise MeterlessError(f"Failed to retrieve job '{job_id}': {e}")

    def get_job_outcome(self, job_id: str) -> Dict[str, Any]:
        """Retrieves the judged outcome projection from GET /v1/jobs/{job_id}/outcome."""
        url = f"{self.base_url}/jobs/{job_id}/outcome"
        headers = self._get_headers(scope="user")
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {}

    def get_job_attempt(self, job_id: str, seq: int) -> Dict[str, Any]:
        """Retrieves an individual attempt record from GET /v1/jobs/{job_id}/attempts/{seq}."""
        url = f"{self.base_url}/jobs/{job_id}/attempts/{seq}"
        headers = self._get_headers(scope="user")
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise MeterlessError(f"Failed to retrieve attempt {seq} for job '{job_id}': {e}")

    def get_job_receipt(self, job_id: str) -> Dict[str, Any]:
        """Retrieves signed compute receipt evidence from GET /v1/jobs/{job_id}/receipt."""
        url = f"{self.base_url}/jobs/{job_id}/receipt"
        headers = self._get_headers(scope="user")
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {}

    def execute_competing_attempts_job(
        self,
        prompt: str,
        n: int = 2,
        judge_mode: str = "accuracy",
        kind: str = "best_of_n",
        window: str = "now",
        privacy: str = "cloud-ok",
        app: str = STABLE_APP_ID,
        poll_interval: float = 0.5,
        max_wait_sec: float = 90.0,
    ) -> Dict[str, Any]:
        """Submits a bounded competing-attempts job, monitors until judged, and returns
        the selected attempt alongside complete outcome and receipt provenance.
        """
        job = self.submit_job(
            prompt=prompt,
            n=n,
            judge_mode=judge_mode,
            kind=kind,
            window=window,
            privacy=privacy,
            app=app,
        )
        job_id = job.get("id") or job.get("job_id")
        if not job_id:
            raise MeterlessError("Job submission did not return a valid job ID.")

        start_time = time.time()
        final_job = None
        while time.time() - start_time < max_wait_sec:
            final_job = self.get_job(job_id)
            status = final_job.get("status")
            if status in ("completed", "settled"):
                break
            elif status in ("failed", "cancelled"):
                err_msg = final_job.get("cancel_reason") or final_job.get("error") or f"Job terminated with status '{status}'"
                raise MeterlessError(f"Nightshift job {job_id} failed: {err_msg}")
            time.sleep(poll_interval)

        if not final_job or final_job.get("status") not in ("completed", "settled"):
            raise MeterlessError(f"Nightshift job {job_id} timed out after {max_wait_sec}s.")

        winner_seq = final_job.get("winner_seq")
        if winner_seq is None:
            # Check if there is only 1 attempt generated and completed
            generated = int(final_job.get("generated") or 0)
            if generated >= 1:
                winner_seq = 1
            else:
                raise MeterlessError(f"Nightshift job {job_id} settled without selecting a winning attempt (winner_seq is null).")

        winning_attempt = self.get_job_attempt(job_id, int(winner_seq))
        outcome = self.get_job_outcome(job_id)
        receipt = self.get_job_receipt(job_id)

        content = winning_attempt.get("text") or winning_attempt.get("content") or ""
        model_info = winning_attempt.get("model") or {}
        if isinstance(model_info, str):
            resolved_model = model_info
        elif isinstance(model_info, dict):
            resolved_model = model_info.get("resolved") or model_info.get("requested") or "unspecified_model"
        else:
            resolved_model = "unspecified_model"

        requested_model = "nvidia/nemotron-3-ultra-550b-a55b"
        if isinstance(model_info, dict) and model_info.get("requested"):
            requested_model = model_info.get("requested")

        receipt_id = receipt.get("receipt_id") or winning_attempt.get("receipt_id")

        return {
            "job_id": job_id,
            "status": final_job.get("status"),
            "winner_seq": winner_seq,
            "runner_up_seq": final_job.get("runner_up_seq"),
            "content": content,
            "requested_model": requested_model,
            "resolved_model": resolved_model,
            "receipt_id": receipt_id,
            "judge_mode": judge_mode,
            "judge_outcome": outcome,
            "competing_attempts_count": n,
            "raw_job": final_job,
        }


def auto_detect_meterless_runtime(
    token: Optional[str] = None,
    hosts: Optional[List[str]] = None,
    ports: Optional[List[int]] = None,
) -> ProviderStatus:
    """Probes candidate loopback ports (8088, 8090, 8787, 11434) across hosts.

    Stops at the first candidate that is verified as Meterless ('online' or 'unauthorized').
    """
    target_hosts = hosts or AUTO_DETECT_HOSTS
    target_ports = ports or AUTO_DETECT_PORTS

    for port in target_ports:
        for host in target_hosts:
            candidate_url = f"http://{host}:{port}/v1"
            try:
                # First check /health to confirm engine is meterless if port is open
                parsed = urlparse(candidate_url)
                health_url = f"{parsed.scheme}://{parsed.netloc}/health"
                try:
                    req_h = urllib.request.Request(health_url, headers={"User-Agent": "FactoryOps/1.0"})
                    with urllib.request.urlopen(req_h, timeout=PROBE_TIMEOUT_SECONDS) as resp:
                        if resp.status == 200:
                            h_info = json.loads(resp.read().decode("utf-8"))
                            if h_info.get("engine") != "meterless":
                                continue
                except Exception:
                    pass

                client = MeterlessClient(base_url=candidate_url, token=token)
                status = client.get_status_and_models()
                if status.status in ("online", "unauthorized"):
                    logger.info(f"Auto-detected Meterless Runtime at {status.base_url} ({status.status})")
                    return status
            except Exception:
                continue

    return ProviderStatus(
        status="offline",
        base_url=DEFAULT_METERLESS_URL,
        message="No active Meterless Runtime detected on standard local ports.",
    )


def diagnose_meterless_runtime() -> Dict[str, Any]:
    """Bounded, read-only connectivity and health diagnostic for the active Meterless runtime.
    Identifies active endpoint, HTTP/API compatibility, authentication, and model availability.
    Guarantees no secret tokens are leaked.
    """
    base_url = os.getenv("METERLESS_BASE_URL", DEFAULT_METERLESS_URL)
    token = resolve_meterless_token()
    token_present = bool(token and token != "rt-local")

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # 1. Probe /health
    health_status = "unreachable"
    engine_name = "unknown"
    try:
        req = urllib.request.Request(f"{origin}/health", headers={"User-Agent": "FactoryOps-Diag/1.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                h_data = json.loads(resp.read().decode("utf-8"))
                health_status = h_data.get("status", "ok")
                engine_name = h_data.get("engine", "meterless")
    except Exception as e:
        health_status = f"offline ({type(e).__name__})"

    # 2. Client status and model discovery
    client = MeterlessClient(base_url=base_url, token=token, timeout=5.0)
    provider_status = client.get_status_and_models()

    # 3. Route preview capability check
    route_preview_supported = False
    try:
        if token_present and provider_status.status == "online":
            prev = client.preview_route(prompt="diagnostic probe", mode="auto", width=1)
            route_preview_supported = isinstance(prev, dict) and "schema" in prev
    except Exception:
        route_preview_supported = False

    return {
        "status": provider_status.status,
        "base_url": client.base_url,
        "engine": engine_name,
        "health_endpoint": health_status,
        "auth_configured": token_present,
        "models_count": len(provider_status.available_models),
        "available_models": provider_status.available_models[:10],
        "route_preview_supported": route_preview_supported,
        "jobs_api_supported": True if health_status == "ok" else False,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "cloud_jobs_allowed": os.getenv("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "false").strip().lower() in ("true", "1", "yes"),
    }
