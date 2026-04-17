"""
AI Stream Service - Shared Infrastructure for AI Assistants

This module provides the common streaming infrastructure for all AI assistants:
- StreamBuffer: In-memory queue management with 15-minute expiration
- Background task management for decoupled frontend/backend communication
- Common message field handling (role, content, reasoning_snapshot, tool_calls_log, is_complete)

For new AI assistants, use this module as the foundation and only implement:
- Domain-specific tools
- Domain-specific result fields (e.g., prompt_result, signal_configs, code_suggestion)

Architecture (IMPORTANT - read before modifying any AI streaming code):

    The frontend and backend are FULLY DECOUPLED via an in-memory buffer.
    This is NOT a direct SSE long-connection. The flow is:

    1. Frontend POST /api/hyper-ai/chat -> Backend returns task_id immediately
    2. Backend spawns a background thread running the AI generator (e.g. stream_chat_response)
    3. Generator yields SSE-formatted strings -> run_ai_task_in_background() parses them
       and writes each event into StreamBufferManager (in-memory, keyed by task_id)
    4. Frontend polls GET /api/ai-stream/{task_id}?offset=N every 300ms to pull new events
    5. If frontend disconnects (page close/refresh), the background thread keeps running
       and events keep accumulating in the buffer (15-min expiry)
    6. Frontend reconnects -> resumes polling from last offset -> gets all missed events

    Key implication: ANY event yielded by the generator automatically becomes available
    to the frontend via polling. To add new event types (e.g. subagent progress), just
    yield them from the generator - no changes needed in this module.
"""
import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Buffer expiration time (15 minutes)
BUFFER_EXPIRATION_SECONDS = 15 * 60
AI_TASK_MAX_WORKERS = int(os.getenv("AI_TASK_MAX_WORKERS", "12"))
AI_BACKGROUND_MAX_WORKERS = int(os.getenv("AI_BACKGROUND_MAX_WORKERS", "4"))

_ai_task_executor = ThreadPoolExecutor(
    max_workers=AI_TASK_MAX_WORKERS,
    thread_name_prefix="ai-task",
)
_ai_background_executor = ThreadPoolExecutor(
    max_workers=AI_BACKGROUND_MAX_WORKERS,
    thread_name_prefix="ai-bg",
)


@dataclass
class StreamChunk:
    """A single chunk in the stream buffer."""
    event_type: str
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)


@dataclass
class StreamTask:
    """Represents a streaming task with its buffer and metadata."""
    task_id: str
    conversation_id: Optional[int] = None
    status: str = "running"  # running, completed, error
    chunks: List[StreamChunk] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

    # Accumulated data for database persistence
    reasoning_parts: List[str] = field(default_factory=list)
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)
    final_content: str = ""

    # Runtime checkpoint state for Hyper AI high-risk tool confirmations
    confirmation_event: threading.Event = field(default_factory=threading.Event)
    confirmation_response: Optional[Dict[str, Any]] = field(default=None)
    pending_confirmation_id: Optional[str] = field(default=None)


