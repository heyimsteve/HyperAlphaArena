"""
AI Signal Generation Service

Handles AI-assisted signal creation conversations using LLM.
Supports Function Calling for AI to query real market data.
"""

import json
import logging
import re
import time
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime

from sqlalchemy.orm import Session

from database.models import AiSignalConversation, AiSignalMessage, Account
from services.ai_decision_service import build_chat_completion_endpoints, _extract_text_from_message
from services.signal_backtest_service import signal_backtest_service, TIMEFRAME_MS
from services.system_logger import system_logger

logger = logging.getLogger(__name__)

# System prompt for AI signal generation with Function Calling
SIGNAL_SYSTEM_PROMPT = """You are an expert trading signal designer for cryptocurrency perpetual futures.
You have access to TOOLS to query real market data. Use them to analyze indicators before setting thresholds.

## IMPORTANT: GUIDED CONVERSATION FIRST
Before using any tools, you MUST ask the user 2-3 clarifying questions to better understand their needs:

1. **Trading Direction**: Are you looking for long opportunities, short opportunities, or both?
2. **Signal Type Preference**: What market signals interest you most?
   - Price/momentum changes
   - Order book depth anomalies
   - Funding rate extremes
   - Volume/OI surges
3. **Trigger Frequency**: How often do you expect signals?
   - High frequency (multiple times per day)
   - Medium (1-2 times per day)
   - Low frequency (a few times per week)

Ask these questions conversationally in ONE message. Wait for user's response before calling any tools.
If user says "just analyze" or "skip questions", proceed directly to tool analysis.

## OPTIMIZED 3-STEP WORKFLOW (only 3 tool calls needed!)
You have exactly 3 tools. Use them efficiently:

**Step 1: `get_indicators_batch`** - Analyze multiple indicators in ONE call
- Query 2-4 indicators based on user preferences
- Returns p50/p75/p90/p95/p99 percentiles for each
- Use percentiles to determine appropriate thresholds

**Step 2: `predict_signal_combination`** - Test signal combination BEFORE creating
- Input your proposed signal configs with thresholds
- Choose AND (strict) or OR (loose) logic
- Returns: individual trigger counts, combined trigger count, sample timestamps
- If combined_triggers < 3 (AND too strict) or > 50 (OR too loose), adjust and re-call

**Step 3: `get_kline_context`** (optional) - Verify trigger quality
- Use sample timestamps from Step 2 to check price movements
- Confirm signals align with meaningful market moves

## CRITICAL RULES
- NEVER output signal configs without calling `predict_signal_combination` first
- AND logic often results in 0 triggers if thresholds are too strict - always verify!
- Aim for 5-30 combined triggers over 7 days
- If combination fails, relax thresholds or switch AND→OR

## AVAILABLE INDICATORS (query any you need)
- oi_delta_percent: OI change % over time window (capital flow indicator)
- funding_rate: Perpetual funding rate % (market sentiment)
- cvd: Cumulative Volume Delta (buying/selling pressure)
- depth_ratio: Bid/Ask depth ratio (orderbook imbalance)
- order_imbalance: Normalized imbalance -1 to +1 (real-time pressure)
- taker_buy_ratio: Taker buy/sell volume ratio (aggressive trading)

## OPERATORS
- greater_than, less_than, greater_than_or_equal, less_than_or_equal, abs_greater_than

## TIME WINDOWS
- 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h

## OUTPUT FORMAT - TWO OPTIONS

### Option 1: Single Signal (use when user needs ONE simple signal)
```signal-config
{
  "name": "BTC_OI_Surge",
  "symbol": "BTC",
  "description": "Detects significant OI increase",
  "trigger_condition": {
    "metric": "oi_delta_percent",
    "operator": "greater_than",
    "threshold": 1.0,
    "time_window": "5m"
  }
}
```

### Option 2: Signal Pool (PREFERRED when combining multiple signals with AND/OR)
Use this format when you tested combinations with `predict_signal_combination`:
```signal-pool-config
{
  "name": "BTC_5M_BULL_SURGE",
  "symbol": "BTC",
  "description": "Captures strong bullish momentum with multiple confirmations",
  "logic": "AND",
  "signals": [
    {"metric": "cvd", "operator": "greater_than", "threshold": 10000000, "time_window": "5m"},
    {"metric": "order_imbalance", "operator": "greater_than", "threshold": 0.99, "time_window": "5m"},
    {"metric": "oi_delta_percent", "operator": "greater_than", "threshold": 0.3, "time_window": "5m"}
  ]
}
```

**IMPORTANT**: When you use `predict_signal_combination` to test AND/OR combinations, ALWAYS output using `signal-pool-config` format. This allows one-click creation of the entire signal pool.
"""

