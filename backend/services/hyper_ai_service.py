"""
Hyper AI Service - Main Agent for Full-Site AI Intelligence

Hyper AI is the master agent that:
- Guides users through onboarding to collect trading preferences
- Maintains user profile and long-term memory across conversations
- Orchestrates sub-agents (Prompt AI, Program AI, Signal AI, Attribution AI)
- Implements context compression for long conversations
- Supports multiple LLM providers with user selection

Architecture:
- StreamBuffer-based async streaming (same as other AI services)
- Long-term memory auto-injected into system prompt alongside user profile
- Mem0-style batch deduplication for memory management
- Context compression at 70% of context window
- Memory extraction runs async in background thread during compression
"""
import json
import logging
import os
import random
import threading
import time
from typing import Any, Dict, Generator, List, Optional

import requests
from sqlalchemy.orm import Session

from database.models import (
    HyperAiProfile,
    HyperAiMemory,
    HyperAiConversation,
    HyperAiMessage
)
from services.ai_decision_service import (
    build_chat_completion_endpoints,
    detect_api_format,
    _extract_text_from_message,
    get_max_tokens,
    build_llm_payload,
    build_llm_headers,
    is_reasoning_model,
    extract_reasoning,
    convert_tools_to_anthropic,
    convert_messages_to_anthropic,
    strip_thinking_tags,
)
from services.ai_stream_service import (
    get_buffer_manager,
    generate_task_id,
    run_ai_task_in_background,
    format_sse_event,
    submit_ai_background_task,
)
from services.hyper_ai_llm_providers import get_provider, get_all_providers
from services.hyper_ai_tools import HYPER_AI_TOOLS
from services.hyper_ai_subagents import execute_subagent_tool
from services.hyper_ai_harness import (
    RISK_HIGH,
    TOOL_STATUS_BLOCKED,
    TOOL_STATUS_DOMAIN_ERROR,
    TOOL_STATUS_INFRA_ERROR,
    TOOL_STATUS_WARNING,
    SubAgentContractChecker,
    ToolFailureTracker,
    assess_tool_risk,
    blocked_meta,
    blocked_tool_result,
    circuit_breaker_result,
    execute_tool_with_meta,
    generate_confirmation_id,
    mask_tool_args,
)
from utils.encryption import decrypt_private_key

logger = logging.getLogger(__name__)

# Maximum tool call iterations to prevent infinite loops
MAX_TOOL_ITERATIONS = 100

# Retry configuration
API_MAX_RETRIES = 5
API_BASE_DELAY = 1.0
API_MAX_DELAY = 16.0
RETRYABLE_STATUS_CODES = {502, 503, 504, 429}
_suggestions_update_lock = threading.Lock()
_suggestions_update_running = False

# System prompt paths
SYSTEM_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "config",
    "hyper_ai_system_prompt.md"
)
ONBOARDING_PROMPT_EN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "config",
    "hyper_ai_onboarding_prompt.md"
)
ONBOARDING_PROMPT_ZH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "config",
    "hyper_ai_onboarding_prompt_zh.md"
)


def _should_retry_api(status_code: Optional[int], error: Optional[str]) -> bool:
    """Check if API error is retryable."""
    if status_code and status_code in RETRYABLE_STATUS_CODES:
        return True
    if error and any(x in error.lower() for x in ['timeout', 'connection', 'reset']):
        return True
    return False


def _get_retry_delay(attempt: int) -> float:
    """Calculate retry delay with exponential backoff and jitter."""
    delay = min(API_BASE_DELAY * (2 ** attempt), API_MAX_DELAY)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter


def load_system_prompt() -> str:
    """Load the Hyper AI system prompt from markdown file."""
    try:
        with open(SYSTEM_PROMPT_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load Hyper AI system prompt: {e}")
        return "You are Hyper AI, an intelligent trading assistant."


def load_onboarding_prompt(lang: str = "en") -> str:
    """Load the onboarding-specific system prompt based on language."""
    prompt_path = ONBOARDING_PROMPT_ZH_PATH if lang == "zh" else ONBOARDING_PROMPT_EN_PATH
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load onboarding prompt ({lang}): {e}")
        if lang == "zh":
            return DEFAULT_ONBOARDING_PROMPT_ZH
        return DEFAULT_ONBOARDING_PROMPT_EN


DEFAULT_ONBOARDING_PROMPT_EN = """You are Hyper AI, a friendly trading assistant helping a new user get started.

Your goal is to have a natural conversation to learn about the user's trading background and preferences.

Information to collect (through natural conversation, not interrogation):
- Trading experience level (beginner/intermediate/advanced)
- Risk preference (conservative/moderate/aggressive)
- Trading style (day trading/swing trading/position trading/scalping)
- Preferred trading symbols (BTC, ETH, SOL, etc.)

Be warm, conversational, and helpful. Ask follow-up questions naturally.
When you have enough information, let the user know they're all set to explore the system.
"""

DEFAULT_ONBOARDING_PROMPT_ZH = """你是 Hyper AI，一个友好的交易助手，正在帮助新用户入门。

你的目标是通过自然的对话了解用户的交易背景和偏好。

需要收集的信息（通过自然对话，而不是审问）：
- 交易经验水平（新手/有一定经验/资深）
- 风险偏好（保守/稳健/激进）
- 交易风格（日内交易/波段交易/趋势交易/超短线）
- 偏好的交易品种（BTC、ETH、SOL 等）

保持温暖、对话式的风格，自然地提出后续问题。
当你收集到足够的信息后，告诉用户他们已经准备好探索系统了。
"""


def get_or_create_profile(db: Session) -> HyperAiProfile:
    """Get existing profile or create a new one (single-user system)."""
    profile = db.query(HyperAiProfile).first()
    if not profile:
        profile = HyperAiProfile()
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def get_llm_config(db: Session) -> Dict[str, Any]:
    """Get LLM configuration from user profile."""
    profile = get_or_create_profile(db)

    if not profile.llm_provider:
        return {"configured": False}

    # Get provider preset or use custom config
    provider = get_provider(profile.llm_provider)
    base_url = profile.llm_base_url or (provider.base_url if provider else "")
    model = profile.llm_model or (provider.models[0] if provider and provider.models else "")

    # Decrypt API key
    api_key = None
    if profile.llm_api_key_encrypted:
        try:
            api_key = decrypt_private_key(profile.llm_api_key_encrypted)
        except Exception as e:
            logger.error(f"Failed to decrypt API key: {e}")

    # Detect API format from URL for custom provider
    if profile.llm_provider == "custom" and base_url:
        _, api_format = detect_api_format(base_url)
        api_format = api_format or "openai"
    else:
        api_format = provider.api_format if provider else "openai"

    return {
        "configured": True,
        "provider": profile.llm_provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "api_format": api_format
    }


def test_llm_connection(
    provider: str,
    api_key: str,
    model: str,
    base_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Test LLM connection by making a simple API call.
    Returns {"success": True} or {"success": False, "error": "message"}
    """
    # Get provider config
    provider_config = get_provider(provider)

    if provider == "custom":
        if not base_url:
            return {"success": False, "error": "Base URL is required for custom provider"}
        # Auto-detect API format from URL (same as AI Trader)
        url, api_format = detect_api_format(base_url)
        if not url:
            return {"success": False, "error": "Invalid Base URL"}
        api_format = api_format or "openai"
    else:
        if not provider_config:
            return {"success": False, "error": f"Unknown provider: {provider}"}
        effective_base_url = base_url or provider_config.base_url
        api_format = provider_config.api_format
        # Build URL based on api_format
        if api_format == "anthropic":
            url = f"{effective_base_url.rstrip('/')}/messages"
        else:
            url = f"{effective_base_url.rstrip('/')}/chat/completions"

    if not model:
        model = provider_config.models[0] if provider_config and provider_config.models else "gpt-3.5-turbo"

    try:
        # Use unified headers/payload builders (see build_llm_payload in ai_decision_service)
        headers = build_llm_headers(api_format, api_key, url)
        payload = build_llm_payload(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            api_format=api_format,
            max_tokens=10,
        )

        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.status_code == 200:
            return {"success": True}
        else:
            error_msg = response.text[:200] if response.text else f"HTTP {response.status_code}"
            # Try to extract error message from JSON
            try:
                err_json = response.json()
                if "error" in err_json:
                    if isinstance(err_json["error"], dict):
                        error_msg = err_json["error"].get("message", error_msg)
                    else:
                        error_msg = str(err_json["error"])
            except:
                pass
            return {"success": False, "error": error_msg}

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Connection timeout"}
    except requests.exceptions.ConnectionError as e:
        return {"success": False, "error": f"Connection failed: {str(e)[:100]}"}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}


def save_llm_config(
    db: Session,
    provider: str,
    api_key: str,
    model: Optional[str] = None,
    base_url: Optional[str] = None
) -> HyperAiProfile:
    """Save LLM configuration to user profile."""
    from utils.encryption import encrypt_private_key

    profile = get_or_create_profile(db)
    profile.llm_provider = provider
    profile.llm_model = model
    profile.llm_base_url = base_url

    if api_key:
        profile.llm_api_key_encrypted = encrypt_private_key(api_key)

    db.commit()
    db.refresh(profile)
    return profile


def get_or_create_conversation(
    db: Session,
    conversation_id: Optional[int] = None,
    is_onboarding: bool = False
) -> HyperAiConversation:
    """Get existing conversation or create a new one."""
    if conversation_id:
        conv = db.query(HyperAiConversation).filter(
            HyperAiConversation.id == conversation_id
        ).first()
        if conv:
            return conv

    # Create new conversation
    conv = HyperAiConversation(title="Hyper AI Chat", is_onboarding=is_onboarding)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def get_conversation_messages(
    db: Session,
    conversation_id: int,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Get recent messages from a conversation."""
    messages = db.query(HyperAiMessage).filter(
        HyperAiMessage.conversation_id == conversation_id
    ).order_by(HyperAiMessage.created_at.desc()).limit(limit).all()

    return [
        {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "reasoning_snapshot": msg.reasoning_snapshot,
            "tool_calls_log": msg.tool_calls_log,
            "is_complete": msg.is_complete,
            "created_at": msg.created_at.isoformat() if msg.created_at else None
        }
        for msg in reversed(messages)
    ]


def save_message(
    db: Session,
    conversation_id: int,
    role: str,
    content: str,
    reasoning_snapshot: Optional[str] = None,
    tool_calls_log: Optional[str] = None,
    is_complete: bool = True,
    interrupt_reason: Optional[str] = None
) -> HyperAiMessage:
    """Save a message to the conversation."""
    message = HyperAiMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        reasoning_snapshot=reasoning_snapshot,
        tool_calls_log=tool_calls_log,
        is_complete=is_complete,
        interrupt_reason=interrupt_reason
    )
    db.add(message)

    # Update conversation metadata
    conv = db.query(HyperAiConversation).filter(
        HyperAiConversation.id == conversation_id
    ).first()
    if conv:
        conv.message_count = (conv.message_count or 0) + 1
        # Auto-generate title from first user message
        if role == "user" and conv.title == "Hyper AI Chat" and content:
            conv.title = content[:50] + ("..." if len(content) > 50 else "")

    db.commit()
    db.refresh(message)
    return message


def build_messages_for_api(
    db: Session,
    conversation_id: int,
    user_message: str,
    api_config: Dict[str, Any],
    include_tools: bool = True
) -> tuple[List[Dict[str, str]], Optional[List[Dict]], Optional[str]]:
    """
    Build message list for LLM API call with automatic compression.
    Uses compression_points to skip already-compressed messages.
    Returns (messages, tools, command_skill) tuple.
    command_skill is set when user used /command mode (e.g. "/trader-diagnosis").
    """
    from services.ai_context_compression_service import (
        compress_messages, update_compression_points,
        restore_tool_calls_to_messages,
        get_last_compression_point, filter_messages_by_compression,
    )

    messages = []

    # Load user profile (used for both skill filtering and personalization)
    profile = get_or_create_profile(db)

    # System prompt with Skill metadata injection
    system_prompt = load_system_prompt()

    # Inject available skills into system prompt (Level 1: metadata only)
    from services.hyper_ai_skill_engine import (
        scan_all_skills, get_enabled_skills, build_skills_metadata_prompt
    )
    all_skills = scan_all_skills()
    enabled_skills = get_enabled_skills(all_skills, profile.enabled_skills)
    skills_prompt = build_skills_metadata_prompt(enabled_skills)
    system_prompt = system_prompt.replace("{available_skills}", skills_prompt)

    # /Command mode: detect /skill_name or /shortcut prefix and inject full SKILL.md
    command_skill = None
    skill_injection = None  # Will be inserted as separate system msg before user msg
    # Build lookup maps: name -> name, shortcut -> name
    skill_lookup = {}
    for s in enabled_skills:
        skill_lookup[s["name"]] = s["name"]
        if s.get("shortcut"):
            skill_lookup[s["shortcut"]] = s["name"]
    if user_message.startswith("/"):
        parts = user_message.split(None, 1)
        candidate = parts[0][1:]  # strip leading /
        resolved_name = skill_lookup.get(candidate)
        if resolved_name:
            from services.hyper_ai_skill_engine import load_skill
            skill_result = load_skill(resolved_name)
            if skill_result.get("success"):
                command_skill = resolved_name
                skill_injection = (
                    f"[Active Skill: {resolved_name}]\n"
                    f"The user triggered this skill via /{candidate} command. "
                    f"You MUST follow the workflow below step by step, "
                    f"executing ALL phases and checkpoints.\n\n"
                    f"{skill_result['content']}"
                )
                user_message = parts[1].strip() if len(parts) > 1 else "Please start this skill workflow."

    messages.append({"role": "system", "content": system_prompt})

    # Get profile context for personalization
    if profile.onboarding_completed:
        profile_context = _build_profile_context(profile)
        if profile_context:
            messages.append({
                "role": "system",
                "content": f"User Profile:\n{profile_context}"
            })

    # Inject long-term memories into context
    memory_context = _build_memory_context(db)
    if memory_context:
        messages.append({
            "role": "system",
            "content": memory_context
        })

    # Check compression points - load summary instead of old messages
    conversation = db.query(HyperAiConversation).filter(
        HyperAiConversation.id == conversation_id
    ).first()
    cp = get_last_compression_point(conversation) if conversation else None

    if cp and cp.get("summary"):
        messages.append({
            "role": "system",
            "content": f"[Previous conversation summary]\n{cp['summary']}"
        })

    # Load history messages (ORM objects for id-based filtering)
    history_orm = db.query(HyperAiMessage).filter(
        HyperAiMessage.conversation_id == conversation_id
    ).order_by(HyperAiMessage.created_at).limit(100).all()

    # Filter by compression point
    history_orm = filter_messages_by_compression(history_orm, cp)

    last_message_id = history_orm[-1].id if history_orm else None

    # Convert to dicts and restore tool calls
    api_format = api_config.get("api_format", "openai")
    history_dicts = [
        {"role": m.role, "content": m.content, "tool_calls_log": m.tool_calls_log}
        for m in history_orm
    ]
    restored_history = restore_tool_calls_to_messages(history_dicts, api_format)
    messages.extend(restored_history)

    # Current user message — if /command mode matched, the last message in
    # restored_history is the raw "/health" saved by stream_chat_response.
    # Replace it with the parsed user_message instead of appending a duplicate.
    if command_skill and messages and messages[-1].get("role") == "user":
        messages[-1]["content"] = user_message
    else:
        messages.append({"role": "user", "content": user_message})

    # Inject skill workflow as a separate system message right before user message.
    # Placed here (not in system prompt) so it's the last thing AI reads before
    # the user's request, giving it highest attention weight.
    if skill_injection:
        user_msg = messages.pop()  # temporarily remove user msg
        messages.append({"role": "system", "content": skill_injection})
        messages.append(user_msg)  # put user msg back at the end

    # Apply compression if needed
    result = compress_messages(messages, api_config, db=db)
    messages = result["messages"]

    # Update compression_points if compression occurred
    if result["compressed"] and result["summary"] and last_message_id:
        if conversation:
            update_compression_points(
                conversation, last_message_id,
                result["summary"], result["compressed_at"], db
            )

    # Return tools if requested (OpenAI format; Anthropic conversion happens in stream_chat_response)
    tools = HYPER_AI_TOOLS if include_tools else None

    return messages, tools, command_skill


def _build_profile_context(profile: HyperAiProfile) -> str:
    """Build profile context string for system prompt."""
    parts = []
    if profile.trading_style:
        parts.append(f"Trading Style: {profile.trading_style}")
    if profile.risk_preference:
        parts.append(f"Risk Preference: {profile.risk_preference}")
    if profile.experience_level:
        parts.append(f"Experience Level: {profile.experience_level}")
    if profile.preferred_symbols:
        parts.append(f"Preferred Symbols: {profile.preferred_symbols}")
    if profile.preferred_timeframe:
        parts.append(f"Preferred Timeframe: {profile.preferred_timeframe}")
    if profile.capital_scale:
        parts.append(f"Capital Scale: {profile.capital_scale}")
    return "\n".join(parts)


def _build_memory_context(db: Session) -> str:
    """
    Build long-term memory context for system prompt injection.
    Groups memories by category for readability.
    """
    from services.hyper_ai_memory_service import get_memories, MAX_MEMORIES

    memories = get_memories(db, limit=MAX_MEMORIES)
    if not memories:
        return ""

    # Group by category
    groups: Dict[str, List[str]] = {}
    category_labels = {
        "preference": "Trading Preferences",
        "decision": "Key Decisions",
        "lesson": "Lessons Learned",
        "insight": "Market Insights",
        "context": "Context",
    }

    for m in memories:
        cat = m.get("category", "context")
        label = category_labels.get(cat, cat.title())
        if label not in groups:
            groups[label] = []
        groups[label].append(m["content"])

    parts = ["Long-term Memory (insights from past conversations):"]
    for label, items in groups.items():
        parts.append(f"\n[{label}]")
        for item in items:
            parts.append(f"- {item}")

    return "\n".join(parts)



# Sub-agent tool names — these return generators instead of strings
SUBAGENT_TOOL_NAMES = {"call_prompt_ai", "call_program_ai", "call_signal_ai", "call_attribution_ai"}


# Sub-agent tools are executed via execute_subagent_tool (generator, yields progress events).
# Normal tools are executed via execute_hyper_ai_tool (plain function, returns string).
# These two paths MUST stay separate - never wrap them in a single function that contains
# both yield and return, because Python turns ANY function with yield into a generator.


def _tool_error_event_data(meta, severity: str = None) -> Dict[str, Any]:
    return {
        "name": meta.tool_name,
        "status": meta.status,
        "severity": severity or meta.status,
        "code": meta.code,
        "message": meta.message,
        "retryable": meta.retryable,
    }


def _await_tool_confirmation(
    db: Session,
    assistant_msg: HyperAiMessage,
    task_id: Optional[str],
    fn_name: str,
    fn_args: Dict[str, Any],
    risk_assessment,
) -> Generator[str, None, tuple[bool, str]]:
    """Pause a high-risk tool call until the user confirms it."""
    if risk_assessment.risk_level != RISK_HIGH:
        return True, ""

    if not task_id:
        return False, blocked_tool_result(
            "High-risk operation was blocked because the streaming task ID is missing."
        )

    manager = get_buffer_manager()
    confirmation_id = generate_confirmation_id()
    if not manager.begin_confirmation(task_id, confirmation_id):
        return False, blocked_tool_result(
            "High-risk operation was blocked because another confirmation is pending or the task is no longer running."
        )

    assistant_msg.content = "[Waiting for user confirmation...]"
    db.commit()

    yield format_sse_event("confirmation_required", {
        "tool_name": fn_name,
        "args": mask_tool_args(fn_args),
        "description": risk_assessment.description,
        "reason": risk_assessment.reason,
        "confirmation_id": confirmation_id,
    })

    task = manager.get_task(task_id)
    if not task:
        manager.clear_confirmation(task_id, confirmation_id)
        return False, blocked_tool_result("High-risk operation was blocked because the task is no longer available.")

    try:
        confirmed = task.confirmation_event.wait(timeout=300)
        response = task.confirmation_response
    finally:
        manager.clear_confirmation(task_id, confirmation_id)

    if not confirmed or not response or not response.get("confirmed"):
        return False, blocked_tool_result(
            "User declined this operation. The tool was NOT executed. "
            "Do NOT retry or re-ask. Simply acknowledge the cancellation and move on."
        )

    return True, ""


def _execute_harnessed_tool_call(
    db: Session,
    assistant_msg: HyperAiMessage,
    task_id: Optional[str],
    fn_name: str,
    fn_args: Dict[str, Any],
    failure_tracker: ToolFailureTracker,
    llm_config: Dict[str, Any],
) -> Generator[str, None, str]:
    """Execute a Hyper AI tool with runtime harness guardrails."""
    if failure_tracker.is_tripped(fn_name):
        tool_result = circuit_breaker_result(fn_name)
        meta = blocked_meta(fn_name, "Tool is temporarily unavailable after repeated infrastructure failures.")
        yield format_sse_event("tool_error", _tool_error_event_data(meta, severity="circuit_breaker"))
        return tool_result

    risk_assessment = assess_tool_risk(db, fn_name, fn_args)
    confirmed, blocked_result = yield from _await_tool_confirmation(
        db=db,
        assistant_msg=assistant_msg,
        task_id=task_id,
        fn_name=fn_name,
        fn_args=fn_args,
        risk_assessment=risk_assessment,
    )
    if not confirmed:
        meta = blocked_meta(fn_name, "User confirmation was not received. The tool was not executed.")
        yield format_sse_event("tool_error", _tool_error_event_data(meta, severity="user_cancelled"))
        return blocked_result

    if fn_name in SUBAGENT_TOOL_NAMES:
        tool_result = yield from execute_subagent_tool(db, fn_name, fn_args, user_id=1)
        contract_ok, warning = SubAgentContractChecker.check(fn_name, tool_result)
        if not contract_ok:
            tool_result = f"{warning}\n{tool_result}"
            meta = blocked_meta(fn_name, warning)
            meta.status = TOOL_STATUS_DOMAIN_ERROR
            meta.code = "contract_fail"
            yield format_sse_event("tool_error", _tool_error_event_data(meta, severity="contract_fail"))
        return tool_result

    tool_result, meta = execute_tool_with_meta(
        db,
        fn_name,
        fn_args,
        user_id=1,
        api_config=llm_config,
    )
    failure_tracker.record(meta)

    if meta.status in (TOOL_STATUS_INFRA_ERROR, TOOL_STATUS_BLOCKED, TOOL_STATUS_WARNING):
        severity = "infra_error" if meta.status == TOOL_STATUS_INFRA_ERROR else meta.status
        data = _tool_error_event_data(meta, severity=severity)
        if meta.status == TOOL_STATUS_INFRA_ERROR:
            data["failure_count"] = failure_tracker.failure_count(fn_name)
            data["circuit_breaker_tripped"] = failure_tracker.is_tripped(fn_name)
        yield format_sse_event("tool_error", data)

    return tool_result


def stream_chat_response(
    db: Session,
    conversation_id: int,
    user_message: str,
    task_id: Optional[str] = None
) -> Generator[str, None, None]:
    """
    Stream chat response from LLM with tool calling support.

    ARCHITECTURE NOTE: This is a generator that yields SSE-formatted strings.
    It does NOT stream directly to the frontend. Instead:
    - start_chat_task() wraps this generator and passes it to run_ai_task_in_background()
    - run_ai_task_in_background() runs this in a background thread, parsing each yielded
      SSE event and storing it in StreamBufferManager (in-memory buffer)
    - Frontend polls /api/ai-stream/{task_id}?offset=N to pull events from the buffer
    - This means ANY event yielded here automatically reaches the frontend via polling,
      and survives frontend disconnects (buffer has 15-min expiry)

    For sub-agent calls (call_*_ai), the tool execution returns a generator instead of
    a string. This generator yields subagent_progress events (forwarded to frontend)
    and finally yields the result string for the main LLM to continue reasoning.
    """
    # Get LLM config
    llm_config = get_llm_config(db)
    if not llm_config.get("configured"):
        yield format_sse_event("error", {
            "message": "LLM not configured. Please complete onboarding first."
        })
        return

    # Save user message
    save_message(db, conversation_id, "user", user_message)

    # Build messages (with automatic compression) and get tools
    messages, tools, command_skill = build_messages_for_api(db, conversation_id, user_message, llm_config)

    # Emit skill_loaded event if /command mode was used
    if command_skill:
        yield format_sse_event("skill_loaded", {"skill_name": command_skill})

    # Prepare API call
    base_url = llm_config["base_url"]
    model = llm_config["model"]
    api_key = llm_config["api_key"]
    api_format = llm_config.get("api_format", "openai")

    # Build endpoints
    endpoints = build_chat_completion_endpoints(base_url, model)
    if not endpoints:
        yield format_sse_event("error", {"message": "Invalid API endpoint"})
        return

    # Use unified headers builder (see build_llm_headers in ai_decision_service)
    headers = build_llm_headers(api_format, api_key, base_url)

    # Create assistant message upfront with is_complete=False for interrupt recovery
    assistant_msg = HyperAiMessage(
        conversation_id=conversation_id,
        role="assistant",
        content="",
        is_complete=False
    )
    db.add(assistant_msg)
    db.flush()

    # Tool call loop variables
    tool_calls_log = []
    reasoning_snapshot = ""
    final_content = ""
    iteration = 0
    failure_tracker = ToolFailureTracker()

    try:
        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            is_last_round = (iteration == MAX_TOOL_ITERATIONS)

            # On last round, inject a system message forcing the AI to summarize
            if is_last_round:
                messages.append({
                    "role": "user",
                    "content": "[SYSTEM] You have reached the maximum tool call limit. You MUST now provide your final response to the user. Summarize all findings from your tool calls and answer the user's question. Do NOT attempt any more tool calls."
                })

            # Use unified payload builder (see build_llm_payload in ai_decision_service)
            if api_format == "anthropic":
                sys_prompt, anthropic_messages = convert_messages_to_anthropic(messages)
                anthropic_tools = convert_tools_to_anthropic(tools) if tools and not is_last_round else None
                body = build_llm_payload(
                    model=model,
                    messages=[{"role": "system", "content": sys_prompt}] + anthropic_messages,
                    api_format=api_format,
                    tools=anthropic_tools,
                )
            else:
                body = build_llm_payload(
                    model=model,
                    messages=messages,
                    api_format=api_format,
                    tools=tools if tools and not is_last_round else None,
                    tool_choice="auto" if tools and not is_last_round else None,
                )

            # Make API call with retry
            response = None
            last_error = None
            last_status_code = None
            last_response_text = None

            for attempt in range(API_MAX_RETRIES):
                for endpoint in endpoints:
                    try:
                        response = requests.post(
                            endpoint, headers=headers, json=body,
                            timeout=180  # Longer timeout for reasoning models
                        )
                        last_status_code = response.status_code
                        last_response_text = response.text[:2000] if response.text else None

                        if response.status_code == 200:
                            break
                        else:
                            last_error = f"HTTP {response.status_code}"
                            logger.warning(f"[HyperAI] Endpoint failed: {response.status_code} - {response.text[:500]}")
                    except requests.exceptions.Timeout as e:
                        last_error = f"Timeout: {str(e)}"
                        logger.warning(f"[HyperAI] Endpoint timeout: {e}")
                    except requests.exceptions.RequestException as e:
                        last_error = str(e)
                        logger.warning(f"[HyperAI] Request error: {e}")

                if response and response.status_code == 200:
                    break

                # Check if should retry
                if not _should_retry_api(last_status_code, last_error):
                    break

                if attempt < API_MAX_RETRIES - 1:
                    delay = _get_retry_delay(attempt)
                    yield format_sse_event("retry", {
                        "attempt": attempt + 2,
                        "max_retries": API_MAX_RETRIES
                    })
                    time.sleep(delay)

            # Check for failure
            if not response or response.status_code != 200:
                error_parts = []
                if last_error:
                    error_parts.append(f"error={last_error}")
                if last_status_code:
                    error_parts.append(f"status={last_status_code}")
                if last_response_text:
                    error_parts.append(f"response={last_response_text[:500]}")
                error_detail = "; ".join(error_parts) if error_parts else "No response from API"
                logger.error(f"[HyperAI] API failed at round {iteration}: {error_detail}")

                if tool_calls_log:
                    assistant_msg.content = f"[Interrupted at round {iteration}] {error_detail}"
                    assistant_msg.tool_calls_log = json.dumps(tool_calls_log)
                    assistant_msg.reasoning_snapshot = reasoning_snapshot if reasoning_snapshot else None
                    assistant_msg.interrupt_reason = f"Round {iteration}: {error_detail}"
                    db.commit()
                    yield format_sse_event("interrupted", {
                        "message_id": assistant_msg.id,
                        "round": iteration,
                        "error": error_detail,
                        "conversation_id": conversation_id
                    })
                else:
                    db.delete(assistant_msg)
                    db.commit()
                    yield format_sse_event("error", {"message": error_detail})
                return

            # Parse response
            try:
                resp_json = response.json()
            except Exception as e:
                logger.error(f"[HyperAI] Failed to parse response: {e}")
                yield format_sse_event("error", {"message": f"Failed to parse response: {e}"})
                return

            # Extract message based on API format
            if api_format == "anthropic":
                # Anthropic format
                content_blocks = resp_json.get("content", [])
                tool_uses = []
                content = ""
                reasoning_content = ""
                for block in content_blocks:
                    if block.get("type") == "text":
                        content += block.get("text", "")
                    elif block.get("type") == "tool_use":
                        tool_uses.append(block)
                    elif block.get("type") == "thinking":
                        t = block.get("thinking", "")
                        if t:
                            reasoning_content += t
                api_tool_calls = tool_uses
            else:
                # OpenAI format
                message = resp_json["choices"][0]["message"]
                api_tool_calls = message.get("tool_calls", [])
                reasoning_content = message.get("reasoning_content", "") or extract_reasoning(message)
                content = message.get("content", "")

            # Strip <thinking> text tags from content (some proxies embed them)
            content, tag_thinking = strip_thinking_tags(content)
            if tag_thinking and not reasoning_content:
                reasoning_content = tag_thinking

            # Send reasoning content if present
            if reasoning_content:
                yield format_sse_event("reasoning", {"content": reasoning_content})
                reasoning_snapshot += f"\n[Round {iteration}]\n{reasoning_content}"

            # Send content if present
            if content:
                yield format_sse_event("content", {"text": content})

            if api_tool_calls:
                # Process tool calls - build assistant message with reasoning_content for DeepSeek
                if api_format == "anthropic":
                    # Anthropic format - store tool_use_blocks for convert_messages_to_anthropic
                    messages.append({
                        "role": "assistant",
                        "content": content or "",
                        "tool_use_blocks": content_blocks
                    })
                    for tu in api_tool_calls:
                        fn_name = tu.get("name", "")
                        fn_args = tu.get("input", {})
                        tool_use_id = tu.get("id", "")
                        if fn_args == "":
                            fn_args = {}

                        yield format_sse_event("tool_call", {"name": fn_name, "args": fn_args})
                        tool_result = yield from _execute_harnessed_tool_call(
                            db=db,
                            assistant_msg=assistant_msg,
                            task_id=task_id,
                            fn_name=fn_name,
                            fn_args=fn_args,
                            failure_tracker=failure_tracker,
                            llm_config=llm_config,
                        )

                        # Emit skill_loaded event so frontend can show skill status
                        if fn_name == "load_skill":
                            yield format_sse_event("skill_loaded", {
                                "skill_name": fn_args.get("skill_name", "")
                            })

                        tool_calls_log.append({
                            "tool": fn_name,
                            "args": fn_args,
                            # Keep full result for save/create tools (needed for entity cards)
                            # Truncate others to avoid bloating tool_calls_log
                            "result": tool_result if fn_name in ('save_prompt', 'save_program', 'save_signal_pool', 'create_ai_trader') else (tool_result[:500] if len(tool_result) > 500 else tool_result)
                        })
                        yield format_sse_event("tool_result", {
                            "name": fn_name,
                            "result": tool_result[:200] if len(tool_result) > 200 else tool_result
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": tool_result
                        })
                else:
                    # OpenAI format - MUST include reasoning_content for DeepSeek Reasoner
                    assistant_msg_dict = {
                        "role": "assistant",
                        "content": content or "",
                        "tool_calls": api_tool_calls
                    }
                    if reasoning_content:
                        assistant_msg_dict["reasoning_content"] = reasoning_content
                    messages.append(assistant_msg_dict)

                    for tc in api_tool_calls:
                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            fn_args = {}

                        yield format_sse_event("tool_call", {"name": fn_name, "args": fn_args})
                        tool_result = yield from _execute_harnessed_tool_call(
                            db=db,
                            assistant_msg=assistant_msg,
                            task_id=task_id,
                            fn_name=fn_name,
                            fn_args=fn_args,
                            failure_tracker=failure_tracker,
                            llm_config=llm_config,
                        )

                        # Emit skill_loaded event so frontend can show skill status
                        if fn_name == "load_skill":
                            yield format_sse_event("skill_loaded", {
                                "skill_name": fn_args.get("skill_name", "")
                            })

                        tool_calls_log.append({
                            "tool": fn_name,
                            "args": fn_args,
                            # Keep full result for save/create tools (needed for entity cards)
                            # Truncate others to avoid bloating tool_calls_log
                            "result": tool_result if fn_name in ('save_prompt', 'save_program', 'save_signal_pool', 'create_ai_trader') else (tool_result[:500] if len(tool_result) > 500 else tool_result)
                        })
                        yield format_sse_event("tool_result", {
                            "name": fn_name,
                            "result": tool_result[:200] if len(tool_result) > 200 else tool_result
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tool_result
                        })

                # Save progress after each round (for retry support)
                if tool_calls_log:
                    assistant_msg.content = f"[Processing round {iteration}...]"
                    assistant_msg.tool_calls_log = json.dumps(tool_calls_log)
                    assistant_msg.reasoning_snapshot = reasoning_snapshot if reasoning_snapshot else None
                    db.commit()
            else:
                # No tool calls - final response
                final_content = content or ""
                break

        # Handle case where final_content is empty (AI ended with tool calls)
        if not final_content:
            if api_format != "anthropic" and 'message' in dir() and message:
                last_content = message.get("content", "")
                if last_content:
                    final_content = last_content
            if not final_content:
                final_content = "Processing completed."

        # Update assistant message and mark as complete
        assistant_msg.content = final_content
        assistant_msg.reasoning_snapshot = reasoning_snapshot if reasoning_snapshot else None
        assistant_msg.tool_calls_log = json.dumps(tool_calls_log) if tool_calls_log else None
        assistant_msg.is_complete = True

        # Update conversation message count for assistant message
        conv = db.query(HyperAiConversation).filter(
            HyperAiConversation.id == conversation_id
        ).first()
        if conv:
            conv.message_count = (conv.message_count or 0) + 1
        db.commit()

        # Calculate fresh token usage and compression points for frontend
        done_data = {
            "conversation_id": conversation_id,
            "content": final_content,
            "tool_calls_count": len(tool_calls_log),
            "tool_calls_log": tool_calls_log if tool_calls_log else None,
            "reasoning_snapshot": reasoning_snapshot if reasoning_snapshot else None,
        }
        try:
            from services.ai_context_compression_service import (
                calculate_token_usage, restore_tool_calls_to_messages,
                get_last_compression_point
            )
            import json as json_mod
            profile = db.query(HyperAiProfile).first()
            if profile and profile.llm_model and conv:
                llm_cfg = get_llm_config(db)
                af = llm_cfg.get("api_format", "openai")
                cp = get_last_compression_point(conv)
                cp_mid = cp.get("message_id", 0) if cp else 0
                h_orm = db.query(HyperAiMessage).filter(
                    HyperAiMessage.conversation_id == conversation_id,
                    HyperAiMessage.id > cp_mid
                ).order_by(HyperAiMessage.created_at).all()
                md = [{"role": m.role, "content": m.content, "tool_calls_log": m.tool_calls_log} for m in h_orm]
                ml = restore_tool_calls_to_messages(md, af)
                if cp and cp.get("summary"):
                    ml.insert(0, {"role": "system", "content": cp["summary"]})
                done_data["token_usage"] = calculate_token_usage(ml, profile.llm_model)
            if conv and conv.compression_points:
                done_data["compression_points"] = json_mod.loads(conv.compression_points)
        except Exception as te:
            logger.warning(f"[HyperAI] Token calc in done event failed: {te}")

        yield format_sse_event("done", done_data)

    except Exception as e:
        logger.error(f"[HyperAI] Error: {e}", exc_info=True)
        if tool_calls_log:
            assistant_msg.content = f"[Error during processing] {str(e)}"
            assistant_msg.tool_calls_log = json.dumps(tool_calls_log)
            assistant_msg.reasoning_snapshot = reasoning_snapshot if reasoning_snapshot else None
            assistant_msg.interrupt_reason = f"Error: {str(e)}"
            db.commit()
            yield format_sse_event("interrupted", {
                "message_id": assistant_msg.id,
                "error": str(e),
                "conversation_id": conversation_id
            })
        else:
            db.delete(assistant_msg)
            db.commit()
            yield format_sse_event("error", {"message": str(e)})


def start_chat_task(
    db: Session,
    conversation_id: int,
    user_message: str,
    lang: str = None
) -> str:
    """Start a chat task in background and return task_id."""
    task_id = generate_task_id("hyper")
    manager = get_buffer_manager()
    manager.create_task(task_id, conversation_id)

    def generator_func():
        from database.connection import SessionLocal
        task_db = SessionLocal()
        try:
            yield from stream_chat_response(task_db, conversation_id, user_message, task_id=task_id)
        finally:
            task_db.close()

    run_ai_task_in_background(task_id, generator_func)
    return task_id


def stream_onboarding_response(
    db: Session,
    conversation_id: int,
    user_message: str,
    lang: str = "en"
) -> Generator[str, None, None]:
    """Stream onboarding chat response - simplified version for profile collection."""
    llm_config = get_llm_config(db)
    if not llm_config.get("configured"):
        yield format_sse_event("error", {"message": "LLM not configured"})
        return

    # Handle greeting request - AI initiates conversation
    is_greeting = user_message == "__GREETING__"
    if is_greeting:
        user_message = "请用中文介绍你自己并开始引导对话。" if lang == "zh" else "Please introduce yourself and start the onboarding conversation."
    else:
        # Save user message (don't save the greeting trigger)
        save_message(db, conversation_id, "user", user_message)

    # Build messages with onboarding prompt (language-specific)
    messages = []
    system_prompt = load_onboarding_prompt(lang)
    messages.append({"role": "system", "content": system_prompt})

    # Get conversation history (skip for greeting)
    if not is_greeting:
        history = get_conversation_messages(db, conversation_id, limit=20)
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})

    # Make API call (reuse existing logic)
    base_url = llm_config["base_url"]
    model = llm_config["model"]
    api_key = llm_config["api_key"]
    api_format = llm_config.get("api_format", "openai")

    endpoints = build_chat_completion_endpoints(base_url, model)
    if not endpoints:
        yield format_sse_event("error", {"message": "Invalid API endpoint"})
        return

    # Use unified headers/payload builders (see build_llm_payload in ai_decision_service)
    headers = build_llm_headers(api_format, api_key, base_url)

    body = build_llm_payload(
        model=model,
        messages=messages,
        api_format=api_format,
        stream=True,
    )

    response = None
    for attempt in range(API_MAX_RETRIES):
        for endpoint in endpoints:
            try:
                response = requests.post(
                    endpoint, headers=headers, json=body,
                    stream=True, timeout=120
                )
                if response.status_code == 200:
                    break
            except requests.exceptions.RequestException:
                continue
        if response and response.status_code == 200:
            break
        time.sleep(_get_retry_delay(attempt))

    if not response or response.status_code != 200:
        yield format_sse_event("error", {"message": "API request failed"})
        return

    yield from _process_onboarding_stream_response(db, conversation_id, response, api_format)


