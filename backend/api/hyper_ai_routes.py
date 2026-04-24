"""
Hyper AI Routes - API endpoints for Hyper AI main agent

Endpoints:
- GET  /api/hyper-ai/providers - List available LLM providers
- GET  /api/hyper-ai/profile - Get user profile and LLM config status
- POST /api/hyper-ai/profile/llm - Save LLM configuration (with connection test)
- POST /api/hyper-ai/test-connection - Test LLM connection without saving
- POST /api/hyper-ai/profile/preferences - Save trading preferences
- GET  /api/hyper-ai/conversations - List conversations
- POST /api/hyper-ai/conversations - Create new conversation
- GET  /api/hyper-ai/conversations/{id}/messages - Get conversation messages
- POST /api/hyper-ai/chat - Start chat (returns task_id for polling)
- GET  /api/hyper-ai/skills - List all skills with enabled status
- PUT  /api/hyper-ai/skills/{name}/toggle - Enable/disable a skill
- GET  /api/hyper-ai/tools - List external tools with config status
- PUT  /api/hyper-ai/tools/{tool_name}/config - Save tool configuration
- DELETE /api/hyper-ai/tools/{tool_name}/config - Remove tool configuration
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import HyperAiConversation
from services.hyper_ai_service import (
    get_or_create_profile,
    get_llm_config,
    save_llm_config,
    test_llm_connection,
    get_or_create_conversation,
    get_conversation_messages,
    start_chat_task,
    start_onboarding_chat_task,
    start_insight_task,
)
from services.hyper_ai_llm_providers import get_all_providers, get_provider
from services.ai_stream_service import get_buffer_manager

router = APIRouter(prefix="/api/hyper-ai", tags=["Hyper AI"])


# Request/Response models
class LLMConfigRequest(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None
    base_url: Optional[str] = None


class PreferencesRequest(BaseModel):
    trading_style: Optional[str] = None
    risk_preference: Optional[str] = None
    experience_level: Optional[str] = None
    preferred_symbols: Optional[str] = None
    preferred_timeframe: Optional[str] = None
    capital_scale: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[int] = None
    mode: Optional[str] = None  # "onboarding" for profile collection
    lang: Optional[str] = None  # "zh" or "en" for language preference


class InsightRequest(BaseModel):
    context: Dict[str, Any]
    selected_event: Optional[Dict[str, Any]] = None
    lang: Optional[str] = None


class ConfirmationRequest(BaseModel):
    task_id: str
    confirmation_id: str
    confirmed: bool


# Endpoints
@router.get("/providers")
def list_providers():
    """List all available LLM providers with their configurations."""
    return {"providers": get_all_providers()}


@router.get("/profile")
def get_profile(db: Session = Depends(get_db)):
    """Get user profile including LLM config status and trading preferences."""
    profile = get_or_create_profile(db)
    llm_config = get_llm_config(db)

    # Get base_url for display
    base_url = llm_config.get("base_url", "") if llm_config.get("configured") else ""

    return {
        "llm_configured": llm_config.get("configured", False),
        "llm_provider": profile.llm_provider,
        "llm_model": profile.llm_model,
        "llm_base_url": base_url,
        "onboarding_completed": profile.onboarding_completed,
        "nickname": profile.nickname,
        "trading_style": profile.trading_style,
        "risk_preference": profile.risk_preference,
        "experience_level": profile.experience_level,
        "preferred_symbols": profile.preferred_symbols,
        "preferred_timeframe": profile.preferred_timeframe,
        "capital_scale": profile.capital_scale,
    }


class TestConnectionRequest(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None
    base_url: Optional[str] = None


@router.post("/test-connection")
def test_connection(request: TestConnectionRequest):
    """Test LLM connection without saving configuration."""
    # Validate provider
    if request.provider != "custom":
        provider = get_provider(request.provider)
        if not provider:
            raise HTTPException(status_code=400, detail="Invalid provider")

    # For custom provider, base_url is required
    if request.provider == "custom" and not request.base_url:
        raise HTTPException(
            status_code=400,
            detail="base_url is required for custom provider"
        )

    # Get default model if not provided
    model = request.model
    if not model and request.provider != "custom":
        provider = get_provider(request.provider)
        if provider and provider.models:
            model = provider.models[0]

    result = test_llm_connection(
        provider=request.provider,
        api_key=request.api_key,
        model=model or "",
        base_url=request.base_url
    )

    return result


@router.post("/profile/llm")
def save_llm_configuration(request: LLMConfigRequest, db: Session = Depends(get_db)):
    """Save LLM provider configuration after testing connection."""
    # Validate provider
    if request.provider != "custom":
        provider = get_provider(request.provider)
        if not provider:
            raise HTTPException(status_code=400, detail="Invalid provider")

    # For custom provider, base_url is required
    if request.provider == "custom" and not request.base_url:
        raise HTTPException(
            status_code=400,
            detail="base_url is required for custom provider"
        )

    # Get default model if not provided
    model = request.model
    if not model and request.provider != "custom":
        provider = get_provider(request.provider)
        if provider and provider.models:
            model = provider.models[0]

    # Test connection before saving
    test_result = test_llm_connection(
        provider=request.provider,
        api_key=request.api_key,
        model=model or "",
        base_url=request.base_url
    )

    if not test_result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=test_result.get("error", "Connection test failed")
        )

    # Save configuration
    profile = save_llm_config(
        db,
        provider=request.provider,
        api_key=request.api_key,
        model=model,
        base_url=request.base_url
    )

    return {"success": True, "provider": profile.llm_provider, "model": profile.llm_model}


@router.post("/profile/preferences")
def save_preferences(request: PreferencesRequest, db: Session = Depends(get_db)):
    """Save trading preferences and mark onboarding as completed."""
    profile = get_or_create_profile(db)

    if request.trading_style is not None:
        profile.trading_style = request.trading_style
    if request.risk_preference is not None:
        profile.risk_preference = request.risk_preference
    if request.experience_level is not None:
        profile.experience_level = request.experience_level
    if request.preferred_symbols is not None:
        profile.preferred_symbols = request.preferred_symbols
    if request.preferred_timeframe is not None:
        profile.preferred_timeframe = request.preferred_timeframe
    if request.capital_scale is not None:
        profile.capital_scale = request.capital_scale

    # Mark onboarding as completed if we have basic info
    if profile.trading_style and profile.risk_preference:
        profile.onboarding_completed = True

    db.commit()
    db.refresh(profile)

    return {
        "success": True,
        "onboarding_completed": profile.onboarding_completed
    }


@router.get("/suggestions")
def get_suggestions(db: Session = Depends(get_db)):
    """
    Get suggested questions for welcome screen.
    Returns cached suggestions or triggers async update if stale (>6 hours).
    For new users (no conversations), returns is_new_user=True.
    """
    from services.hyper_ai_service import get_or_update_suggestions
    return get_or_update_suggestions(db)

@router.get("/conversations")
def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """List recent conversations (excluding onboarding). Bot conversations pinned first."""
    conversations = db.query(HyperAiConversation).filter(
        HyperAiConversation.is_onboarding != True
    ).order_by(
        HyperAiConversation.is_bot_conversation.desc(),
        HyperAiConversation.updated_at.desc()
    ).limit(limit).all()

    return {
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "message_count": c.message_count,
                "is_bot_conversation": bool(c.is_bot_conversation),
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in conversations
        ]
    }


@router.post("/conversations")
def create_conversation(db: Session = Depends(get_db)):
    """Create a new conversation."""
    conv = get_or_create_conversation(db)
    return {
        "id": conv.id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None
    }


@router.get("/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """Get messages from a conversation with compression points and token usage."""
    from database.models import HyperAiConversation, HyperAiProfile
    from services.ai_context_compression_service import calculate_token_usage, restore_tool_calls_to_messages
    import json as json_module

    messages = get_conversation_messages(db, conversation_id, limit)

    # Get compression points from conversation
    conversation = db.query(HyperAiConversation).filter(
        HyperAiConversation.id == conversation_id
    ).first()

    compression_points = []
    if conversation and conversation.compression_points:
        try:
            compression_points = json_module.loads(conversation.compression_points)
        except (json_module.JSONDecodeError, TypeError):
            compression_points = []

    # Calculate token usage (only messages after compression point + summary)
    token_usage = None
    profile = db.query(HyperAiProfile).first()
    if profile and profile.llm_model and messages:
        from services.ai_context_compression_service import get_last_compression_point
        from database.models import HyperAiMessage
        llm_config = get_llm_config(db)
        api_format = llm_config.get("api_format", "openai")

        # Load ORM objects for id-based filtering
        cp = get_last_compression_point(conversation) if conversation else None
        cp_msg_id = cp.get("message_id", 0) if cp else 0

        history_orm = db.query(HyperAiMessage).filter(
            HyperAiMessage.conversation_id == conversation_id,
            HyperAiMessage.id > cp_msg_id
        ).order_by(HyperAiMessage.created_at).all()

        msg_dicts = [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls_log": m.tool_calls_log,
                "reasoning_snapshot": m.reasoning_snapshot,
            }
            for m in history_orm
        ]
        msg_list = restore_tool_calls_to_messages(msg_dicts, api_format, model=profile.llm_model or "")
        if cp and cp.get("summary"):
            msg_list.insert(0, {"role": "system", "content": cp["summary"]})
        token_usage = calculate_token_usage(msg_list, profile.llm_model)

    return {
        "messages": messages,
        "compression_points": compression_points,
        "token_usage": token_usage
    }


@router.post("/chat")
def start_chat(request: ChatRequest, db: Session = Depends(get_db)):
    """
    Start a chat with Hyper AI.
    Returns task_id for polling via /api/ai-stream/{task_id}.

    mode="onboarding" uses a special prompt for profile collection.
    """
    # Check LLM config
    llm_config = get_llm_config(db)
    if not llm_config.get("configured"):
        raise HTTPException(
            status_code=400,
            detail="LLM not configured. Please complete onboarding first."
        )

    # Get or create conversation (mark as onboarding if in onboarding mode)
    is_onboarding = request.mode == "onboarding"
    conv = get_or_create_conversation(db, request.conversation_id, is_onboarding=is_onboarding)

    # Start background task based on mode
    if is_onboarding:
        task_id = start_onboarding_chat_task(db, conv.id, request.message, request.lang)
    else:
        task_id = start_chat_task(db, conv.id, request.message, request.lang)

    return {
        "task_id": task_id,
        "conversation_id": conv.id
    }


@router.post("/confirm-tool")
def confirm_tool(request: ConfirmationRequest):
    """Submit a runtime checkpoint response for a pending Hyper AI tool call."""
    manager = get_buffer_manager()
    accepted = manager.submit_confirmation(
        request.task_id,
        request.confirmation_id,
        request.confirmed,
    )
    if not accepted:
        raise HTTPException(status_code=404, detail="No matching pending confirmation")
    return {"success": True}


@router.post("/insight")
def start_insight(request: InsightRequest, db: Session = Depends(get_db)):
    """Start a one-shot Insight analysis task without chat conversation persistence."""
    llm_config = get_llm_config(db)
    if not llm_config.get("configured"):
        raise HTTPException(
            status_code=400,
            detail="LLM not configured. Please complete onboarding first."
        )

    task_id = start_insight_task(
        db=db,
        context=request.context,
        selected_event=request.selected_event,
        lang=request.lang,
    )
    return {"task_id": task_id}


# Memory endpoints
@router.get("/memories")
def list_memories(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """List user memories, optionally filtered by category."""
    from services.hyper_ai_memory_service import get_memories, MEMORY_CATEGORIES

    if category and category not in MEMORY_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category. Valid: {MEMORY_CATEGORIES}")

    memories = get_memories(db, category=category, limit=limit)
    return {"memories": memories, "categories": MEMORY_CATEGORIES}


@router.delete("/memories/{memory_id}")
def delete_memory_endpoint(memory_id: int, db: Session = Depends(get_db)):
    """Delete (deactivate) a memory."""
    from services.hyper_ai_memory_service import delete_memory

    success = delete_memory(db, memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True}


# Skill endpoints
@router.get("/skills")
def list_skills(db: Session = Depends(get_db)):
    """List all available skills with their enabled/disabled status."""
    from services.hyper_ai_skill_engine import scan_all_skills, get_enabled_skills

    all_skills = scan_all_skills()
    profile = get_or_create_profile(db)
    enabled = get_enabled_skills(all_skills, profile.enabled_skills)
    enabled_names = {s["name"] for s in enabled}

    return {
        "skills": [
            {
                "name": s["name"],
                "description": s["description"],
                "description_zh": s.get("description_zh", ""),
                "command": f"/{s.get('shortcut') or s['name']}",
                "enabled": s["name"] in enabled_names,
            }
            for s in all_skills
        ]
    }


class SkillToggleRequest(BaseModel):
    enabled: bool


@router.put("/skills/{skill_name}/toggle")
def toggle_skill(skill_name: str, body: SkillToggleRequest, db: Session = Depends(get_db)):
    """Enable or disable a specific skill for the current user."""
    import json as _json
    from services.hyper_ai_skill_engine import scan_all_skills

    all_skills = scan_all_skills()
    valid_names = {s["name"] for s in all_skills}
    if skill_name not in valid_names:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    profile = get_or_create_profile(db)

    # Parse current enabled list (None = all enabled)
    if profile.enabled_skills is None:
        current = [s["name"] for s in all_skills]
    else:
        try:
            current = _json.loads(profile.enabled_skills)
        except (ValueError, TypeError):
            current = [s["name"] for s in all_skills]

    if body.enabled and skill_name not in current:
        current.append(skill_name)
    elif not body.enabled and skill_name in current:
        current.remove(skill_name)

    # If all skills enabled, store None (default behavior)
    if set(current) == valid_names:
        profile.enabled_skills = None
    else:
        profile.enabled_skills = _json.dumps(current)

    db.commit()
    return {"success": True, "skill_name": skill_name, "enabled": body.enabled}


# ── External Tool Configuration ──


@router.get("/tools")
def list_tools(db: Session = Depends(get_db)):
    """List all registered external tools with their config status."""
    from services.hyper_ai_tool_registry import (
        EXTERNAL_TOOL_REGISTRY, get_tool_configs,
    )

    configs = get_tool_configs(db)
    tools = []
    for name, meta in EXTERNAL_TOOL_REGISTRY.items():
        tool_cfg = configs.get(name, {})
        has_key = bool(tool_cfg.get("api_key_encrypted"))
        tools.append({
            "name": name,
            "display_name": meta["display_name"],
            "display_name_zh": meta.get("display_name_zh", meta["display_name"]),
            "description": meta["description"],
            "description_zh": meta.get("description_zh", meta["description"]),
            "icon": meta.get("icon", "wrench"),
            "config_fields": meta["config_fields"],
            "get_url": meta.get("get_url"),
            "get_url_label": meta.get("get_url_label"),
            "get_url_label_zh": meta.get("get_url_label_zh"),
            "configured": has_key,
            "enabled": tool_cfg.get("enabled", False),
        })
    return {"tools": tools}


class ToolConfigRequest(BaseModel):
    config: dict  # {"api_key": "tvly-xxx", ...}
    validate_key: bool = True


@router.put("/tools/{tool_name}/config")
async def save_tool_config(
    tool_name: str, body: ToolConfigRequest, db: Session = Depends(get_db)
):
    """Save configuration for an external tool. Optionally validates the key."""
    from services.hyper_ai_tool_registry import (
        EXTERNAL_TOOL_REGISTRY, TOOL_VALIDATORS, set_tool_api_key,
    )

    if tool_name not in EXTERNAL_TOOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    api_key = body.config.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required")

    # Optional validation
    if body.validate_key and tool_name in TOOL_VALIDATORS:
        ok, err = await TOOL_VALIDATORS[tool_name](api_key)
        if not ok:
            return {"success": False, "error": err}

    set_tool_api_key(db, tool_name, api_key)
    return {"success": True, "tool_name": tool_name}


@router.delete("/tools/{tool_name}/config")
def delete_tool_config(tool_name: str, db: Session = Depends(get_db)):
    """Remove configuration for an external tool."""
    from services.hyper_ai_tool_registry import (
        EXTERNAL_TOOL_REGISTRY, remove_tool_config,
    )

    if tool_name not in EXTERNAL_TOOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    remove_tool_config(db, tool_name)
    return {"success": True, "tool_name": tool_name}
