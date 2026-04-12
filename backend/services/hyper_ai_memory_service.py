"""
Hyper AI Memory Service - User insights and memory management

This module provides:
1. Memory storage and retrieval with automatic system prompt injection
2. Batch LLM-based deduplication (single call for all memories)
3. Memory categories for organized storage
4. Importance scoring and automatic limit enforcement (max 50)

Memory Categories:
- preference: User trading preferences and style
- decision: Important trading decisions made
- lesson: Lessons learned from trades
- insight: Market insights and observations
- context: General context about user's situation

Architecture:
- Memory is extracted during context compression (async, non-blocking)
- Batch deduplication: 1 LLM call handles all new memories vs all existing
- Memories auto-injected into system prompt alongside user profile
- user_info category (from onboarding) is excluded from dedup/eviction
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy.orm import Session

from database.models import HyperAiMemory

logger = logging.getLogger(__name__)

# Memory capacity limit
MAX_MEMORIES = 50

# Memory categories
MEMORY_CATEGORIES = [
    "preference",  # Trading preferences
    "decision",    # Important decisions
    "lesson",      # Lessons learned
    "insight",     # Market insights
    "context",     # General context
]


def get_memories(
    db: Session,
    category: Optional[str] = None,
    limit: int = 20,
    active_only: bool = True
) -> List[Dict[str, Any]]:
    """
    Retrieve memories, optionally filtered by category.

    Args:
        db: Database session
        category: Filter by category (None for all)
        limit: Maximum number of memories to return
        active_only: Only return active memories

    Returns:
        List of memory dictionaries
    """
    query = db.query(HyperAiMemory)

    if active_only:
        query = query.filter(HyperAiMemory.is_active == True)

    if category:
        query = query.filter(HyperAiMemory.category == category)

    # Order by importance and recency
    memories = query.order_by(
        HyperAiMemory.importance.desc(),
        HyperAiMemory.created_at.desc()
    ).limit(limit).all()

    return [
        {
            "id": m.id,
            "category": m.category,
            "content": m.content,
            "source": m.source,
            "importance": m.importance,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in memories
    ]


def add_memory(
    db: Session,
    category: str,
    content: str,
    source: str = "conversation",
    importance: float = 0.5
) -> HyperAiMemory:
    """
    Add a new memory entry.

    Args:
        db: Database session
        category: Memory category
        content: Memory content
        source: Source of the memory (conversation, compression, manual)
        importance: Importance score (0.0 to 1.0)

    Returns:
        Created memory object
    """
    memory = HyperAiMemory(
        category=category,
        content=content,
        source=source,
        importance=importance,
        is_active=True
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def update_memory(
    db: Session,
    memory_id: int,
    content: Optional[str] = None,
    importance: Optional[float] = None,
    is_active: Optional[bool] = None
) -> Optional[HyperAiMemory]:
    """Update an existing memory."""
    memory = db.query(HyperAiMemory).filter(HyperAiMemory.id == memory_id).first()
    if not memory:
        return None

    if content is not None:
        memory.content = content
    if importance is not None:
        memory.importance = importance
    if is_active is not None:
        memory.is_active = is_active

    memory.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(memory)
    return memory


def delete_memory(db: Session, memory_id: int) -> bool:
    """Soft delete a memory by marking it inactive."""
    memory = db.query(HyperAiMemory).filter(HyperAiMemory.id == memory_id).first()
    if not memory:
        return False

    memory.is_active = False
    memory.updated_at = datetime.utcnow()
    db.commit()
    return True


# Batch deduplication prompt - handles all new memories in a single LLM call
BATCH_DEDUP_PROMPT = """You are a memory deduplication assistant for a crypto trading AI.

## Existing Memories (already stored):
{existing_memories}

## New Memories (candidates to add):
{new_memories}

For EACH new memory, decide ONE action by comparing against ALL existing memories:
- ADD: New memory is different and valuable, add it
- UPDATE: New memory refines/updates an existing one. Provide existing_id and merged content
- DELETE: New memory contradicts/replaces an existing one. Provide existing_id to delete, then add new
- NONE: New memory is redundant/duplicate of existing, discard it