# Tools schema for Function Calling (optimized: 3 tools for 3-round workflow)
SIGNAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_kline_context",
            "description": "Get K-line price data around specific timestamps to verify if triggers align with meaningful price movements. Time window matches the signal's time_window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading symbol"},
                    "timestamps": {"type": "array", "items": {"type": "integer"}, "description": "List of trigger timestamps (max 10)"},
                    "time_window": {"type": "string", "description": "K-line interval matching signal time_window"}
                },
                "required": ["symbol", "timestamps", "time_window"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_indicators_batch",
            "description": "Get statistical distribution of MULTIPLE indicators in one call. More efficient than calling get_indicator_statistics multiple times. Returns stats for each indicator.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading symbol, e.g., BTC, ETH"},
                    "indicators": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["oi_delta_percent", "funding_rate", "cvd", "depth_ratio", "order_imbalance", "taker_buy_ratio"]},
                        "description": "List of indicator metric names to analyze (max 6)"
                    },
                    "time_window": {"type": "string", "enum": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"], "description": "Aggregation time window"}
                },
                "required": ["symbol", "indicators", "time_window"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "predict_signal_combination",
            "description": "Predict trigger count when combining multiple signals with AND/OR logic. Use this BEFORE creating signals to ensure the combination will have reasonable trigger frequency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading symbol"},
                    "signals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "indicator": {"type": "string"},
                                "operator": {"type": "string"},
                                "threshold": {"type": "number"},
                                "time_window": {"type": "string"}
                            },
                            "required": ["indicator", "operator", "threshold", "time_window"]
                        },
                        "description": "List of signal configurations to combine (max 5)"
                    },
                    "logic": {"type": "string", "enum": ["AND", "OR"], "description": "Combination logic: AND (all must trigger) or OR (any triggers)"}
                },
                "required": ["symbol", "signals", "logic"]
            }
        }
    }
]