def start_onboarding_chat_task(
    db: Session,
    conversation_id: int,
    user_message: str,
    lang: str = None
) -> str:
    """Start an onboarding chat task in background."""
    task_id = generate_task_id("onboard")
    manager = get_buffer_manager()
    manager.create_task(task_id, conversation_id)

    # Default to English if not specified
    effective_lang = lang or "en"

    def generator_func():
        from database.connection import SessionLocal
        task_db = SessionLocal()
        try:
            yield from stream_onboarding_response(task_db, conversation_id, user_message, effective_lang)
        finally:
            task_db.close()

    run_ai_task_in_background(task_id, generator_func)
    return task_id


def _build_insight_messages(
    lang: str,
    context: Dict[str, Any],
    selected_event: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Build the one-shot Insight prompt without chat history, memory, or tools."""
    use_zh = (lang or "").startswith("zh")
    language_instruction = (
        "以中文回复。\n"
        "所有自然语言字段必须使用简体中文，包括 market_emotion、headline、summary、key_drivers.text、risks、explanation_markdown、next_cycle_period、confidence_basis、similar_pattern。\n"
        "即使输入数据或字段名是英文，输出内容也必须是中文，不能夹杂英文句子。\n"
    ) if use_zh else (
        "Respond in English.\n"
        "All natural-language fields must be written in English.\n"
    )

    system_prompt = (
        "You are Hyper AI inside Hyper Alpha Arena.\n"
        f"{language_instruction}"
        "You analyze market intelligence for a retail crypto trader.\n"
        "Use only the provided context.\n"
        "Do not use external tools.\n"
        "Return exactly one JSON object and nothing else.\n"
        "Do not use markdown fences.\n"
        "Think in four layers before producing the JSON: technical structure, fund-flow behavior, news/event sentiment, and the conflicts between them.\n"
        "The final directional call must be grounded in those layers instead of giving a free-floating opinion.\n"
        "Use this exact schema:\n"
        "{\n"
        '  "sentiment": "bullish|bearish|mixed",\n'
        '  "probability": 0-100 integer,\n'
        '  "market_emotion": "short phrase",\n'
        '  "headline": "one sentence conclusion",\n'
        '  "summary": "2-3 sentence plain-language explanation",\n'
        '  "sentiment_breakdown": {\n'
        '    "technical": 0-100 integer,\n'
        '    "flow": 0-100 integer,\n'
        '    "news": 0-100 integer\n'
        '  },\n'
        '  "next_cycle_period": "the next period matching the current chart interval",\n'
        '  "next_cycle_target_price": number|null,\n'
        '  "next_cycle_range_low": number|null,\n'
        '  "next_cycle_range_high": number|null,\n'
        '  "technical_levels": [\n'
        '    {"price": number, "type": "support|resistance", "label": "short phrase"}\n'
        '  ],\n'
        '  "key_drivers": [\n'
        '    {"text": "driver 1", "impact": "high|medium|low", "tone": "bullish|bearish|mixed"}\n'
        '  ],\n'
        '  "risks": ["risk 1", "risk 2"],\n'
        '  "confidence_basis": "one sentence explaining why the confidence level is justified",\n'
        '  "similar_pattern": "short description of the nearest comparable market setup from recent behavior",\n'
        '  "explanation_markdown": "short markdown explanation with evidence bullets"\n'
        "}\n"
        "The probability must reflect directional confidence for the next cycle and should be justified by the breakdown scores, not guessed in isolation.\n"
        "The next-cycle target and range must be your forecast for the next period, even if uncertain.\n"
        "Use sentiment_breakdown to score each dimension independently: technical is based on kline structure, momentum, and nearby support/resistance; flow is based on large-order direction, OI change, and funding behavior; news is based on recent event tone, clustering, and relevance.\n"
        "Use technical_levels to identify the most relevant nearby support and resistance levels from the provided chart context.\n"
        "Use key_drivers to rank the most important catalysts. Impact must distinguish primary versus secondary drivers.\n"
        "Use confidence_basis to state what specifically makes the confidence believable.\n"
        "Use similar_pattern to describe the closest recent setup or regime match visible in the provided data. If there is no credible analogue, say that clearly.\n"
        'If evidence is mixed, set sentiment to "mixed" and explain the conflict clearly.\n'
        "The context includes kline behavior, all relevant symbol news events, and selected exchange fund-flow behavior."
    )

    user_payload = {
        "selected_event": selected_event,
        "context": context,
    }

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def stream_insight_response(
    db: Session,
    context: Dict[str, Any],
    selected_event: Optional[Dict[str, Any]] = None,
    lang: str = "en",
) -> Generator[str, None, None]:
    """Stream a one-shot Insight analysis without conversation persistence."""
    llm_config = get_llm_config(db)
    if not llm_config.get("configured"):
        yield format_sse_event("error", {"message": "LLM not configured"})
        return

    base_url = llm_config["base_url"]
    model = llm_config["model"]
    api_key = llm_config["api_key"]
    api_format = llm_config.get("api_format", "openai")

    endpoints = build_chat_completion_endpoints(base_url, model)
    if not endpoints:
        yield format_sse_event("error", {"message": "Invalid API endpoint"})
        return

    headers = build_llm_headers(api_format, api_key, base_url)
    messages = _build_insight_messages(lang or "en", context, selected_event)
    body = build_llm_payload(
        model=model,
        messages=messages,
        api_format=api_format,
        stream=True,
        temperature=0.2,
    )

    response = None
    last_error = None
    last_status_code = None
    last_response_text = None

    for attempt in range(API_MAX_RETRIES):
        for endpoint in endpoints:
            try:
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=body,
                    stream=True,
                    timeout=180,
                )
                last_status_code = response.status_code
                last_response_text = response.text[:2000] if response.text else None
                if response.status_code == 200:
                    break
                last_error = f"HTTP {response.status_code}"
            except requests.exceptions.Timeout as e:
                last_error = f"Timeout: {str(e)}"
            except requests.exceptions.RequestException as e:
                last_error = str(e)

        if response and response.status_code == 200:
            break

        if not _should_retry_api(last_status_code, last_error):
            break

        if attempt < API_MAX_RETRIES - 1:
            yield format_sse_event("retry", {
                "attempt": attempt + 2,
                "max_retries": API_MAX_RETRIES
            })
            time.sleep(_get_retry_delay(attempt))

    if not response or response.status_code != 200:
        error_parts = []
        if last_error:
            error_parts.append(f"error={last_error}")
        if last_status_code:
            error_parts.append(f"status={last_status_code}")
        if last_response_text:
            error_parts.append(f"response={last_response_text[:500]}")
        error_detail = "; ".join(error_parts) if error_parts else "No response from API"
        yield format_sse_event("error", {"message": error_detail})
        return

    content_parts: List[str] = []
    reasoning_parts: List[str] = []

    try:
        for line in response.iter_lines():
            if not line:
                continue

            line_str = line.decode("utf-8")
            if not line_str.startswith("data: "):
                continue

            data_str = line_str[6:]
            if data_str == "[DONE]":
                break

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if api_format == "anthropic":
                event_type = data.get("type")
                if event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            content_parts.append(text)
                            yield format_sse_event("content", {"text": text})
                elif event_type == "content_block_start":
                    content_block = data.get("content_block", {})
                    if content_block.get("type") == "thinking":
                        thinking = content_block.get("thinking", "")
                        if thinking:
                            reasoning_parts.append(thinking)
                            yield format_sse_event("reasoning", {"content": thinking})
            else:
                choices = data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                text = delta.get("content", "")
                if text:
                    content_parts.append(text)
                    yield format_sse_event("content", {"text": text})

                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    reasoning_parts.append(reasoning)
                    yield format_sse_event("reasoning", {"content": reasoning})

        full_content = "".join(content_parts)
        full_reasoning = "".join(reasoning_parts) if reasoning_parts else None

        full_content, tag_thinking = strip_thinking_tags(full_content)
        if tag_thinking:
            full_reasoning = (full_reasoning + "\n\n" + tag_thinking).strip() if full_reasoning else tag_thinking

        yield format_sse_event("done", {
            "content": full_content.strip(),
            "reasoning": full_reasoning,
        })
    except Exception as e:
        yield format_sse_event("error", {"message": str(e)})


def start_insight_task(
    db: Session,
    context: Dict[str, Any],
    selected_event: Optional[Dict[str, Any]] = None,
    lang: Optional[str] = None,
) -> str:
    """Start a one-shot Insight analysis task without chat conversation persistence."""
    task_id = generate_task_id("insight")
    manager = get_buffer_manager()
    manager.create_task(task_id, None)

    effective_lang = lang or "en"

    def generator_func():
        from database.connection import SessionLocal
        task_db = SessionLocal()
        try:
            yield from stream_insight_response(
                task_db,
                context=context,
                selected_event=selected_event,
                lang=effective_lang,
            )
        finally:
            task_db.close()

    run_ai_task_in_background(task_id, generator_func)
    return task_id


def _parse_profile_data(content: str) -> Optional[Dict[str, str]]:
    """Parse [PROFILE_DATA]...[COMPLETE] block from AI response with tolerance."""
    import re

    # Try multiple patterns for tolerance (different AI models may vary)
    patterns = [
        r'\[PROFILE_DATA\](.*?)\[COMPLETE\]',
        r'\[PROFILE_DATA\](.*?)\[/COMPLETE\]',
        r'\[PROFILE\](.*?)\[COMPLETE\]',
        r'\[PROFILE\](.*?)\[/PROFILE\]',
        r'```\s*\[PROFILE_DATA\](.*?)\[COMPLETE\]\s*```',  # In code block
    ]

    block = None
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if match:
            block = match.group(1).strip()
            break

    if not block:
        return None

    data = {}
    for line in block.split('\n'):
        line = line.strip()
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip().lower()
            value = value.strip()
            # Normalize common key variations
            if key in ['name', 'nickname', 'nick', '称呼', '昵称']:
                key = 'nickname'
            elif key in ['exp', 'experience', '经验', '交易经验']:
                key = 'experience'
            elif key in ['risk', 'risk_preference', '风险', '风险偏好']:
                key = 'risk'
            elif key in ['style', 'trading_style', '风格', '交易风格']:
                key = 'style'
            data[key] = value

    return data if data else None


def _strip_profile_markers(content: str) -> str:
    """Remove [PROFILE_DATA]...[COMPLETE] block from content for display."""
    import re

    # Remove various formats of profile data blocks
    patterns = [
        r'\[PROFILE_DATA\].*?\[COMPLETE\]',
        r'\[PROFILE_DATA\].*?\[/COMPLETE\]',
        r'\[PROFILE\].*?\[COMPLETE\]',
        r'\[PROFILE\].*?\[/PROFILE\]',
        r'```\s*\[PROFILE_DATA\].*?\[COMPLETE\]\s*```',
    ]

    cleaned = content
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.DOTALL | re.IGNORECASE)

    # Clean up extra whitespace
    cleaned = cleaned.strip()

    return cleaned


def _save_profile_from_onboarding(db: Session, profile_data: Dict[str, str]) -> None:
    """Save parsed profile data to database."""
    profile = get_or_create_profile(db)

    # Save nickname to profile
    nickname = profile_data.get('nickname', '')
    if nickname:
        profile.nickname = nickname

    # Save profile fields (natural language descriptions)
    if profile_data.get('experience'):
        profile.experience_level = profile_data['experience']

    if profile_data.get('risk'):
        profile.risk_preference = profile_data['risk']

    if profile_data.get('style'):
        style = profile_data['style']
        if style.lower() not in ['未提及', 'not mentioned']:
            profile.trading_style = style

    # Mark onboarding as completed
    profile.onboarding_completed = True

    db.commit()
    logger.info(f"Saved onboarding profile: nickname={nickname}, experience={profile.experience_level}")


def _process_onboarding_stream_response(
    db: Session,
    conversation_id: int,
    response: requests.Response,
    api_format: str
) -> Generator[str, None, None]:
    """Process streaming response for onboarding, handling profile data extraction."""
    content_parts = []
    reasoning_parts = []

    try:
        for line in response.iter_lines():
            if not line:
                continue

            line_str = line.decode('utf-8')
            if not line_str.startswith('data: '):
                continue

            data_str = line_str[6:]
            if data_str == '[DONE]':
                break

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Extract content based on API format
            if api_format == "anthropic":
                delta = data.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        content_parts.append(text)
                        yield format_sse_event("content", {"text": text})
            else:
                # OpenAI format
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        content_parts.append(text)
                        yield format_sse_event("content", {"text": text})

                    reasoning = delta.get("reasoning_content", "")
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield format_sse_event("reasoning", {"text": reasoning})

        # Process full content
        full_content = "".join(content_parts)
        full_reasoning = "".join(reasoning_parts) if reasoning_parts else None

        # Strip <thinking> text tags from content (some proxies embed them)
        full_content, tag_thinking = strip_thinking_tags(full_content)
        if tag_thinking:
            full_reasoning = (full_reasoning + "\n\n" + tag_thinking).strip() if full_reasoning else tag_thinking

        # Check for profile data completion
        profile_data = _parse_profile_data(full_content)
        onboarding_complete = False

        if profile_data:
            # Save profile to database
            _save_profile_from_onboarding(db, profile_data)
            onboarding_complete = True

            # Strip markers from content for display
            display_content = _strip_profile_markers(full_content)
        else:
            display_content = full_content

        # Save assistant message (without profile markers)
        if display_content:
            save_message(
                db, conversation_id, "assistant", display_content,
                reasoning_snapshot=full_reasoning,
                is_complete=True
            )

        yield format_sse_event("done", {
            "conversation_id": conversation_id,
            "content_length": len(display_content),
            "onboarding_complete": onboarding_complete
        })

    except Exception as e:
        logger.error(f"Onboarding stream processing error: {e}", exc_info=True)
        yield format_sse_event("error", {"message": str(e)})


# ============================================================================
# Suggested Questions Generation (for welcome screen)
# ============================================================================

SUGGESTION_CACHE_HOURS = 6  # Update suggestions every 6 hours


def get_suggestions_context(db: Session) -> Dict[str, Any]:
    """
    Gather context for generating suggested questions.
    Returns user profile, recent conversations, and configuration status.
    """
    from database.models import Account, SignalPool, HyperliquidWallet

    profile = get_or_create_profile(db)

    # Get recent 3 conversations (non-onboarding, non-bot)
    recent_convs = db.query(HyperAiConversation).filter(
        HyperAiConversation.is_onboarding == False,
        HyperAiConversation.is_bot_conversation == False
    ).order_by(HyperAiConversation.updated_at.desc()).limit(3).all()

    conversations_context = []
    for conv in recent_convs:
        # Get last 2 user messages and 2 assistant messages
        messages = db.query(HyperAiMessage).filter(
            HyperAiMessage.conversation_id == conv.id
        ).order_by(HyperAiMessage.created_at.desc()).limit(4).all()

        msg_snippets = []
        for msg in reversed(messages):
            content = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
            role_label = "User" if msg.role == "user" else "AI"
            msg_snippets.append(f"- {role_label}: {content}")

        if msg_snippets:
            conversations_context.append({
                "title": conv.title,
                "snippets": msg_snippets
            })

    # Get configuration status
    trader_count = db.query(Account).filter(
        Account.is_deleted == False,
        Account.account_type == "AI"
    ).count()

    signal_pool_count = db.query(SignalPool).count()

    wallet_count = db.query(HyperliquidWallet).count()

    return {
        "profile": {
            "nickname": profile.nickname,
            "trading_style": profile.trading_style,
            "risk_preference": profile.risk_preference,
            "experience_level": profile.experience_level,
            "preferred_symbols": profile.preferred_symbols,
        },
        "conversations": conversations_context,
        "config_status": {
            "trader_count": trader_count,
            "signal_pool_count": signal_pool_count,
            "wallet_count": wallet_count,
        }
    }


def build_suggestions_prompt(context: Dict[str, Any]) -> str:
    """
    Build prompt for generating suggested questions.
    """
    profile = context.get("profile", {})
    conversations = context.get("conversations", [])
    config_status = context.get("config_status", {})

    prompt_parts = ["Based on the following user context, generate 3 short questions the user might want to ask next.\n"]

    # User profile
    if any([profile.get("nickname"), profile.get("trading_style"), profile.get("experience_level")]):
        prompt_parts.append("User Profile:")
        if profile.get("nickname"):
            prompt_parts.append(f"- Name: {profile['nickname']}")
        if profile.get("experience_level"):
            prompt_parts.append(f"- Experience: {profile['experience_level']}")
        if profile.get("trading_style"):
            prompt_parts.append(f"- Style: {profile['trading_style']}")
        if profile.get("risk_preference"):
            prompt_parts.append(f"- Risk: {profile['risk_preference']}")
        prompt_parts.append("")

    # Configuration status
    prompt_parts.append("Current Setup:")
    prompt_parts.append(f"- AI Traders: {config_status.get('trader_count', 0)}")
    prompt_parts.append(f"- Signal Pools: {config_status.get('signal_pool_count', 0)}")
    prompt_parts.append(f"- Wallets: {config_status.get('wallet_count', 0)}")
    prompt_parts.append("")

    # Recent conversations
    if conversations:
        prompt_parts.append("Recent Conversations:")
        for conv in conversations:
            prompt_parts.append(f"\n[{conv['title']}]")
            for snippet in conv["snippets"]:
                prompt_parts.append(snippet)
        prompt_parts.append("")

    prompt_parts.append("---")
    prompt_parts.append("Generate 3 short, natural questions (max 30 chars each) the user might want to continue exploring.")
    prompt_parts.append("Use the same language as the user's recent conversations.")
    prompt_parts.append("Output ONLY a JSON array of 3 strings, no other text.")
    prompt_parts.append('Example: ["How is my BTC Trader doing?", "Create a new signal pool", "Explain leverage settings"]')

    return "\n".join(prompt_parts)


def generate_suggested_questions(db: Session) -> List[str]:
    """
    Generate suggested questions using the user's configured LLM.
    Returns empty list if LLM not configured or generation fails.
    """
    config = get_llm_config(db)
    if not config.get("configured"):
        return []

    context = get_suggestions_context(db)

    # No conversations = new user, return empty (frontend will show default questions)
    if not context.get("conversations"):
        return []

    prompt = build_suggestions_prompt(context)

    # Extract config values
    api_format = config.get("api_format", "openai")
    base_url = config.get("base_url", "")
    model = config.get("model", "")
    api_key = config.get("api_key", "")

    if not all([base_url, api_key, model]):
        logger.warning("[Suggestions] Incomplete LLM config")
        return []

    try:
        # Use unified LLM call pattern (same as hyper_ai_memory_service)
        endpoints = build_chat_completion_endpoints(base_url, model)
        if api_format == "anthropic":
            endpoint = endpoints[0] if endpoints else f"{base_url.rstrip('/')}/messages"
        else:
            endpoint = endpoints[0] if endpoints else f"{base_url.rstrip('/')}/chat/completions"

        headers = build_llm_headers(api_format, api_key, endpoint)
        body = build_llm_payload(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_format=api_format,
            max_tokens=150,
            temperature=0.7,
        )

        logger.info(f"[Suggestions] Calling LLM: {endpoint}, model: {model}")
        response = requests.post(endpoint, headers=headers, json=body, timeout=30)

        if response.status_code != 200:
            logger.warning(f"[Suggestions] LLM error: status={response.status_code}, body={response.text[:200]}")
            return []

        data = response.json()

        # Extract content (same pattern as memory service)
        if api_format == "anthropic":
            content_list = data.get("content", [])
            text = content_list[0].get("text", "") if content_list else ""
        else:
            choices = data.get("choices", [])
            text = choices[0].get("message", {}).get("content", "") if choices else ""

        if not text:
            logger.warning(f"[Suggestions] LLM returned empty content")
            return []

        # Parse JSON array from response
        text = text.strip()
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        questions = json.loads(text)
        if isinstance(questions, list) and len(questions) >= 1:
            logger.info(f"[Suggestions] Generated {len(questions)} questions")
            return questions[:3]

        logger.warning(f"[Suggestions] Invalid response format: {text[:100]}")
        return []

    except requests.exceptions.Timeout:
        logger.warning("[Suggestions] LLM timeout (30s)")
        return []
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"[Suggestions] LLM connection error: {e}")
        return []
    except json.JSONDecodeError as e:
        logger.warning(f"[Suggestions] JSON parse error: {e}")
        return []
    except Exception as e:
        logger.warning(f"[Suggestions] Unexpected error: {type(e).__name__}: {e}")
        return []


def get_or_update_suggestions(db: Session) -> Dict[str, Any]:
    """
    Get cached suggestions or trigger async update if stale.
    Returns current suggestions (may be stale) and triggers background update.
    """
    from datetime import datetime, timedelta

    profile = get_or_create_profile(db)

    # Check if we have conversations at all
    conv_count = db.query(HyperAiConversation).filter(
        HyperAiConversation.is_onboarding == False,
        HyperAiConversation.is_bot_conversation == False
    ).count()

    if conv_count == 0:
        return {
            "suggestions": [],
            "is_new_user": True,
            "updated_at": None
        }

    # Parse cached suggestions
    cached_suggestions = []
    if profile.suggested_questions:
        try:
            cached_suggestions = json.loads(profile.suggested_questions)
        except:
            pass

    # Check if cache is stale
    cache_stale = True
    if profile.suggested_questions_at:
        cache_age = datetime.utcnow() - profile.suggested_questions_at
        cache_stale = cache_age > timedelta(hours=SUGGESTION_CACHE_HOURS)

    # If stale, trigger async update
    if cache_stale:
        with _suggestions_update_lock:
            global _suggestions_update_running
            if _suggestions_update_running:
                return {
                    "suggestions": cached_suggestions,
                    "is_new_user": False,
                    "updated_at": profile.suggested_questions_at.isoformat() if profile.suggested_questions_at else None
                }
            _suggestions_update_running = True

        def update_task():
            global _suggestions_update_running
            from database.connection import SessionLocal
            task_db = SessionLocal()
            try:
                questions = generate_suggested_questions(task_db)
                if questions:
                    task_profile = get_or_create_profile(task_db)
                    task_profile.suggested_questions = json.dumps(questions)
                    task_profile.suggested_questions_at = datetime.utcnow()
                    task_db.commit()
                    logger.info(f"Updated suggested questions: {questions}")
            except Exception as e:
                logger.error(f"Failed to update suggestions: {e}")
            finally:
                task_db.close()
                with _suggestions_update_lock:
                    _suggestions_update_running = False

        submit_ai_background_task(update_task)

    return {
        "suggestions": cached_suggestions,
        "is_new_user": False,
        "updated_at": profile.suggested_questions_at.isoformat() if profile.suggested_questions_at else None
    }
