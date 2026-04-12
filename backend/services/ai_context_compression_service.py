"""
AI Context Compression Service - Shared context management for all AI assistants

This module provides:
1. Token estimation for messages using tiktoken (including tool_calls)
2. Context window management with compression triggers
3. Conversation summarization using the user's configured LLM
4. Memory extraction during compression
5. Tool call history restoration from DB format to LLM API format

Architecture:
- restore_tool_calls_to_messages(): Converts DB [{tool, args, result}] back into
  standard LLM API messages (OpenAI tool_calls+tool or Anthropic tool_use+tool_result).
  Called by all 5 AI services when building cross-turn context.
- compress_messages(): Trigger compression at 70% of context window. Generates summary
  of older messages, extracts memories, replaces old messages with summary.
- find_compression_point(): Never splits inside a tool-call group boundary.

Usage:
    from services.ai_context_compression_service import (
        estimate_tokens,
        should_compress,
        compress_messages,
        restore_tool_calls_to_messages,
        calculate_token_usage,
    )
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, TypedDict

import requests
import tiktoken
from sqlalchemy.orm import Session

from services.ai_stream_service import submit_ai_background_task
from services.system_logger import system_logger

logger = logging.getLogger(__name__)

# Initialize tiktoken encoder (cl100k_base works for GPT-4, Claude, and most models)
_encoder = None

def _get_encoder():
    """Lazy load tiktoken encoder."""
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


class CompressionResult(TypedDict):
    """Result of compression operation."""
    messages: List[Dict[str, Any]]  # Compressed message list
    compressed: bool  # Whether compression was performed
    summary: Optional[str]  # Summary text if compressed
    compressed_message_count: int  # Number of messages compressed
    compressed_at: Optional[str]  # ISO timestamp of compression

# Model context window sizes (updated 2026-03)
MODEL_CONTEXT_WINDOWS = {
    # OpenAI - GPT-5 series (must be listed before gpt-4 to match first)
    "gpt-5.4": 272000,
    "gpt-5.2": 400000,
    "gpt-5": 400000,
    # OpenAI - GPT-4 series
    "gpt-4.1": 1047576,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    # OpenAI - o-series reasoning models
    "o4-mini": 200000,
    "o3": 200000,
    "o3-mini": 200000,
    "o1": 200000,
    "o1-mini": 128000,
    # Anthropic
    "claude-opus-4": 200000,
    "claude-sonnet-4": 200000,
    "claude-haiku-4": 200000,
    "claude-3": 200000,
    "claude-sonnet": 200000,
    "claude-opus": 200000,
    "claude-haiku": 200000,
    # Google - Gemini 3 series (must be listed before gemini-2)
    "gemini-3": 1000000,
    "gemini-2.5": 1000000,
    "gemini-2": 1000000,
    "gemini-1.5": 1000000,
    # Deepseek
    "deepseek-chat": 128000,
    "deepseek-reasoner": 128000,
    # Qwen - 3.5 series (must be listed before qwen3)
    "qwen3.5": 262144,
    "qwen3-coder": 262144,
    "qwen3": 262144,
    "qwen-max": 262144,
    "qwen-plus": 131072,
    "qwen-turbo": 131072,
    # xAI Grok
    "grok-4.1": 2000000,
    "grok-4-fast": 2000000,
    "grok-4": 256000,
    "grok-3": 131072,
    # Meta Llama
    "llama-4-scout": 10000000,
    "llama-4-maverick": 1000000,
    "llama-4": 1000000,
    # Moonshot
    "moonshot-v1-128k": 128000,
    "moonshot-v1-32k": 32000,
    "moonshot-v1-8k": 8000,
    # GLM (Zhipu)
    "glm-5": 200000,
    "glm-4": 200000,
    # MiniMax
    "minimax-m2": 204800,
}

# Compression threshold (70% of context window - conservative for tokenizer differences)
COMPRESSION_THRESHOLD = 0.7

# Reserved tokens for system prompt and response
RESERVED_TOKENS = 4000


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for text using tiktoken.
    Uses cl100k_base encoding which works for GPT-4, Claude, and most models.
    """
    if not text:
        return 0

    try:
        enc = _get_encoder()
        return len(enc.encode(text))
    except Exception as e:
        logger.warning(f"tiktoken encoding failed, using fallback: {e}")
        # Fallback: rough estimate of 4 chars per token
        return max(len(text) // 4, 1)


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """
    Estimate total tokens for a list of messages.

    Handles standard messages (role + content) as well as tool-call messages:
    - OpenAI format: "tool_calls" field on assistant messages (function name + arguments JSON)
    - Anthropic format: content list with tool_use/tool_result blocks
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # Anthropic format: content is a list of blocks
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        total += estimate_tokens(item["text"])
                    elif item.get("type") == "tool_use":
                        # Count tool name + serialized input
                        total += estimate_tokens(item.get("name", ""))
                        total += estimate_tokens(json.dumps(item.get("input", {})))
                    elif item.get("type") == "tool_result":
                        tc = item.get("content", "")
                        total += estimate_tokens(tc) if isinstance(tc, str) else 20
        # OpenAI format: tool_calls field on assistant messages
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {})
                total += estimate_tokens(fn.get("name", ""))
                total += estimate_tokens(fn.get("arguments", ""))
        # Add overhead for role and formatting
        total += 4
    return total


def get_context_window(model: str) -> int:
    """Get context window size for a model."""
    model_lower = model.lower()

    # Check exact matches first
    for key, size in MODEL_CONTEXT_WINDOWS.items():
        if key in model_lower:
            return size

    # Default fallback (128K is the minimum for modern models in 2026)
    logger.warning(f"Unknown model '{model}' for context window, using 128K fallback")
    return 128000


def should_compress(
    messages: List[Dict[str, Any]],
    model: str,
    threshold: float = COMPRESSION_THRESHOLD
) -> Tuple[bool, int, int]:
    """
    Check if conversation should be compressed.

    Returns:
        (should_compress, current_tokens, max_tokens)
    """
    context_window = get_context_window(model)
    max_tokens = int(context_window * threshold) - RESERVED_TOKENS
    current_tokens = estimate_messages_tokens(messages)

    return (current_tokens > max_tokens, current_tokens, max_tokens)


# Show warning when usage reaches 85% of compression threshold (15% remaining)
WARNING_RATIO = 0.85


def calculate_token_usage(
    messages: List[Dict[str, Any]],
    model: str
) -> Dict[str, Any]:
    """
    Calculate token usage ratio for a conversation.
    Used to display context usage warning in frontend.

    usage_ratio is relative to max_tokens (the compression trigger line):
    - 0.85 = 85% used, 15% remaining -> show_warning starts
    - 1.0  = at compression line, 0% remaining
    - >1.0 = over limit, capped to 1.0 for display

    Returns:
        {
            "current_tokens": int,
            "max_tokens": int,
            "usage_ratio": float (0.0-1.0, capped),
            "show_warning": bool (True when >= 85% of compression line)
        }
    """
    context_window = get_context_window(model)
    max_tokens = int(context_window * COMPRESSION_THRESHOLD) - RESERVED_TOKENS
    current_tokens = estimate_messages_tokens(messages)
    raw_ratio = current_tokens / max_tokens if max_tokens > 0 else 0

    return {
        "current_tokens": current_tokens,
        "max_tokens": max_tokens,
        "usage_ratio": round(min(raw_ratio, 1.0), 3),
        "show_warning": raw_ratio >= WARNING_RATIO
    }


def find_compression_point(
    messages: List[Dict[str, Any]],
    target_tokens: int
) -> int:
    """
    Find the index where to split messages for compression.
    Keep recent messages, compress older ones.

    IMPORTANT: Never splits inside a tool-call group. A tool-call group is:
    - OpenAI:    assistant(tool_calls) + tool(result)... + assistant(final)
    - Anthropic: assistant(tool_use) + user(tool_result) + assistant(final)
    The split point is adjusted outward to the nearest group boundary.

    Returns index of first message to keep (messages before this will be compressed).
    """
    if not messages:
        return 0

    # Build a map of tool-call group boundaries.
    # Each group starts at an assistant message with tool_calls and ends at the
    # next assistant message that has NO tool_calls (the final reply).
    group_start_of = {}  # index -> group start index
    current_group_start = None
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        has_tool_calls = bool(msg.get("tool_calls"))
        # Anthropic: assistant content list with tool_use blocks
        if not has_tool_calls and isinstance(msg.get("content"), list):
            has_tool_calls = any(
                b.get("type") == "tool_use" for b in msg["content"] if isinstance(b, dict)
            )
        if role == "assistant" and has_tool_calls:
            current_group_start = i
        if current_group_start is not None:
            group_start_of[i] = current_group_start
        # End group when we hit an assistant message without tool_calls
        if role == "assistant" and not has_tool_calls and current_group_start is not None:
            current_group_start = None

    # Calculate tokens from the end to find raw split point
    tokens_from_end = 0
    keep_from_index = len(messages)

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        msg_tokens = _estimate_single_message_tokens(msg)
        if tokens_from_end + msg_tokens > target_tokens:
            break
        tokens_from_end += msg_tokens
        keep_from_index = i

    # Adjust: if split lands inside a tool-call group, move to group start
    if keep_from_index in group_start_of:
        keep_from_index = group_start_of[keep_from_index]

    # Keep at least the last 2 messages
    return min(keep_from_index, len(messages) - 2)


def _estimate_single_message_tokens(msg: Dict[str, Any]) -> int:
    """Estimate tokens for a single message (content + tool_calls)."""
    tokens = 0
    content = msg.get("content", "")
    if isinstance(content, str):
        tokens += estimate_tokens(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    tokens += estimate_tokens(item["text"])
                elif item.get("type") in ("tool_use", "tool_result"):
                    tokens += estimate_tokens(json.dumps(item))
    for tc in msg.get("tool_calls", []):
        fn = tc.get("function", {})
        tokens += estimate_tokens(fn.get("name", ""))
        tokens += estimate_tokens(fn.get("arguments", ""))
    tokens += 4  # overhead
    return tokens


def restore_tool_calls_to_messages(
    history: List[Dict[str, Any]],
    api_format: str = "openai"
) -> List[Dict[str, Any]]:
    """
    Restore tool_calls_log from DB storage format into standard LLM API messages.

    WHY THIS EXISTS:
    During a single task, the LLM sees full tool_call + tool_result messages in memory.
    But when the task completes, only `content` and `tool_calls_log` (JSON) are saved to DB.
    On the next turn, if we only load `content`, the LLM loses all tool call context and
    may redundantly re-call the same tools. This function restores that context.

    DB storage format (tool_calls_log):
        [{"tool": "get_positions", "args": {"symbol": "BTC"}, "result": "{...}"}]

    Restored to OpenAI format:
        assistant(content="", tool_calls=[...]) -> tool(content="...") -> ... -> assistant(content="final reply")

    Restored to Anthropic format:
        assistant(content=[tool_use blocks]) -> user(content=[tool_result blocks]) -> assistant(content="final reply")

    Args:
        history: List of DB message dicts with keys: role, content, tool_calls_log (optional)
        api_format: "openai" or "anthropic"

    Returns:
        List of standard LLM API messages with tool calls properly structured
    """
    messages = []

    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "") or ""
        tool_calls_log_raw = msg.get("tool_calls_log")

        # Parse tool_calls_log (could be JSON string or already a list)
        tool_calls_log = None
        if tool_calls_log_raw:
            if isinstance(tool_calls_log_raw, str):
                try:
                    tool_calls_log = json.loads(tool_calls_log_raw)
                except (json.JSONDecodeError, TypeError):
                    tool_calls_log = None
            elif isinstance(tool_calls_log_raw, list):
                tool_calls_log = tool_calls_log_raw

        # No tool calls -> simple message
        if role != "assistant" or not tool_calls_log:
            messages.append({"role": role, "content": content})
            continue

        # Restore tool calls based on API format
        if api_format == "anthropic":
            messages.extend(
                _restore_anthropic_tool_calls(tool_calls_log, content)
            )
        else:
            messages.extend(
                _restore_openai_tool_calls(tool_calls_log, content)
            )

    return messages


def _restore_openai_tool_calls(
    tool_calls_log: List[Dict[str, Any]],
    final_content: str
) -> List[Dict[str, Any]]:
    """
    Restore tool calls into OpenAI chat completion format.

    Produces:
      1. assistant message with tool_calls array
      2. One tool message per call with matching tool_call_id
      3. Final assistant message with the actual reply content
    """
    result = []

    # Build assistant message with tool_calls
    tc_array = []
    for i, entry in enumerate(tool_calls_log):
        tc_array.append({
            "id": f"call_restored_{i}",
            "type": "function",
            "function": {
                "name": entry.get("tool", "unknown"),
                "arguments": json.dumps(entry.get("args", {}))
            }
        })

    result.append({
        "role": "assistant",
        "content": "",
        "tool_calls": tc_array
    })

    # Add tool result messages
    for i, entry in enumerate(tool_calls_log):
        raw_result = entry.get("result", "")
        result.append({
            "role": "tool",
            "tool_call_id": f"call_restored_{i}",
            "content": raw_result if isinstance(raw_result, str) else json.dumps(raw_result)
        })

    # Final assistant reply
    if final_content:
        result.append({"role": "assistant", "content": final_content})

    return result


def _restore_anthropic_tool_calls(
    tool_calls_log: List[Dict[str, Any]],
    final_content: str
) -> List[Dict[str, Any]]:
    """
    Restore tool calls into Anthropic messages API format.

    Produces:
      1. assistant message with tool_use content blocks
      2. user message with tool_result content blocks
      3. Final assistant message with the actual reply content
    """
    result = []

    # Build tool_use blocks
    tool_use_blocks = []
    for i, entry in enumerate(tool_calls_log):
        tool_use_blocks.append({
            "type": "tool_use",
            "id": f"tooluse_restored_{i}",
            "name": entry.get("tool", "unknown"),
            "input": entry.get("args", {})
        })

    result.append({"role": "assistant", "content": tool_use_blocks})

    # Build tool_result blocks
    tool_result_blocks = []
    for i, entry in enumerate(tool_calls_log):
        raw_result = entry.get("result", "")
        tool_result_blocks.append({
            "type": "tool_result",
            "tool_use_id": f"tooluse_restored_{i}",
            "content": raw_result if isinstance(raw_result, str) else json.dumps(raw_result)
        })

    result.append({"role": "user", "content": tool_result_blocks})

    # Final assistant reply
    if final_content:
        result.append({"role": "assistant", "content": final_content})

    return result


COMPRESSION_PROMPT = """You are a conversation context compressor for a crypto trading AI assistant.
Create a structured summary that preserves all critical context needed to continue the conversation seamlessly.

## What to preserve (in order of priority):

1. **Current task state**: What is the user working on? What stage? (e.g. "Building intraday BTC strategy, v2 generated with EMA crossover + RSI filter, not yet saved")

2. **Key decisions and parameters**: Specific numbers, thresholds, configurations confirmed (e.g. "5x leverage, 2% TP, 1% SL, EMA 9/21")

3. **Tool call results**: Key findings from tools - positions, balances, market data, diagnostics. Summarize findings, not raw data.

4. **Code/strategy change log**: Do NOT include full code. Instead record:
   - What was changed and why (e.g. "Relaxed depth_ratio filter from 0.001 to 0.01 because original was too strict")
   - Version progression (e.g. "v1→v2: added volume confirmation; v2→v3: removed OI filter")
   - Whether the latest version was saved by user or still unsaved

5. **Test results and issues found**: Backtest/live test outcomes, bugs discovered, performance problems, unexpected behavior (e.g. "24h backtest: 0 trades executed due to strict depth filter")

6. **Disagreements and open questions**: Points where user disagreed with AI suggestion, alternative approaches discussed but not chosen, unresolved debates

7. **Pending items**: Unresolved requests, next steps discussed, things user asked to do later

8. **User preferences**: Trading style, risk tolerance, workflow preferences mentioned

## Rules:
- Be specific: include actual numbers, coin names, parameter values
- Be thorough: target 4000-6500 words (this is intentional — modern LLMs have large context windows, preserve detail over brevity)
- Use structured format with clear section headers
- Write as context briefing for the AI's next turn, not as a chat log
- Do NOT include greetings or meta-discussion about the conversation itself
- For code changes: describe the logic change, NOT the code itself

Conversation to summarize:
{conversation}

Structured summary:"""


def generate_summary(
    messages_to_compress: List[Dict[str, Any]],
    api_config: Dict[str, Any]
) -> Optional[str]:
    """
    Generate a summary of messages using LLM.

    Args:
        messages_to_compress: Messages to summarize
        api_config: LLM configuration with base_url, api_key, model

    Returns:
        Summary text or None if failed
    """
    if not messages_to_compress:
        return None

    # Build conversation text (handles both plain messages and tool-call messages)
    conv_parts = []
    for msg in messages_to_compress:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            conv_parts.append(f"{role.upper()}: {content}")
        elif isinstance(content, list):
            # Anthropic format or tool_use/tool_result blocks
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        conv_parts.append(f"ASSISTANT: [Called tool: {block.get('name', '?')}]")
                    elif block.get("type") == "tool_result":
                        tc = block.get("content", "")
                        snippet = tc[:200] if isinstance(tc, str) else str(tc)[:200]
                        conv_parts.append(f"TOOL_RESULT: {snippet}")
        # OpenAI tool_calls on assistant messages
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
            conv_parts.append(f"ASSISTANT: [Called tools: {', '.join(names)}]")

    conversation_text = "\n\n".join(conv_parts)

    # Prepare API call
    base_url = api_config.get("base_url", "")
    api_key = api_config.get("api_key", "")
    model = api_config.get("model", "")
    api_format = api_config.get("api_format", "openai")

    if not all([base_url, api_key, model]):
        logger.warning("Incomplete API config for compression")
        return None

    prompt = COMPRESSION_PROMPT.format(conversation=conversation_text[:30000])

    try:
        from services.ai_decision_service import build_chat_completion_endpoints, build_llm_payload, build_llm_headers

        if api_format == "anthropic":
            endpoints = build_chat_completion_endpoints(base_url, model)
            endpoint = endpoints[0] if endpoints else f"{base_url.rstrip('/')}/messages"
        else:
            endpoints = build_chat_completion_endpoints(base_url, model)
            endpoint = endpoints[0] if endpoints else f"{base_url}/chat/completions"

        # Use unified headers/payload builders (see build_llm_payload in ai_decision_service)
        headers = build_llm_headers(api_format, api_key, base_url)
        body = build_llm_payload(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_format=api_format,
            max_tokens=12000,
        )

        response = requests.post(endpoint, headers=headers, json=body, timeout=600)

        if response.status_code != 200:
            resp_snippet = response.text[:500] if response.text else "empty"
            logger.error(
                f"Compression API error: status={response.status_code}, "
                f"model={model}, endpoint={endpoint}, "
                f"prompt_tokens~{estimate_tokens(prompt)}, "
                f"response={resp_snippet}"
            )
            system_logger.add_log(
                "ERROR", "system_error",
                f"Compression API error: {response.status_code}",
                {
                    "model": model,
                    "endpoint": endpoint,
                    "status": response.status_code,
                    "prompt_tokens": estimate_tokens(prompt),
                    "response_snippet": resp_snippet[:200],
                }
            )
            return None

        data = response.json()

        # Extract content based on format
        if api_format == "anthropic":
            content = data.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "")
        else:
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")

    except Exception as e:
        logger.error(f"Compression failed: {type(e).__name__}: {e}")
        system_logger.add_log(
            "ERROR", "system_error",
            f"Compression exception: {type(e).__name__}",
            {"model": model, "error": str(e)[:300]}
        )

    return None


def compress_messages(
    messages: List[Dict[str, Any]],
    api_config: Dict[str, Any],
    keep_system: bool = True,
    db: Optional[Session] = None,
    extract_memories: bool = True
) -> CompressionResult:
    """
    Compress conversation messages if needed.

    Args:
        messages: Full message list including system prompt
        api_config: LLM configuration
        keep_system: Whether to preserve system messages
        db: Database session (required for memory extraction)
        extract_memories: Whether to extract memories during compression

    Returns:
        CompressionResult with compressed messages and metadata
    """
    model = api_config.get("model", "")
    needs_compression, current, max_tokens = should_compress(messages, model)

    if not needs_compression:
        return CompressionResult(
            messages=messages,
            compressed=False,
            summary=None,
            compressed_message_count=0,
            compressed_at=None
        )

    logger.info(f"Compressing conversation: {current} tokens > {max_tokens} limit")

    # Separate system messages and conversation
    system_messages = []
    conversation_messages = []

    for msg in messages:
        if msg.get("role") == "system" and keep_system:
            system_messages.append(msg)
        else:
            conversation_messages.append(msg)

    # Find compression point (keep ~40% of max tokens for recent messages)
    target_keep = int(max_tokens * 0.4)
    split_index = find_compression_point(conversation_messages, target_keep)

    if split_index <= 0:
        # Nothing to compress
        return CompressionResult(
            messages=messages,
            compressed=False,
            summary=None,
            compressed_message_count=0,
            compressed_at=None
        )

    # Split messages
    to_compress = conversation_messages[:split_index]
    to_keep = conversation_messages[split_index:]

    # Extract memories in background thread (non-blocking)
    if extract_memories and db:
        try:
            mem_parts = []
            for m in to_compress:
                role = m.get('role', 'unknown').upper()
                content = m.get('content', '')
                if isinstance(content, str) and content.strip():
                    mem_parts.append(f"{role}: {content}")
                for tc in m.get('tool_calls', []):
                    fn = tc.get('function', {})
                    mem_parts.append(f"TOOL_CALL: {fn.get('name', '?')}")
            conv_text = "\n\n".join(mem_parts)

            # Copy api_config to avoid thread-safety issues
            api_config_copy = dict(api_config)

            def _extract_memories_bg(conv_text_bg, api_cfg_bg):
                """Background thread for memory extraction + batch dedup."""
                from database.connection import SessionLocal
                bg_db = SessionLocal()
                try:
                    from services.hyper_ai_memory_service import process_compression_memories
                    count = process_compression_memories(bg_db, conv_text_bg, api_cfg_bg)
                    logger.warning(f"[Compression] Background memory extraction done: {count} memories")
                except Exception as e:
                    logger.warning(f"[Compression] Background memory extraction failed: {type(e).__name__}: {e}")
                finally:
                    bg_db.close()

            submit_ai_background_task(_extract_memories_bg, conv_text, api_config_copy)
        except Exception as e:
            logger.warning(f"[Compression] Failed to start memory extraction thread: {e}")

    # Generate summary
    summary = generate_summary(to_compress, api_config)

    if not summary:
        # Fallback: just truncate without summary
        logger.warning("Summary generation failed, truncating without summary")
        return CompressionResult(
            messages=system_messages + to_keep,
            compressed=True,
            summary=None,
            compressed_message_count=len(to_compress),
            compressed_at=datetime.now(timezone.utc).isoformat()
        )

    # Build compressed message list
    compressed = system_messages.copy()

    # Add summary as a system message
    compressed.append({
        "role": "system",
        "content": f"[Previous conversation summary]\n{summary}"
    })

    # Add recent messages
    compressed.extend(to_keep)

    new_tokens = estimate_messages_tokens(compressed)
    logger.info(f"Compression complete: {current} -> {new_tokens} tokens")

    return CompressionResult(
        messages=compressed,
        compressed=True,
        summary=summary,
        compressed_message_count=len(to_compress),
        compressed_at=datetime.now(timezone.utc).isoformat()
    )


def update_compression_points(
    conversation: Any,
    last_message_id: int,
    summary: str,
    compressed_at: str,
    db: Session
) -> None:
    """
    Update conversation's compression_points field after compression.

    Args:
        conversation: Conversation ORM object (any type)
        last_message_id: ID of the last message before compression point
        summary: Summary text of compressed messages
        compressed_at: ISO timestamp of compression
        db: Database session
    """
    # Parse existing compression points
    existing = []
    if conversation.compression_points:
        try:
            existing = json.loads(conversation.compression_points)
        except (json.JSONDecodeError, TypeError):
            existing = []

    # Add new compression point
    new_point = {
        "message_id": last_message_id,
        "summary": summary,
        "compressed_at": compressed_at
    }
    existing.append(new_point)

    # Update conversation
    conversation.compression_points = json.dumps(existing)
    db.commit()
    logger.info(f"Updated compression_points for conversation {conversation.id}")


def get_last_compression_point(conversation: Any) -> Optional[Dict[str, Any]]:
    """
    Get the most recent compression point from a conversation.

    Returns:
        {"message_id": int, "summary": str, "compressed_at": str} or None
    """
    if not conversation.compression_points:
        return None
    try:
        points = json.loads(conversation.compression_points)
        if isinstance(points, list) and points:
            return points[-1]
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def filter_messages_by_compression(
    messages_orm: list,
    compression_point: Optional[Dict[str, Any]]
) -> list:
    """
    Filter ORM message objects by compression point.
    Only keep messages with id > compression_point["message_id"].

    Args:
        messages_orm: List of ORM message objects (must have .id attribute)
        compression_point: Result from get_last_compression_point()

    Returns:
        Filtered list of ORM message objects (after compression point)
    """
    if not compression_point:
        return messages_orm

    cp_message_id = compression_point.get("message_id", 0)
    return [m for m in messages_orm if m.id > cp_message_id]