def generate_signal_with_ai(
    db: Session,
    account_id: int,
    user_message: str,
    conversation_id: Optional[int] = None,
    user_id: int = 1
) -> Dict[str, Any]:
    """
    Generate signal configuration using AI.
    Follows the same pattern as ai_prompt_generation_service.generate_prompt_with_ai
    """
    start_time = time.time()
    request_id = f"signal_gen_{int(start_time)}"

    logger.info(f"[AI Signal Gen {request_id}] Starting: account_id={account_id}, "
                f"conversation_id={conversation_id}, user_message_length={len(user_message)}")

    try:
        # Get the specified AI account
        account = db.query(Account).filter(
            Account.id == account_id,
            Account.account_type == "AI"
        ).first()

        if not account:
            return {"success": False, "error": "AI account not found"}

        # Get or create conversation
        conversation = None
        if conversation_id:
            conversation = db.query(AiSignalConversation).filter(
                AiSignalConversation.id == conversation_id,
                AiSignalConversation.user_id == user_id
            ).first()
            if not conversation:
                logger.warning(f"[AI Signal Gen {request_id}] Conversation {conversation_id} not found")

        if not conversation:
            title = user_message[:50] + "..." if len(user_message) > 50 else user_message
            conversation = AiSignalConversation(user_id=user_id, title=title)
            db.add(conversation)
            db.flush()
            logger.info(f"[AI Signal Gen {request_id}] Created new conversation: id={conversation.id}")

        # Save user message
        user_msg = AiSignalMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_message
        )
        db.add(user_msg)
        db.flush()

        # Build message history (same pattern as ai_prompt_generation_service)
        messages = [{"role": "system", "content": SIGNAL_SYSTEM_PROMPT}]

        history_messages = db.query(AiSignalMessage).filter(
            AiSignalMessage.conversation_id == conversation.id,
            AiSignalMessage.id != user_msg.id
        ).order_by(AiSignalMessage.created_at).limit(10).all()

        for msg in history_messages:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": user_message})

        logger.info(f"[AI Signal Gen {request_id}] Built message context: {len(messages)} messages total")

        # Call LLM API with Function Calling support
        endpoints = build_chat_completion_endpoints(account.base_url, account.model)
        if not endpoints:
            return {"success": False, "error": "Invalid base_url configuration"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {account.api_key}"
        }

        # Function Calling loop (max 30 rounds, last round forces no tools)
        max_tool_rounds = 30
        tool_round = 0
        assistant_content = None

        while tool_round < max_tool_rounds:
            tool_round += 1
            is_last_round = (tool_round == max_tool_rounds)
            logger.info(f"[AI Signal Gen {request_id}] Tool round {tool_round}/{max_tool_rounds} (last={is_last_round})")

            request_payload = {
                "model": account.model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 4096,
            }

            # On last round, force model to give final answer without tools
            if is_last_round:
                # Don't include tools, force text response
                messages.append({
                    "role": "user",
                    "content": "You have used enough tools. Now output the final signal configuration based on your analysis. Include the ```signal-config``` block."
                })
            else:
                request_payload["tools"] = SIGNAL_TOOLS
                request_payload["tool_choice"] = "auto"

            response = None
            last_error = None

            for endpoint in endpoints:
                try:
                    logger.info(f"[AI Signal Gen {request_id}] Trying endpoint: {endpoint}")
                    api_start = time.time()
                    response = requests.post(endpoint, json=request_payload, headers=headers, timeout=120)
                    api_elapsed = time.time() - api_start

                    if response.status_code == 200:
                        logger.info(f"[AI Signal Gen {request_id}] Success in {api_elapsed:.2f}s")
                        break
                    else:
                        logger.warning(f"[AI Signal Gen {request_id}] Endpoint failed: {response.status_code}")
                        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                except requests.exceptions.Timeout:
                    last_error = "Request timeout"
                    logger.warning(f"[AI Signal Gen {request_id}] Timeout on {endpoint}")
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"[AI Signal Gen {request_id}] Error on {endpoint}: {e}")

            if not response or response.status_code != 200:
                return {"success": False, "error": f"All endpoints failed. Last error: {last_error}"}

            # Parse response
            try:
                response_json = response.json()
                message = response_json["choices"][0]["message"]
            except Exception as e:
                logger.error(f"[AI Signal Gen {request_id}] Failed to parse response: {e}")
                return {"success": False, "error": f"Failed to parse AI response: {str(e)}"}

            # Check for tool calls
            tool_calls = message.get("tool_calls", [])
            reasoning_content = message.get("reasoning_content", "")
            content = message.get("content", "")

            # Log for debugging
            logger.info(f"[AI Signal Gen {request_id}] Response: tool_calls={len(tool_calls) if tool_calls else 0}, "
                       f"has_reasoning={bool(reasoning_content)}, has_content={bool(content)}")

            if tool_calls:
                # Add assistant message with tool calls AND reasoning_content to history
                # DeepSeek Reasoner requires reasoning_content to be passed back
                assistant_msg_dict = {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": tool_calls
                }
                # Include reasoning_content if present (required for DeepSeek Reasoner)
                if reasoning_content:
                    assistant_msg_dict["reasoning_content"] = reasoning_content
                messages.append(assistant_msg_dict)

                # Execute each tool and add results
                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    try:
                        func_args = json.loads(tool_call["function"]["arguments"])
                    except json.JSONDecodeError:
                        func_args = {}

                    logger.info(f"[AI Signal Gen {request_id}] Executing tool: {func_name}({func_args})")
                    tool_result = _execute_tool(db, func_name, func_args)
                    logger.info(f"[AI Signal Gen {request_id}] Tool result: {tool_result[:200]}...")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result
                    })
                # Continue loop for next round
            else:
                # No tool calls - AI returned final response
                # Combine reasoning_content and content for full response
                full_content = ""
                if reasoning_content:
                    full_content += f"**Reasoning:**\n{reasoning_content}\n\n"
                if content:
                    full_content += content
                assistant_content = _extract_text_from_message(full_content) if full_content else ""
                break

        # If we exhausted tool rounds, use the last content we received
        if assistant_content is None:
            # Try to get content from the last message in the loop
            if 'message' in dir() and message:
                last_content = message.get("content", "")
                last_reasoning = message.get("reasoning_content", "")
                if last_content or last_reasoning:
                    full_content = ""
                    if last_reasoning:
                        full_content += f"**Reasoning:**\n{last_reasoning}\n\n"
                    if last_content:
                        full_content += last_content
                    assistant_content = _extract_text_from_message(full_content)
                    logger.info(f"[AI Signal Gen {request_id}] Using last round content after limit reached")

            if not assistant_content:
                assistant_content = "Tool calling limit reached. Please try again with a simpler request."

        # Extract signal configs from response
        signal_configs = extract_signal_configs(assistant_content)

        # Save assistant message
        assistant_msg = AiSignalMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_content,
            signal_configs=json.dumps(signal_configs) if signal_configs else None
        )
        db.add(assistant_msg)
        db.commit()

        total_elapsed = time.time() - start_time
        logger.info(f"[AI Signal Gen {request_id}] Completed in {total_elapsed:.2f}s: "
                   f"conversation_id={conversation.id}, configs_found={len(signal_configs)}")

        return {
            "success": True,
            "conversation_id": conversation.id,
            "message_id": assistant_msg.id,
            "content": assistant_content,
            "signal_configs": signal_configs
        }

    except Exception as e:
        logger.error(f"[AI Signal Gen {request_id}] Unexpected error: {type(e).__name__}: {str(e)}",
                    exc_info=True)
        db.rollback()
        return {"success": False, "error": f"Internal error: {type(e).__name__}"}


