"""
BindIQ — Shared utilities for all Agent 1 collectors.
Every collector imports from here.
"""

import json
import logging
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps


# ═════════════════════════════════════════════════════════════════════════════
# LOGGER
# ═════════════════════════════════════════════════════════════════════════════

def get_logger(name: str, log_dir: Path) -> logging.Logger:
    """
    Returns a named logger that writes to both file and console.
    Safe to call multiple times — returns existing logger if already configured.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger   # already configured — don't add duplicate handlers

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    # File handler (DEBUG level — full detail)
    fh = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console handler (INFO level — human-readable progress)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ═════════════════════════════════════════════════════════════════════════════
# RETRY DECORATOR
# ═════════════════════════════════════════════════════════════════════════════

def retry(
    max_attempts: int = 3,
    delay: float = 2.0,
    exceptions: tuple = (Exception,),
    backoff: float = 1.5,
):
    """
    Decorator: retry a function up to max_attempts times on specified exceptions.
    Uses exponential backoff: delay * backoff^attempt

    Usage:
        @retry(max_attempts=3, delay=2.0, exceptions=(requests.RequestException,))
        def fetch(...): ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        wait = delay * (backoff ** (attempt - 1))
                        time.sleep(wait)
            raise last_exc
        return wrapper
    return decorator


# ═════════════════════════════════════════════════════════════════════════════
# JSON SAVE
# ═════════════════════════════════════════════════════════════════════════════

def save_json(data: dict | list, path: Path) -> None:
    """Write data as pretty-printed JSON. Creates parent dirs if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_json(path: Path) -> dict | list | None:
    """Load JSON from path. Returns None if file doesn't exist."""
    path = Path(path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ═════════════════════════════════════════════════════════════════════════════
# TIMESTAMP
# ═════════════════════════════════════════════════════════════════════════════

def timestamp() -> str:
    """UTC ISO-8601 timestamp string."""
    return datetime.now(timezone.utc).isoformat()


# ═════════════════════════════════════════════════════════════════════════════
# RATE LIMITER
# ═════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Thread-safe rate limiter. Enforces a minimum gap between calls.

    Usage:
        limiter = RateLimiter(calls_per_minute=6)
        limiter.wait()   # call before each HTTP request
    """

    def __init__(self, calls_per_minute: int):
        self._min_gap   = 60.0 / max(calls_per_minute, 1)
        self._lock      = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_gap:
                time.sleep(self._min_gap - elapsed)
            self._last_call = time.monotonic()