Respond in JSON only:
{{"actions": [
  {{"new_index": 0, "action": "ADD"}},
  {{"new_index": 1, "action": "UPDATE", "existing_id": 4, "merged": "merged content here"}},
  {{"new_index": 2, "action": "NONE"}},
  {{"new_index": 3, "action": "DELETE", "existing_id": 8}}
]}}"""


def batch_dedup_memories(
    db: Session,
    new_memories: List[Dict[str, Any]],
    api_config: Dict[str, Any],
    source: str = "compression"
) -> int:
    """
    Batch deduplication: compare all new memories against all existing in 1 LLM call.
    Replaces the old per-memory dedup loop.

    Args:
        new_memories: List of {"category", "content", "importance"} dicts
        api_config: LLM config
        source: Memory source tag

    Returns:
        Number of memories added/updated
    """
    if not new_memories:
        return 0

    # Get all active memories (exclude user_info from onboarding)
    existing = get_memories(db, limit=MAX_MEMORIES)
    existing = [m for m in existing if m.get("category") != "user_info"]

    # If no existing memories, just add all
    if not existing:
        count = 0
        for mem in new_memories:
            cat = mem.get("category", "context")
            if cat not in MEMORY_CATEGORIES or not mem.get("content"):
                continue
            add_memory(db, cat, mem["content"], source, mem.get("importance", 0.5))
            count += 1
        enforce_memory_limit(db)
        return count

    # Build prompt with existing and new memories
    existing_text = "\n".join(
        f"[ID:{m['id']}] ({m['category']}) {m['content']}"
        for m in existing
    )
    new_text = "\n".join(
        f"[{i}] ({m.get('category','context')}) {m.get('content','')}"
        for i, m in enumerate(new_memories)
    )

    prompt = BATCH_DEDUP_PROMPT.format(
        existing_memories=existing_text,
        new_memories=new_text
    )

    # Single LLM call for all dedup decisions
    actions = _call_llm_for_dedup(prompt, api_config)
    if actions is None:
        # LLM failed, fallback: add all as new
        logger.warning("[Memory] Batch dedup LLM failed, adding all as new")
        count = 0
        for mem in new_memories:
            cat = mem.get("category", "context")
            if cat not in MEMORY_CATEGORIES or not mem.get("content"):
                continue
            add_memory(db, cat, mem["content"], source, mem.get("importance", 0.5))
            count += 1
        enforce_memory_limit(db)
        return count

    # Execute actions
    count = 0
    for act in actions:
        idx = act.get("new_index")
        if idx is None or idx >= len(new_memories):
            continue
        mem = new_memories[idx]
        cat = mem.get("category", "context")
        content = mem.get("content", "")
        importance = mem.get("importance", 0.5)
        if not content or cat not in MEMORY_CATEGORIES:
            continue

        action = act.get("action", "ADD").upper()

        if action == "ADD":
            add_memory(db, cat, content, source, importance)
            count += 1
        elif action == "UPDATE":
            eid = act.get("existing_id")
            merged = act.get("merged") or content
            if eid:
                old = next((m for m in existing if m["id"] == eid), None)
                old_imp = old.get("importance", 0.5) if old else 0.5
                update_memory(db, eid, content=merged, importance=max(importance, old_imp))
                count += 1
            else:
                add_memory(db, cat, content, source, importance)
                count += 1
        elif action == "DELETE":
            eid = act.get("existing_id")
            if eid:
                delete_memory(db, eid)
            add_memory(db, cat, content, source, importance)
            count += 1
        # NONE: discard, do nothing

    enforce_memory_limit(db)
    return count


def _call_llm_for_dedup(
    prompt: str,
    api_config: Dict[str, Any]
) -> Optional[List[Dict[str, Any]]]:
    """
    Single LLM call for batch deduplication. Returns list of action dicts or None on failure.
    """
    base_url = api_config.get("base_url", "")
    api_key = api_config.get("api_key", "")
    model = api_config.get("model", "")
    api_format = api_config.get("api_format", "openai")

    if not all([base_url, api_key, model]):
        logger.warning("[Memory] Incomplete API config for dedup")
        return None

    try:
        from services.ai_decision_service import (
            build_chat_completion_endpoints, build_llm_payload, build_llm_headers
        )
        endpoints = build_chat_completion_endpoints(base_url, model)
        if api_format == "anthropic":
            endpoint = endpoints[0] if endpoints else f"{base_url.rstrip('/')}/messages"
        else:
            endpoint = endpoints[0] if endpoints else f"{base_url}/chat/completions"

        headers = build_llm_headers(api_format, api_key, base_url)
        body = build_llm_payload(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_format=api_format,
            max_tokens=800,
            temperature=None,
        )

        response = requests.post(endpoint, headers=headers, json=body, timeout=60)

        if response.status_code != 200:
            logger.warning(
                f"[Memory] Dedup API error: status={response.status_code}, "
                f"body={response.text[:500]}"
            )
            return None

        data = response.json()

        if api_format == "anthropic":
            content = data.get("content", [])
            text = content[0].get("text", "") if content else ""
        else:
            choices = data.get("choices", [])
            text = choices[0].get("message", {}).get("content", "") if choices else ""

        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return result.get("actions", [])

        logger.warning(f"[Memory] Dedup response not valid JSON: {text[:200]}")
        return None

    except requests.exceptions.Timeout:
        logger.warning("[Memory] Dedup API timeout (60s)")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"[Memory] Dedup API connection error: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"[Memory] Dedup response JSON parse error: {e}")
        return None
    except Exception as e:
        logger.warning(f"[Memory] Dedup unexpected error: {type(e).__name__}: {e}")
        return None


def enforce_memory_limit(db: Session) -> int:
    """
    Enforce MAX_MEMORIES limit by soft-deleting lowest importance memories.
    Excludes user_info category (managed by onboarding).
    Returns number of memories evicted.
    """
    active_count = db.query(HyperAiMemory).filter(
        HyperAiMemory.is_active == True,
        HyperAiMemory.category != "user_info"
    ).count()

    if active_count <= MAX_MEMORIES:
        return 0

    excess = active_count - MAX_MEMORIES
    # Get lowest importance memories to evict
    to_evict = db.query(HyperAiMemory).filter(
        HyperAiMemory.is_active == True,
        HyperAiMemory.category != "user_info"
    ).order_by(
        HyperAiMemory.importance.asc(),
        HyperAiMemory.created_at.asc()
    ).limit(excess).all()

    for m in to_evict:
        m.is_active = False
        m.updated_at = datetime.utcnow()

    db.commit()
    logger.warning(f"[Memory] Evicted {len(to_evict)} memories (limit={MAX_MEMORIES})")
    return len(to_evict)


# Memory extraction prompt for compression
EXTRACT_MEMORIES_PROMPT = """You are a memory extraction assistant for a crypto trading AI platform.
Analyze this conversation and extract key user insights worth remembering long-term.