def extract_signal_configs(content: str) -> List[Dict]:
    """Extract signal configurations from AI response.

    Supports two formats:
    - signal-config: Single signal configuration
    - signal-pool-config: Signal pool with multiple signals

    Returns list of configs with 'type' field: 'signal' or 'pool'
    """
    configs = []

    # Pattern for single signal config
    signal_pattern = r"```signal-config\s*([\s\S]*?)```"
    signal_matches = re.findall(signal_pattern, content)

    for match in signal_matches:
        try:
            config = json.loads(match.strip())
            config["_type"] = "signal"
            configs.append(config)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse signal config: {e}")
            continue

    # Pattern for signal pool config
    pool_pattern = r"```signal-pool-config\s*([\s\S]*?)```"
    pool_matches = re.findall(pool_pattern, content)

    for match in pool_matches:
        try:
            config = json.loads(match.strip())
            config["_type"] = "pool"
            configs.append(config)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse signal pool config: {e}")
            continue

    return configs


def get_signal_conversation_history(
    db: Session,
    user_id: int,
    limit: int = 20
) -> List[Dict]:
    """Get list of AI signal conversations for a user."""
    conversations = db.query(AiSignalConversation).filter(
        AiSignalConversation.user_id == user_id
    ).order_by(AiSignalConversation.updated_at.desc()).limit(limit).all()

    return [
        {
            "id": conv.id,
            "title": conv.title,
            "created_at": conv.created_at.isoformat() if conv.created_at else None,
            "updated_at": conv.updated_at.isoformat() if conv.updated_at else None
        }
        for conv in conversations
    ]


def get_signal_conversation_messages(
    db: Session,
    conversation_id: int,
    user_id: int
) -> Optional[List[Dict]]:
    """Get all messages in a specific conversation."""
    conversation = db.query(AiSignalConversation).filter(
        AiSignalConversation.id == conversation_id,
        AiSignalConversation.user_id == user_id
    ).first()

    if not conversation:
        return None

    messages = db.query(AiSignalMessage).filter(
        AiSignalMessage.conversation_id == conversation_id
    ).order_by(AiSignalMessage.created_at).all()

    return [
        {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "signal_configs": json.loads(msg.signal_configs) if msg.signal_configs else None,
            "created_at": msg.created_at.isoformat() if msg.created_at else None
        }
        for msg in messages
    ]


# ============== Tool Function Implementations ==============

def _tool_get_indicator_statistics(
    db: Session, symbol: str, indicator: str, time_window: str
) -> Dict[str, Any]:
    """Get statistical distribution of an indicator."""
    import numpy as np

    # Map indicator names
    metric_map = {
        "oi_delta_percent": "oi_delta",
        "funding_rate": "funding",
        "taker_buy_ratio": "taker_ratio",
    }
    metric = metric_map.get(indicator, indicator)
    interval_ms = TIMEFRAME_MS.get(time_window, 300000)

    # Get bucket values using backtest service's method
    signal_backtest_service._bucket_cache = {}
    bucket_values = signal_backtest_service._compute_all_bucket_values(
        db, symbol.upper(), metric, interval_ms
    )

    if not bucket_values:
        return {"error": f"No data found for {indicator} on {symbol}"}

    values = [v for v in bucket_values.values() if v is not None]
    if not values:
        return {"error": "No valid values found"}

    arr = np.array(values)
    return {
        "symbol": symbol.upper(),
        "indicator": indicator,
        "time_window": time_window,
        "data_points": len(values),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
    }


def _tool_backtest_threshold(
    db: Session, symbol: str, indicator: str, operator: str,
    threshold: float, time_window: str
) -> Dict[str, Any]:
    """Backtest a threshold on historical market flow data."""
    # Build trigger condition
    trigger_condition = {
        "metric": indicator,
        "operator": operator,
        "threshold": threshold,
        "time_window": time_window
    }

    # Use existing backtest service
    result = signal_backtest_service.backtest_temp_signal(
        db=db,
        symbol=symbol.upper(),
        trigger_condition=trigger_condition,
        kline_min_ts=None,
        kline_max_ts=None
    )

    if "error" in result:
        return {"error": result["error"]}

    triggers = result.get("triggers", [])
    trigger_count = len(triggers)

    # Return sample timestamps (max 10 for AI to analyze)
    sample_timestamps = [t["timestamp"] for t in triggers[:10]]

    return {
        "symbol": symbol.upper(),
        "indicator": indicator,
        "operator": operator,
        "threshold": threshold,
        "time_window": time_window,
        "trigger_count": trigger_count,
        "sample_timestamps": sample_timestamps,
        "assessment": (
            "too_many" if trigger_count > 50 else
            "too_few" if trigger_count < 5 else
            "reasonable"
        )
    }