class StreamBufferManager:
    """
    Manages stream buffers for all active AI tasks.

    Thread-safe singleton that handles:
    - Creating and tracking stream tasks
    - Adding chunks to task buffers
    - Retrieving chunks with offset support
    - Automatic cleanup of expired tasks
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._tasks: Dict[str, StreamTask] = {}
        self._tasks_lock = threading.Lock()
        self._cleanup_thread = None
        self._running = True
        self._start_cleanup_thread()
        self._initialized = True

    def _start_cleanup_thread(self):
        """Start background thread for cleaning up expired tasks."""
        def cleanup_loop():
            while self._running:
                try:
                    self._cleanup_expired_tasks()
                except Exception as e:
                    logger.error(f"[StreamBuffer] Cleanup error: {e}")
                time.sleep(60)  # Check every minute

        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _cleanup_expired_tasks(self):
        """Remove tasks that have been completed for more than 15 minutes."""
        now = time.time()
        expired_ids = []

        with self._tasks_lock:
            for task_id, task in self._tasks.items():
                # Only clean up completed/error tasks after expiration
                if task.status in ("completed", "error"):
                    if task.completed_at and (now - task.completed_at) > BUFFER_EXPIRATION_SECONDS:
                        expired_ids.append(task_id)
                # Also clean up very old running tasks (stuck tasks, > 30 minutes)
                elif (now - task.created_at) > BUFFER_EXPIRATION_SECONDS * 2:
                    expired_ids.append(task_id)

            for task_id in expired_ids:
                del self._tasks[task_id]
                logger.debug(f"[StreamBuffer] Cleaned up expired task: {task_id}")

    def create_task(self, task_id: str, conversation_id: Optional[int] = None) -> StreamTask:
        """Create a new stream task."""
        with self._tasks_lock:
            if task_id in self._tasks:
                logger.warning(f"[StreamBuffer] Task {task_id} already exists, overwriting")
            task = StreamTask(task_id=task_id, conversation_id=conversation_id)
            self._tasks[task_id] = task
            return task

    def get_task(self, task_id: str) -> Optional[StreamTask]:
        """Get a task by ID."""
        with self._tasks_lock:
            return self._tasks.get(task_id)

    def add_chunk(self, task_id: str, event_type: str, data: Dict[str, Any]):
        """Add a chunk to a task's buffer."""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task:
                task.chunks.append(StreamChunk(event_type=event_type, data=data))

    def get_chunks(self, task_id: str, offset: int = 0) -> tuple[List[StreamChunk], str]:
        """
        Get chunks from a task starting at offset.
        Returns (chunks, status).
        """
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return [], "not_found"
            return task.chunks[offset:], task.status

    def complete_task(self, task_id: str, result: Optional[Dict[str, Any]] = None):
        """Mark a task as completed."""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "completed"
                task.completed_at = time.time()
                task.result = result

    def fail_task(self, task_id: str, error_message: str):
        """Mark a task as failed."""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "error"
                task.completed_at = time.time()
                task.error_message = error_message

    def update_task_data(self, task_id: str, **kwargs):
        """Update task accumulated data (reasoning_parts, tool_calls_log, etc.)."""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task:
                for key, value in kwargs.items():
                    if hasattr(task, key):
                        setattr(task, key, value)

    def submit_confirmation(self, task_id: str, confirmation_id: str, confirmed: bool) -> bool:
        """Submit a user response for a pending runtime checkpoint."""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task or task.status != "running":
                return False
            if not task.pending_confirmation_id:
                return False
            if task.pending_confirmation_id != confirmation_id:
                return False
            task.confirmation_response = {
                "confirmation_id": confirmation_id,
                "confirmed": bool(confirmed),
                "submitted_at": time.time(),
            }
            task.confirmation_event.set()
            return True

    def begin_confirmation(self, task_id: str, confirmation_id: str) -> bool:
        """Start a pending runtime checkpoint for a task."""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task or task.status != "running":
                return False
            if task.pending_confirmation_id:
                return False
            task.confirmation_response = None
            task.pending_confirmation_id = confirmation_id
            task.confirmation_event.clear()
            return True

    def clear_confirmation(self, task_id: str, confirmation_id: Optional[str] = None):
        """Clear pending runtime checkpoint state."""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if confirmation_id and task.pending_confirmation_id != confirmation_id:
                return
            task.confirmation_response = None
            task.pending_confirmation_id = None
            task.confirmation_event.clear()

    def get_pending_task_for_conversation(self, conversation_id: int) -> Optional[StreamTask]:
        """Check if there's a running task for a conversation."""
        with self._tasks_lock:
            for task in self._tasks.values():
                if task.conversation_id == conversation_id and task.status == "running":
                    return task
            return None