Conversation:
{conversation}

## Categories and what to extract:

**preference** (importance 0.7-0.9):
- Trading style (scalping, swing, intraday), risk tolerance, leverage preferences
- Preferred coins/pairs, timeframes, position sizing rules
- Daily routines (e.g. close all positions before UTC 23:30)

**decision** (importance 0.6-0.8):
- Strategy parameters chosen (e.g. EMA periods, RSI thresholds, TP/SL percentages)
- Specific trading rules or conditions the user confirmed
- Configuration changes (e.g. switched model, changed leverage from 5x to 3x)

**lesson** (importance 0.7-0.9):
- Losses or mistakes and what the user learned
- What worked well and why
- Market behavior patterns the user identified

**insight** (importance 0.5-0.7):
- Market observations (e.g. "BTC tends to dump after funding rate > 0.1%")
- Correlations or patterns discussed
- Backtesting results and conclusions

## Rules:
- Each memory should be specific and self-contained (readable without context)
- Include concrete numbers/parameters when available
- Max 5 memories per extraction, only truly important ones
- If nothing significant, return empty list

Respond in JSON:
{{"memories": [{{"category": "...", "content": "...", "importance": 0.8}}]}}"""


def extract_memories_from_conversation(
    conversation_text: str,
    api_config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Extract memories from conversation using LLM.

    Returns:
        List of {"category", "content", "importance"} dicts
    """
    prompt = EXTRACT_MEMORIES_PROMPT.format(
        conversation=conversation_text[:6000]
    )

    base_url = api_config.get("base_url", "")
    api_key = api_config.get("api_key", "")
    model = api_config.get("model", "")
    api_format = api_config.get("api_format", "openai")

    if not all([base_url, api_key, model]):
        return []

    try:
        from services.ai_decision_service import build_chat_completion_endpoints, build_llm_payload, build_llm_headers

        endpoints = build_chat_completion_endpoints(base_url, model)
        if api_format == "anthropic":
            endpoint = endpoints[0] if endpoints else f"{base_url.rstrip('/')}/messages"
        else:
            endpoint = endpoints[0] if endpoints else f"{base_url}/chat/completions"

        # Use unified headers/payload builders (see build_llm_payload in ai_decision_service)
        headers = build_llm_headers(api_format, api_key, base_url)
        body = build_llm_payload(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_format=api_format,
            max_tokens=500,
            temperature=None,
        )

        response = requests.post(endpoint, headers=headers, json=body, timeout=60)

        if response.status_code != 200:
            logger.warning(
                f"[Memory] Extraction API error: status={response.status_code}, "
                f"body={response.text[:500]}"
            )
            return []

        data = response.json()

        # Extract response text
        if api_format == "anthropic":
            content = data.get("content", [])
            text = content[0].get("text", "") if content else ""
        else:
            choices = data.get("choices", [])
            text = choices[0].get("message", {}).get("content", "") if choices else ""

        # Parse JSON response
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return result.get("memories", [])

    except requests.exceptions.Timeout:
        logger.warning("[Memory] Extraction API timeout (60s)")
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"[Memory] Extraction API connection error: {e}")
    except json.JSONDecodeError as e:
        logger.warning(f"[Memory] Extraction response JSON parse error: {e}")
    except Exception as e:
        logger.warning(f"[Memory] Extraction unexpected error: {type(e).__name__}: {e}")

    return []


def process_compression_memories(
    db: Session,
    conversation_text: str,
    api_config: Dict[str, Any]
) -> int:
    """
    Extract and store memories during context compression.
    Uses batch dedup: 1 LLM call for extraction + 1 for dedup = 2 total.

    Returns:
        Number of memories added/updated
    """
    memories = extract_memories_from_conversation(conversation_text, api_config)

    if not memories:
        return 0

    # Filter valid memories
    valid = [
        m for m in memories
        if m.get("content") and m.get("category", "context") in MEMORY_CATEGORIES
    ]

    if not valid:
        return 0

    count = batch_dedup_memories(db, valid, api_config, source="compression")
    logger.warning(f"[Memory] Processed {count} memories from compression (batch mode)")
    return count