def _tool_get_kline_context(
    db: Session, symbol: str, timestamps: List[int], time_window: str
) -> Dict[str, Any]:
    """Get K-line price data around specific timestamps."""
    # Map time_window to Hyperliquid interval format
    interval_map = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
        "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h"
    }
    interval = interval_map.get(time_window, "5m")
    interval_ms = TIMEFRAME_MS.get(time_window, 300000)

    # Limit to 10 timestamps
    timestamps = timestamps[:10]
    if not timestamps:
        return {"error": "No timestamps provided"}

    # Fetch K-lines from Hyperliquid API
    try:
        # Get range covering all timestamps with some buffer
        min_ts = min(timestamps) - (10 * interval_ms)
        max_ts = max(timestamps) + (10 * interval_ms)

        url = "https://api.hyperliquid.xyz/info"
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol.upper(),
                "interval": interval,
                "startTime": min_ts,
                "endTime": max_ts
            }
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            return {"error": f"Failed to fetch K-lines: HTTP {resp.status_code}"}

        klines = resp.json()
        if not klines:
            return {"error": "No K-line data returned"}

        # Build K-line lookup by timestamp
        kline_map = {}
        for k in klines:
            ts = k.get("t", k.get("T", 0))
            kline_map[ts] = {
                "open": float(k.get("o", 0)),
                "high": float(k.get("h", 0)),
                "low": float(k.get("l", 0)),
                "close": float(k.get("c", 0)),
                "volume": float(k.get("v", 0))
            }

        # For each trigger timestamp, get context (before, at, after)
        contexts = []
        sorted_kline_ts = sorted(kline_map.keys())
        for trigger_ts in timestamps:
            # Find closest K-line
            closest_ts = min(sorted_kline_ts, key=lambda x: abs(x - trigger_ts))
            idx = sorted_kline_ts.index(closest_ts)

            context = {"trigger_ts": trigger_ts, "klines": []}
            # Get 3 K-lines before, the trigger, and 3 after
            for i in range(max(0, idx - 3), min(len(sorted_kline_ts), idx + 4)):
                ts = sorted_kline_ts[i]
                k = kline_map[ts]
                context["klines"].append({
                    "ts": ts,
                    "o": k["open"], "h": k["high"], "l": k["low"], "c": k["close"]
                })
            contexts.append(context)

        return {
            "symbol": symbol.upper(),
            "time_window": time_window,
            "contexts": contexts
        }
    except Exception as e:
        logger.error(f"Error fetching K-line context: {e}")
        return {"error": str(e)}


