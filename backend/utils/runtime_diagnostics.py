import logging
import threading
import time
from typing import Any

THREAD_DIAGNOSTIC_THRESHOLD = 200
HOT_PATH_WARNING_COOLDOWN_SECONDS = 120
SLOW_REQUEST_WARNING_SECONDS = 2.0

_warning_lock = threading.Lock()
_last_warning_at: dict[str, float] = {}


def get_current_thread_count() -> int:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("Threads:"):
                    return int(line.split()[1])
    except OSError:
        return -1
    return -1


def emit_warning_with_cooldown(
    logger: logging.Logger,
    key: str,
    message: str,
    *args: Any,
    cooldown_seconds: int = HOT_PATH_WARNING_COOLDOWN_SECONDS,
) -> None:
    now = time.time()
    with _warning_lock:
        last_warning_at = _last_warning_at.get(key, 0.0)
        if now - last_warning_at < cooldown_seconds:
            return
        _last_warning_at[key] = now
    logger.warning(message, *args)


def log_hot_path_delta(
    logger: logging.Logger,
    key: str,
    path: str,
    start_threads: int,
    start_time_monotonic: float,
    **context: Any,
) -> None:
    end_threads = get_current_thread_count()
    duration_seconds = time.monotonic() - start_time_monotonic

    if max(start_threads, end_threads) < THREAD_DIAGNOSTIC_THRESHOLD:
        return

    context_str = " ".join(f"{name}={value}" for name, value in context.items() if value is not None)
    emit_warning_with_cooldown(
        logger,
        key,
        "[HotPath Diagnostic] path=%s start_threads=%s end_threads=%s delta=%s duration=%.3fs %s",
        path,
        start_threads,
        end_threads,
        end_threads - start_threads,
        duration_seconds,
        context_str,
    )