# Global singleton instance
_buffer_manager: Optional[StreamBufferManager] = None


def get_buffer_manager() -> StreamBufferManager:
    """Get the global StreamBufferManager instance."""
    global _buffer_manager
    if _buffer_manager is None:
        _buffer_manager = StreamBufferManager()
    return _buffer_manager


def format_sse_event(event_type: str, data: Any) -> str:
    """Format data as an SSE event string."""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"


def generate_task_id(prefix: str = "ai") -> str:
    """Generate a unique task ID."""
    return f"{prefix}_{int(time.time() * 1000)}_{id(threading.current_thread()) % 10000}"


def run_ai_task_in_background(
    task_id: str,
    generator_func: Callable[[], Generator[str, None, None]],
    on_complete: Optional[Callable[[StreamTask], None]] = None,
    on_error: Optional[Callable[[StreamTask, Exception], None]] = None
) -> Future:
    """
    Run an AI streaming task in a background thread.

    The generator_func yields SSE-formatted strings. Each event is parsed and stored
    in StreamBufferManager, where the frontend retrieves it via polling. The generator
    runs independently of the frontend connection - if the frontend disconnects, this
    thread keeps running and events accumulate in the buffer (15-min expiry).

    Any new SSE event type yielded by the generator will automatically be available
    to the frontend without changes here.
    """
    def run():
        manager = get_buffer_manager()
        task = manager.get_task(task_id)

        try:
            for sse_event in generator_func():
                # Parse SSE event: "event: type\ndata: {...}\n\n"
                if not sse_event or not sse_event.strip():
                    continue

                lines = sse_event.strip().split('\n')
                event_type = "message"
                data = {}

                for line in lines:
                    if line.startswith('event: '):
                        event_type = line[7:]
                    elif line.startswith('data: '):
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            data = {"raw": line[6:]}

                # Add to buffer
                manager.add_chunk(task_id, event_type, data)

                # Track completion/error events
                if event_type == "done":
                    manager.complete_task(task_id, data)
                    if on_complete and task:
                        on_complete(task)
                    return
                elif event_type == "error":
                    manager.fail_task(task_id, data.get("message", "Unknown error"))
                    return
                elif event_type == "interrupted":
                    manager.fail_task(task_id, f"Interrupted: {data.get('error', 'Unknown')}")
                    return

            # Generator finished without done/error event
            manager.complete_task(task_id, {"status": "completed"})
            if on_complete and task:
                on_complete(task)

        except Exception as e:
            logger.error(f"[AITask {task_id}] Background task error: {e}", exc_info=True)
            manager.fail_task(task_id, str(e))
            if on_error and task:
                on_error(task, e)

    return _ai_task_executor.submit(run)


def submit_ai_background_task(
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Future:
    """Run non-stream AI background work with a bounded executor."""
    return _ai_background_executor.submit(func, *args, **kwargs)


def get_ai_runtime_stats() -> Dict[str, int]:
    """Return bounded AI executor stats for low-frequency health monitoring."""
    manager = get_buffer_manager()

    with manager._tasks_lock:
        running_tasks = sum(1 for task in manager._tasks.values() if task.status == "running")

    task_threads = len(getattr(_ai_task_executor, "_threads", ()))
    background_threads = len(getattr(_ai_background_executor, "_threads", ()))
    task_queue = getattr(getattr(_ai_task_executor, "_work_queue", None), "qsize", lambda: 0)()
    background_queue = getattr(getattr(_ai_background_executor, "_work_queue", None), "qsize", lambda: 0)()

    return {
        "running_tasks": running_tasks,
        "task_max_workers": AI_TASK_MAX_WORKERS,
        "task_threads": task_threads,
        "task_queue": task_queue,
        "background_max_workers": AI_BACKGROUND_MAX_WORKERS,
        "background_threads": background_threads,
        "background_queue": background_queue,
    }