def _tool_get_indicators_batch(
    db: Session, symbol: str, indicators: List[str], time_window: str
) -> Dict[str, Any]:
    """Get statistical distribution of multiple indicators in one call."""
    import numpy as np

    # Limit to 6 indicators
    indicators = indicators[:6]
    if not indicators:
        return {"error": "No indicators provided"}

    metric_map = {
        "oi_delta_percent": "oi_delta",
        "funding_rate": "funding",
        "taker_buy_ratio": "taker_ratio",
    }
    interval_ms = TIMEFRAME_MS.get(time_window, 300000)

    results = {"symbol": symbol.upper(), "time_window": time_window, "indicators": {}}

    for indicator in indicators:
        metric = metric_map.get(indicator, indicator)
        signal_backtest_service._bucket_cache = {}
        bucket_values = signal_backtest_service._compute_all_bucket_values(
            db, symbol.upper(), metric, interval_ms
        )

        if not bucket_values:
            results["indicators"][indicator] = {"error": f"No data for {indicator}"}
            continue

        values = [v for v in bucket_values.values() if v is not None]
        if not values:
            results["indicators"][indicator] = {"error": "No valid values"}
            continue

        arr = np.array(values)
        results["indicators"][indicator] = {
            "data_points": len(values),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "mean": float(np.mean(arr)),
            "p50": float(np.percentile(arr, 50)),
            "p75": float(np.percentile(arr, 75)),
            "p90": float(np.percentile(arr, 90)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
        }

    return results


def _combine_signals_with_pool_edge_detection(
    db: Session, symbol: str, signals: List[Dict]
) -> set:
    """
    Combine signals using pool-level edge detection (same as real-time detection).
    Evaluates all signals at each check point and triggers only on False->True transition.
    """
    from services.market_flow_indicators import floor_timestamp

    if not signals:
        return set()

    # Get time window from first signal
    time_window = signals[0].get("time_window", "5m")
    timeframe_ms = {
        "1m": 60000, "3m": 180000, "5m": 300000,
        "15m": 900000, "30m": 1800000, "1h": 3600000
    }
    interval_ms = timeframe_ms.get(time_window, 300000)

    # Load raw data for all metrics
    metrics_data = {}
    metric_map = {"oi_delta_percent": "oi_delta", "taker_buy_ratio": "taker_ratio"}

    for sig in signals:
        metric = sig.get("indicator")
        if metric and metric not in metrics_data:
            metric = metric_map.get(metric, metric)
            raw_data = signal_backtest_service._load_raw_data_for_metric(
                db, symbol, metric, None, None, interval_ms
            )
            metrics_data[metric] = raw_data

    # Generate check points from all data timestamps
    all_timestamps = set()
    for data in metrics_data.values():
        if data:
            all_timestamps.update(r[0] for r in data)

    check_points = sorted(all_timestamps)
    if not check_points:
        return set()

    # Evaluate all signals at each check point with pool-level edge detection
    triggers = set()
    was_active = False

    for check_time in check_points:
        all_met = True

        for sig in signals:
            metric = sig.get("indicator")
            operator = sig.get("operator")
            threshold = sig.get("threshold")

            metric = metric_map.get(metric, metric)
            raw_data = metrics_data.get(metric, [])

            value = signal_backtest_service._calculate_indicator_at_time(
                raw_data, metric, check_time, interval_ms
            )

            if value is None:
                all_met = False
                break

            if not signal_backtest_service._evaluate_condition(value, operator, threshold):
                all_met = False
                break

        # Pool-level edge detection: only trigger on False -> True
        if all_met and not was_active:
            triggers.add(check_time)

        was_active = all_met

    return triggers


def _tool_predict_signal_combination(
    db: Session, symbol: str, signals: List[Dict], logic: str
) -> Dict[str, Any]:
    """Predict trigger count when combining multiple signals."""
    # Limit to 5 signals
    signals = signals[:5]
    if not signals:
        return {"error": "No signals provided"}

    # Get triggers for each signal (for individual stats and OR logic)
    signal_triggers = {}
    individual_counts = {}
    individual_samples = {}

    for i, sig in enumerate(signals):
        trigger_condition = {
            "metric": sig.get("indicator"),
            "operator": sig.get("operator"),
            "threshold": sig.get("threshold"),
            "time_window": sig.get("time_window")
        }

        result = signal_backtest_service.backtest_temp_signal(
            db=db,
            symbol=symbol.upper(),
            trigger_condition=trigger_condition,
            kline_min_ts=None,
            kline_max_ts=None
        )

        if "error" in result:
            return {"error": f"Signal {i+1} backtest failed: {result['error']}"}

        triggers = result.get("triggers", [])
        trigger_timestamps = [t["timestamp"] for t in triggers]
        signal_triggers[i] = set(trigger_timestamps)
        individual_counts[i] = len(triggers)
        individual_samples[i] = sorted(trigger_timestamps)[:5]

    # Combine based on logic
    if logic == "AND":
        # Use pool-level edge detection (same as real-time detection)
        combined_ts = _combine_signals_with_pool_edge_detection(
            db, symbol.upper(), signals
        )
    else:  # OR
        combined_ts = set.union(*signal_triggers.values()) if signal_triggers else set()

    combined_count = len(combined_ts)
    # Sample timestamps for combined triggers (max 10)
    combined_samples = sorted(list(combined_ts))[:10]

    # Build response
    response = {
        "symbol": symbol.upper(),
        "logic": logic,
        "signal_count": len(signals),
        "individual_triggers": individual_counts,
        "individual_sample_timestamps": individual_samples,
        "combined_triggers": combined_count,
        "combined_sample_timestamps": combined_samples,
        "assessment": (
            "too_many" if combined_count > 50 else
            "too_few" if combined_count < 3 else
            "reasonable"
        )
    }

    # Add recommendation
    if logic == "AND" and combined_count < 3:
        response["recommendation"] = "AND logic too strict. Consider relaxing thresholds or using OR logic."
    elif logic == "OR" and combined_count > 50:
        response["recommendation"] = "OR logic too loose. Consider tightening thresholds or using AND logic."

    return response


def _execute_tool(db: Session, tool_name: str, arguments: Dict) -> str:
    """Execute a tool and return JSON result."""
    try:
        if tool_name == "get_kline_context":
            result = _tool_get_kline_context(
                db=db,
                symbol=arguments.get("symbol", "BTC"),
                timestamps=arguments.get("timestamps", []),
                time_window=arguments.get("time_window", "5m")
            )
        elif tool_name == "get_indicators_batch":
            result = _tool_get_indicators_batch(
                db=db,
                symbol=arguments.get("symbol", "BTC"),
                indicators=arguments.get("indicators", []),
                time_window=arguments.get("time_window", "5m")
            )
        elif tool_name == "predict_signal_combination":
            result = _tool_predict_signal_combination(
                db=db,
                symbol=arguments.get("symbol", "BTC"),
                signals=arguments.get("signals", []),
                logic=arguments.get("logic", "AND")
            )
        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        return json.dumps(result)
    except Exception as e:
        logger.error(f"Tool execution error: {tool_name} - {e}")
        return json.dumps({"error": str(e)})


# ============== SSE Streaming Implementation ==============

def _sse_event(event_type: str, data: Any) -> str:
    """Format an SSE event."""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"


def _format_analysis_log(analysis_log: List[Dict]) -> str:
    """Format analysis log as Markdown for storage and display."""
    if not analysis_log:
        return ""

    lines = ["<details>", "<summary>Analysis Process</summary>", ""]

    for entry in analysis_log:
        if entry["type"] == "reasoning":
            # Truncate long reasoning content
            content = entry["content"]
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"**Round {entry['round']} - Reasoning:**")
            lines.append(f"> {content}")
            lines.append("")
        elif entry["type"] == "tool_call":
            lines.append(f"**Round {entry['round']} - Tool: `{entry['name']}`**")
            # Format arguments
            args_str = ", ".join(f"{k}={v}" for k, v in entry["arguments"].items())
            lines.append(f"- Arguments: {args_str}")
            # Format result summary
            result = entry.get("result", {})
            if entry["name"] == "get_indicator_statistics":
                stats = result
                lines.append(f"- Result: p90={stats.get('p90')}, p95={stats.get('p95')}, p99={stats.get('p99')}")
            elif entry["name"] == "backtest_threshold":
                lines.append(f"- Result: {result.get('trigger_count')} triggers ({result.get('assessment')})")
            else:
                lines.append(f"- Result: {json.dumps(result)[:200]}")
            lines.append("")

    lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def generate_signal_with_ai_stream(
    db: Session,
    account_id: int,
    user_message: str,
    conversation_id: Optional[int] = None,
    user_id: int = 1
):
    """
    Generate signal configuration using AI with SSE streaming.
    Yields SSE events for real-time progress updates.

    Event types:
    - status: Progress status message
    - tool_call: Tool being called with arguments
    - tool_result: Result from tool execution
    - content: AI response content chunk
    - signal_config: Parsed signal configuration
    - done: Completion with final result
    - error: Error occurred
    """
    start_time = time.time()
    request_id = f"signal_gen_{int(start_time)}"

    logger.info(f"[AI Signal Gen Stream {request_id}] Starting")
    yield _sse_event("status", {"message": "Initializing AI signal generation..."})

    try:
        # Get the specified AI account
        account = db.query(Account).filter(
            Account.id == account_id,
            Account.account_type == "AI"
        ).first()

        if not account:
            yield _sse_event("error", {"message": "AI account not found"})
            return

        yield _sse_event("status", {"message": f"Using model: {account.model}"})

        # Get or create conversation
        conversation = None
        if conversation_id:
            conversation = db.query(AiSignalConversation).filter(
                AiSignalConversation.id == conversation_id,
                AiSignalConversation.user_id == user_id
            ).first()

        if not conversation:
            title = user_message[:50] + "..." if len(user_message) > 50 else user_message
            conversation = AiSignalConversation(user_id=user_id, title=title)
            db.add(conversation)
            db.flush()

        # Save user message
        user_msg = AiSignalMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_message
        )
        db.add(user_msg)
        db.flush()

        # Build message history
        messages = [{"role": "system", "content": SIGNAL_SYSTEM_PROMPT}]
        history_messages = db.query(AiSignalMessage).filter(
            AiSignalMessage.conversation_id == conversation.id,
            AiSignalMessage.id != user_msg.id
        ).order_by(AiSignalMessage.created_at).limit(10).all()

        for msg in history_messages:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_message})

        # Build endpoints and headers
        endpoints = build_chat_completion_endpoints(account.base_url, account.model)
        if not endpoints:
            yield _sse_event("error", {"message": "Invalid base_url configuration"})
            return

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {account.api_key}"
        }

        yield _sse_event("status", {"message": "Analyzing your request..."})

        # Function Calling loop (max 30 rounds)
        max_tool_rounds = 30
        tool_round = 0
        assistant_content = None
        # Accumulate analysis process for white-box display
        analysis_log = []

        while tool_round < max_tool_rounds:
            tool_round += 1
            is_last_round = (tool_round == max_tool_rounds)

            yield _sse_event("status", {
                "message": f"Processing round {tool_round}/{max_tool_rounds}...",
                "round": tool_round,
                "max_rounds": max_tool_rounds
            })

            request_payload = {
                "model": account.model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 4096,
            }

            if is_last_round:
                messages.append({
                    "role": "user",
                    "content": "Output the final signal configuration now. Include the ```signal-config``` block."
                })
            else:
                request_payload["tools"] = SIGNAL_TOOLS
                request_payload["tool_choice"] = "auto"

            # Call API
            response = None
            for endpoint in endpoints:
                try:
                    response = requests.post(endpoint, json=request_payload, headers=headers, timeout=120)
                    if response.status_code == 200:
                        break
                except Exception as e:
                    logger.warning(f"[AI Signal Gen Stream {request_id}] Endpoint error: {e}")

            if not response or response.status_code != 200:
                error_detail = "No response"
                if response:
                    error_detail = f"HTTP {response.status_code}: {response.text[:500]}"
                logger.error(f"[AI Signal Gen Stream {request_id}] API failed at round {tool_round}: {error_detail}")
                system_logger.add_log("ERROR", "ai_signal_gen", f"API failed at round {tool_round}", {"error": error_detail, "request_id": request_id})
                yield _sse_event("error", {"message": f"API request failed: {error_detail}"})
                return

            # Parse response
            try:
                response_json = response.json()
                message = response_json["choices"][0]["message"]
            except Exception as e:
                logger.error(f"[AI Signal Gen Stream {request_id}] Failed to parse response: {e}")
                system_logger.add_log("ERROR", "ai_signal_gen", f"Failed to parse response", {"error": str(e), "request_id": request_id})
                yield _sse_event("error", {"message": f"Failed to parse response: {e}"})
                return

            tool_calls = message.get("tool_calls", [])
            reasoning_content = message.get("reasoning_content", "")
            content = message.get("content", "")

            # Send reasoning content if present
            if reasoning_content:
                yield _sse_event("reasoning", {"content": reasoning_content})
                # Log reasoning for white-box display
                analysis_log.append({
                    "type": "reasoning",
                    "round": tool_round,
                    "content": reasoning_content
                })

            # Send content if present
            if content:
                yield _sse_event("content", {"content": content})

            if tool_calls:
                # Process tool calls
                assistant_msg_dict = {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": tool_calls
                }
                if reasoning_content:
                    assistant_msg_dict["reasoning_content"] = reasoning_content
                messages.append(assistant_msg_dict)

                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    try:
                        func_args = json.loads(tool_call["function"]["arguments"])
                    except json.JSONDecodeError:
                        func_args = {}

                    yield _sse_event("tool_call", {
                        "name": func_name,
                        "arguments": func_args
                    })

                    tool_result = _execute_tool(db, func_name, func_args)
                    tool_result_parsed = json.loads(tool_result)

                    yield _sse_event("tool_result", {
                        "name": func_name,
                        "result": tool_result_parsed
                    })

                    # Log tool call for white-box display
                    analysis_log.append({
                        "type": "tool_call",
                        "round": tool_round,
                        "name": func_name,
                        "arguments": func_args,
                        "result": tool_result_parsed
                    })

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result
                    })
            else:
                # No tool calls - final response
                # Don't add reasoning here - analysis_log already has it via <details> format
                assistant_content = _extract_text_from_message(content) if content else ""
                break

        # Handle limit reached
        if assistant_content is None:
            if 'message' in dir() and message:
                last_content = message.get("content", "")
                if last_content:
                    assistant_content = _extract_text_from_message(last_content)
            if not assistant_content:
                assistant_content = "Processing completed."

        # Extract signal configs and save
        signal_configs = extract_signal_configs(assistant_content)

        for config in signal_configs:
            yield _sse_event("signal_config", {"config": config})

        # Format analysis log as Markdown for storage
        analysis_markdown = _format_analysis_log(analysis_log)
        full_content_for_storage = analysis_markdown + assistant_content if analysis_markdown else assistant_content

        # Save assistant message with analysis process
        assistant_msg = AiSignalMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=full_content_for_storage,
            signal_configs=json.dumps(signal_configs) if signal_configs else None
        )
        db.add(assistant_msg)
        db.commit()

        # Send completion event
        yield _sse_event("done", {
            "success": True,
            "conversation_id": conversation.id,
            "message_id": assistant_msg.id,
            "content": assistant_content,
            "signal_configs": signal_configs,
            "elapsed": round(time.time() - start_time, 2)
        })

    except Exception as e:
        logger.error(f"[AI Signal Gen Stream {request_id}] Error: {e}", exc_info=True)
        system_logger.add_log("ERROR", "ai_signal_gen", f"Unexpected error in AI signal generation", {"error": str(e), "request_id": request_id})
        db.rollback()
        yield _sse_event("error", {"message": str(e)})
