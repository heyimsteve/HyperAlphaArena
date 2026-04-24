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
from services.ai_decision_service import build_chat_completion_endpoints, detect_api_format, _extract_text_from_message, get_max_tokens, build_llm_payload, build_llm_headers, extract_reasoning, convert_tools_to_anthropic, convert_messages_to_anthropic, strip_thinking_tags
from services.signal_backtest_service import signal_backtest_service, TIMEFRAME_MS
from services.exchanges.symbol_mapper import SymbolMapper
from services.system_logger import system_logger

logger = logging.getLogger(__name__)

# System prompt for AI signal generation with Function Calling
SIGNAL_SYSTEM_PROMPT = """You are an expert trading signal designer for cryptocurrency perpetual futures.
You have access to TOOLS to query real market data. Use them to analyze indicators before setting thresholds.

## SUPPORTED EXCHANGES
- **hyperliquid**: Hyperliquid perpetual futures (default)
- **binance**: Binance USDS-M perpetual futures

Each exchange has its own market data. Signals created for one exchange only work with that exchange's data.

## CORE CONCEPT: Signal Pools are TRIGGERS, not STRATEGIES
Signal pools detect market conditions and trigger the Trading AI to make decisions.
The Trading AI analyzes full market context and decides whether to buy/sell/hold.
Your job: Configure signals that detect the market conditions the user cares about.
Output ONE signal pool per request - the Trading AI can only use one pool at a time.

## IMPORTANT: GUIDED CONVERSATION FIRST
Before using any tools, you MUST ask the user 2-3 clarifying questions to better understand their needs:

1. **Exchange**: Which exchange do you want to create signals for? Hyperliquid or Binance?
2. **Trading Direction**: Are you looking for long opportunities, short opportunities, or both?
3. **Signal Type Preference**: What market signals interest you most?
   - Price/momentum changes
   - Order book depth anomalies
   - Funding rate extremes
   - Volume/OI surges
4. **Trigger Frequency**: How often do you expect signals?
   - High frequency (multiple times per day)
   - Medium (1-2 times per day)
   - Low frequency (a few times per week)

Ask these questions conversationally in ONE message. Wait for user's response before calling any tools.
If user says "just analyze" or "skip questions", ask at least which exchange they want before proceeding.

## OPTIMIZED 3-STEP WORKFLOW (only 3 tool calls needed!)
You have exactly 3 tools. Use them efficiently:

**Step 1: `get_indicators_batch`** - Analyze multiple indicators in ONE call
- Query 2-4 indicators based on user preferences
- **IMPORTANT**: Always pass the `exchange` parameter matching user's choice
- Returns p50/p75/p90/p95/p99 percentiles for each
- Use percentiles to determine appropriate thresholds

**Step 2: `predict_signal_combination`** - Test signal combination BEFORE creating
- Input your proposed signal configs with thresholds
- **IMPORTANT**: Always pass the `exchange` parameter matching user's choice
- Choose AND (strict) or OR (loose) logic
- Analyzes the LAST 7 DAYS of data to calculate trigger frequency
- Returns: individual trigger counts (over 7 days), combined trigger count, sample timestamps
- If combined_triggers < 3 (AND too strict) or > 50 (OR too loose), adjust and re-call

**Step 3: `get_kline_context`** (optional) - Verify trigger quality
- Use sample timestamps from Step 2 to check price movements
- **IMPORTANT**: Always pass the `exchange` parameter matching user's choice
- Confirm signals align with meaningful market moves

## CRITICAL RULES
- NEVER output signal configs without calling `predict_signal_combination` first
- ALWAYS use the same exchange parameter across all tool calls
- AND logic often results in 0 triggers if thresholds are too strict - always verify!
- Aim for 5-30 combined triggers over 7 days (approximately 1-4 triggers per day)
- If combination fails, relax thresholds or switch AND→OR

## AVAILABLE INDICATORS (query any you need)

### Market Flow Indicators (15-second granularity data)
- oi_delta_percent: OI change % over time window (capital flow indicator)
- funding_rate: Funding rate CHANGE in bps (basis points). Positive=rate increasing, negative=rate decreasing. 1 bps = 0.01%.
- cvd: Cumulative Volume Delta (buying/selling pressure)
- depth_ratio: Bid/Ask depth ratio (orderbook imbalance)
- order_imbalance: Normalized imbalance -1 to +1 (real-time pressure)
- taker_buy_ratio: Log of taker buy/sell ratio, ln(buy/sell). >0=buyers dominate, <0=sellers dominate. Symmetric around 0.
- taker_volume: **COMPOSITE INDICATOR** - Detects when one side dominates with significant volume. Requires: direction (buy/sell/any), ratio_threshold (multiplier, e.g., 1.5 = 50% more), volume_threshold (min total volume in USD).
- price_change: Price change percentage over time window. Positive=price up, negative=price down. Formula: (current_price - prev_price) / prev_price * 100
- volatility: Price volatility (range) percentage over time window. Always positive. Formula: (high - low) / low * 100. Detects price swings regardless of direction.

### Factor Indicators (K-line close data, 86+ factors)
Factor signals use the format `factor:<factor_name>` as the metric value.
They are computed from K-line (candlestick) data using the expression engine, and trigger ONLY at K-line close boundaries.
- Use `get_indicators_batch` with indicator name `factor:<factor_name>` to query factor value distribution
- Factors cover: Trend (ADX, MA crossovers), Momentum (RSI, CCI, ROC), Volatility (ATR, Bollinger), Volume (CMF, MFI), Statistical (Z-score, skewness), Composite (Ichimoku, Keltner, efficiency ratio)
- To see available factors, ask the user or query the factor library
- Factor metric format in signal config: `"metric": "factor:RSI21"`, with standard operator/threshold
- Factor signals are ideal for **trend-following** and **mean-reversion** strategies on longer timeframes (1h, 4h)
- `get_indicators_batch` response includes `decay_half_life_hours`: positive=half-life hours (short-term factor), -1=persistent (IC strengthens over time, trend factor), null=no data

## OPERATORS (for standard indicators)
- greater_than, less_than, greater_than_or_equal, less_than_or_equal, abs_greater_than
- NOTE: taker_volume does NOT use operators - it uses direction + ratio_threshold + volume_threshold

## TIME WINDOWS
- 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h

## INDICATOR SEMANTICS (for threshold design)
| Indicator | Positive Value Meaning | Negative Value Meaning |
|-----------|------------------------|------------------------|
| cvd | Buyer volume dominates | Seller volume dominates |
| oi_delta_percent | Positions increasing | Positions decreasing |
| funding_rate | Rate increasing (more bullish) | Rate decreasing (more bearish) |
| taker_buy_ratio | Buyers more aggressive | Sellers more aggressive |
| order_imbalance | Bid depth > Ask depth | Ask depth > Bid depth |
| depth_ratio | >1: More bids | <1: More asks |

## OPERATORS AND DIRECTION DETECTION
- greater_than / less_than: Detect specific direction (e.g., cvd > 0 detects buyer flow)
- abs_greater_than: Detect magnitude only, ignores direction (for volatility/activity signals)

## HANDLING USER DIRECTION PREFERENCES
- "long opportunities": Use conditions detecting buyer-dominated flow (cvd > X, taker_buy_ratio > 0, order_imbalance > X)
- "short opportunities": Use conditions detecting seller-dominated flow (cvd < -X, taker_buy_ratio < 0, order_imbalance < -X)
- "both directions": Use abs_greater_than to detect significant activity regardless of direction

## OUTPUT FORMAT - TWO OPTIONS

### Option 1: Single Signal (use when user needs ONE simple signal)
```signal-config
{
  "name": "BTC_OI_Surge",
  "symbol": "BTC",
  "exchange": "hyperliquid",
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
  "name": "BTC_5M_MOMENTUM_SURGE",
  "symbol": "BTC",
  "exchange": "binance",
  "description": "Detects strong momentum with multiple confirmations",
  "logic": "AND",
  "signals": [
    {"metric": "cvd", "operator": "greater_than", "threshold": 10000000, "time_window": "5m"},
    {"metric": "order_imbalance", "operator": "greater_than", "threshold": 0.99, "time_window": "5m"},
    {"metric": "oi_delta_percent", "operator": "greater_than", "threshold": 0.3, "time_window": "5m"}
  ]
}
```
**NOTE**: Output ONE signal pool per request. The Trading AI can only bind to one pool at a time.
**IMPORTANT**: The `exchange` field is REQUIRED. Use the exchange the user specified.

### Option 3: taker_volume Composite Signal (special format)
```signal-config
{
  "name": "BTC_TAKER_SURGE",
  "symbol": "BTC",
  "exchange": "hyperliquid",
  "description": "Detects strong taker volume dominance",
  "trigger_condition": {
    "metric": "taker_volume",
    "direction": "buy",
    "ratio_threshold": 1.5,
    "volume_threshold": 100000,
    "time_window": "5m"
  }
}
```
- direction: "buy" (buyers dominate), "sell" (sellers dominate), or "any" (either side)
- ratio_threshold: Multiplier (1.5 = one side is 1.5x the other)
- volume_threshold: Minimum total volume in USD (buy + sell)

### Option 4: Factor Signal (uses K-line close data)
```signal-config
{
  "name": "BTC_RSI_OVERSOLD",
  "symbol": "BTC",
  "exchange": "hyperliquid",
  "description": "RSI21 drops below 30 (oversold zone)",
  "trigger_condition": {
    "metric": "factor:RSI21",
    "operator": "less_than",
    "threshold": 30,
    "time_window": "1h"
  }
}
```
- Factor metrics use `factor:<factor_name>` format (e.g., `factor:NORM_PRICE`, `factor:ADX14`)
- Use standard operator/threshold (same as other signals)
- time_window = K-line period (1h, 4h recommended for factors)
- Factor signals trigger at K-line close boundaries, not every 15 seconds

**IMPORTANT**: When you use `predict_signal_combination` to test AND/OR combinations, ALWAYS output using `signal-pool-config` format. This allows one-click creation of the entire signal pool.

## CRITICAL: OUTPUT FORMAT COMPLIANCE

Your signal configuration output MUST use the exact code block format:
- Use ` ```signal-config ` for single signals
- Use ` ```signal-pool-config ` for signal pools

Always wrap your final configuration in the appropriate code block, otherwise the user will NOT see your signal configuration in the UI.
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
                    "time_window": {"type": "string", "description": "K-line interval matching signal time_window"},
                    "exchange": {"type": "string", "enum": ["hyperliquid", "binance"], "description": "Exchange to fetch K-lines from. Default: hyperliquid"}
                },
                "required": ["symbol", "timestamps", "time_window"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_indicators_batch",
            "description": "Get statistical distribution of MULTIPLE indicators in one call. Returns stats for each indicator from the specified exchange's market data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading symbol, e.g., BTC, ETH"},
                    "indicators": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of indicator names to analyze (max 6). Standard: oi_delta_percent, funding_rate, cvd, depth_ratio, order_imbalance, taker_buy_ratio, taker_volume, price_change, volatility. Factor: use 'factor:<name>' format (e.g., 'factor:RSI21', 'factor:ADX14')."
                    },
                    "time_window": {"type": "string", "enum": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"], "description": "Aggregation time window"},
                    "exchange": {"type": "string", "enum": ["hyperliquid", "binance"], "description": "Exchange to query data from. Default: hyperliquid"}
                },
                "required": ["symbol", "indicators", "time_window"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "predict_signal_combination",
            "description": "Predict trigger count when combining multiple signals with AND/OR logic. Analyzes the LAST 7 DAYS of data from the specified exchange. Use this BEFORE creating signals to ensure the combination will have reasonable trigger frequency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading symbol"},
                    "signals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "indicator": {"type": "string", "description": "Metric name. Standard: cvd, oi_delta_percent, etc. Composite: taker_volume. Factor: factor:<name> (e.g., factor:RSI21)."},
                                "operator": {"type": "string", "description": "For standard indicators only. Not used for taker_volume."},
                                "threshold": {"type": "number", "description": "For standard indicators only. Not used for taker_volume."},
                                "time_window": {"type": "string"},
                                "direction": {"type": "string", "enum": ["buy", "sell", "any"], "description": "For taker_volume only: which side must dominate"},
                                "ratio_threshold": {"type": "number", "description": "For taker_volume only: multiplier (e.g., 1.5 = 50% more)"},
                                "volume_threshold": {"type": "number", "description": "For taker_volume only: min total volume in USD"}
                            },
                            "required": ["indicator", "time_window"]
                        },
                        "description": "List of signal configurations to combine (max 5). For taker_volume, use direction/ratio_threshold/volume_threshold."
                    },
                    "logic": {"type": "string", "enum": ["AND", "OR"], "description": "Combination logic: AND (all must trigger) or OR (any triggers)"},
                    "exchange": {"type": "string", "enum": ["hyperliquid", "binance"], "description": "Exchange to query data from. Default: hyperliquid"}
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
            Account.account_type == "AI",
            Account.is_deleted != True
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

        # Build message history with compression support
        from services.ai_context_compression_service import compress_messages, update_compression_points

        messages = [{"role": "system", "content": SIGNAL_SYSTEM_PROMPT}]

        # Get more messages, compression will handle limits
        history_messages = db.query(AiSignalMessage).filter(
            AiSignalMessage.conversation_id == conversation.id,
            AiSignalMessage.id != user_msg.id
        ).order_by(AiSignalMessage.created_at).limit(100).all()

        last_message_id = None
        for msg in history_messages:
            messages.append({"role": msg.role, "content": msg.content})
            last_message_id = msg.id

        messages.append({"role": "user", "content": user_message})

        # Apply compression if needed
        api_config = {
            "base_url": account.base_url,
            "api_key": account.api_key,
            "model": account.model,
            "api_format": detect_api_format(account.base_url)[1] or "openai"
        }
        comp_result = compress_messages(messages, api_config, db=db)
        messages = comp_result["messages"]

        # Update compression_points if compression occurred
        if comp_result["compressed"] and comp_result["summary"] and last_message_id:
            update_compression_points(
                conversation, last_message_id,
                comp_result["summary"], comp_result["compressed_at"], db
            )

        logger.info(f"[AI Signal Gen {request_id}] Built message context: {len(messages)} messages total")

        # Call LLM API with Function Calling support
        api_format = api_config["api_format"]
        endpoint, _ = detect_api_format(account.base_url)
        if api_format == 'anthropic':
            endpoints = [endpoint] if endpoint else []
        else:
            endpoints = build_chat_completion_endpoints(account.base_url, account.model)
        if not endpoints:
            return {"success": False, "error": "Invalid base_url configuration"}

        # Use unified headers builder (see build_llm_headers in ai_decision_service)
        headers = build_llm_headers(api_format, account.api_key, account.base_url)

        # Function Calling loop (max 30 rounds, last round forces no tools)
        max_tool_rounds = 30
        tool_round = 0
        assistant_content = None

        while tool_round < max_tool_rounds:
            tool_round += 1
            is_last_round = (tool_round == max_tool_rounds)
            logger.info(f"[AI Signal Gen {request_id}] Tool round {tool_round}/{max_tool_rounds} (last={is_last_round})")

            # On last round, force model to give final answer without tools
            if is_last_round:
                messages.append({
                    "role": "user",
                    "content": "You have used enough tools. Now output the final signal configuration based on your analysis. Include the ```signal-config``` block."
                })

            # Use unified payload builder (see build_llm_payload in ai_decision_service)
            if api_format == 'anthropic':
                sys_prompt, anthropic_messages = convert_messages_to_anthropic(messages)
                tools_for_round = convert_tools_to_anthropic(SIGNAL_TOOLS) if not is_last_round else None
                request_payload = build_llm_payload(
                    model=account.model,
                    messages=[{"role": "system", "content": sys_prompt}] + anthropic_messages,
                    api_format=api_format,
                    tools=tools_for_round,
                )
            else:
                request_payload = build_llm_payload(
                    model=account.model,
                    messages=messages,
                    api_format=api_format,
                    tools=SIGNAL_TOOLS if not is_last_round else None,
                    tool_choice="auto" if not is_last_round else None,
                )

            response = None
            last_error = None
            last_status_code = None
            last_response_text = None

            for endpoint in endpoints:
                try:
                    logger.info(f"[AI Signal Gen {request_id}] Trying endpoint: {endpoint}")
                    api_start = time.time()
                    response = requests.post(endpoint, json=request_payload, headers=headers, timeout=120)
                    api_elapsed = time.time() - api_start
                    last_status_code = response.status_code
                    last_response_text = response.text[:2000] if response.text else None

                    if response.status_code == 200:
                        logger.info(f"[AI Signal Gen {request_id}] Success in {api_elapsed:.2f}s")
                        break
                    else:
                        last_error = f"HTTP {response.status_code}"
                        logger.warning(f"[AI Signal Gen {request_id}] Endpoint failed: {response.status_code} - {response.text[:500]}")
                except requests.exceptions.Timeout as e:
                    last_error = f"Timeout after 120s: {str(e)}"
                    logger.warning(f"[AI Signal Gen {request_id}] Timeout on {endpoint}: {e}")
                except requests.exceptions.ConnectionError as e:
                    last_error = f"Connection error: {str(e)}"
                    logger.warning(f"[AI Signal Gen {request_id}] Connection error on {endpoint}: {e}")
                except Exception as e:
                    last_error = f"{type(e).__name__}: {str(e)}"
                    logger.warning(f"[AI Signal Gen {request_id}] Error on {endpoint}: {type(e).__name__}: {e}")

            if not response or response.status_code != 200:
                error_parts = []
                if last_error:
                    error_parts.append(f"error={last_error}")
                if last_status_code:
                    error_parts.append(f"status={last_status_code}")
                if last_response_text:
                    error_parts.append(f"response={last_response_text[:500]}")
                error_detail = "; ".join(error_parts) if error_parts else "No response from API"
                logger.error(f"[AI Signal Gen {request_id}] API failed: {error_detail}")
                return {"success": False, "error": f"All endpoints failed: {error_detail}"}

            # Parse response based on API format
            try:
                response_json = response.json()
                if api_format == 'anthropic':
                    content_blocks = response_json.get("content", [])
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
                    tool_calls = None
                    api_tool_calls = tool_uses if tool_uses else None
                else:
                    message = response_json["choices"][0]["message"]
                    tool_calls = message.get("tool_calls", [])
                    reasoning_content = message.get("reasoning_content", "") or extract_reasoning(message)
                    content = message.get("content", "")
                    api_tool_calls = tool_calls if tool_calls else None
            except Exception as e:
                logger.error(f"[AI Signal Gen {request_id}] Failed to parse response: {e}")
                return {"success": False, "error": f"Failed to parse AI response: {str(e)}"}

            # Strip <thinking> text tags from content
            content, tag_thinking = strip_thinking_tags(content)
            if tag_thinking and not reasoning_content:
                reasoning_content = tag_thinking

            # Log for debugging
            logger.info(f"[AI Signal Gen {request_id}] Response: tool_calls={len(api_tool_calls) if api_tool_calls else 0}, "
                       f"has_reasoning={bool(reasoning_content)}, has_content={bool(content)}")

            if api_tool_calls:
                if api_format == 'anthropic':
                    # Anthropic format: tool_use blocks
                    messages.append({
                        "role": "assistant",
                        "content": content or "",
                        "tool_use_blocks": response_json.get("content", [])
                    })
                    for tool_use in api_tool_calls:
                        func_name = tool_use.get("name", "")
                        tool_id = tool_use.get("id", "")
                        func_args = tool_use.get("input", {})
                        logger.info(f"[AI Signal Gen {request_id}] Executing tool: {func_name}({func_args})")
                        tool_result = _execute_tool(db, func_name, func_args)
                        logger.info(f"[AI Signal Gen {request_id}] Tool result: {tool_result[:200]}...")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": tool_result
                        })
                else:
                    # OpenAI format: tool_calls array
                    assistant_msg_dict = {
                        "role": "assistant",
                        "content": content or "",
                        "tool_calls": api_tool_calls
                    }
                    if reasoning_content:
                        assistant_msg_dict["reasoning_content"] = reasoning_content
                    messages.append(assistant_msg_dict)
                    for tool_call in api_tool_calls:
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

    Parsing priority:
    1. Exact code block format (```signal-config / ```signal-pool-config)
    2. Fallback: ```json code block
    3. Fallback: Plain JSON object detection

    Returns list of configs with '_type' field: 'signal' or 'pool'
    """
    configs = []

    # === Priority 1: Exact code block format (existing logic, preserved) ===
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

    # If exact match succeeded, return immediately
    if configs:
        return configs

    # === Priority 2: Fallback parsing (only when exact match fails) ===
    logger.info("No exact code block match, attempting fallback parsing")

    # Try ```json code block first
    json_pattern = r"```json\s*([\s\S]*?)```"
    json_matches = re.findall(json_pattern, content)

    for match in json_matches:
        config = _try_parse_signal_json(match.strip())
        if config:
            configs.append(config)

    if configs:
        return configs

    # Try plain JSON object detection using bracket matching
    # Find { "name": pattern and extract complete JSON object
    json_start_pattern = r'\{\s*"name"\s*:'
    for match in re.finditer(json_start_pattern, content):
        start_idx = match.start()
        json_obj = _extract_balanced_json(content, start_idx)
        if json_obj:
            config = _try_parse_signal_json(json_obj)
            if config:
                configs.append(config)
                break  # Only take the first valid plain JSON to avoid duplicates

    return configs


def _extract_balanced_json(content: str, start_idx: int) -> Optional[str]:
    """Extract a complete JSON object using bracket matching.

    Starting from start_idx (which should be at '{'), finds the matching '}'.
    """
    if start_idx >= len(content) or content[start_idx] != '{':
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start_idx, len(content)):
        char = content[i]

        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return content[start_idx:i + 1]

    return None  # Unbalanced brackets


def _try_parse_signal_json(json_str: str) -> Optional[Dict]:
    """Try to parse a JSON string as signal config.

    Determines type based on content:
    - Has 'signals' array -> pool
    - Has 'trigger_condition' -> signal
    - Has 'logic' field -> pool
    - Otherwise -> signal (default)
    """
    try:
        config = json.loads(json_str)
        if not isinstance(config, dict):
            return None

        # Must have 'name' field to be a valid signal config
        if "name" not in config:
            return None

        # Determine type based on content
        if "signals" in config and isinstance(config.get("signals"), list):
            config["_type"] = "pool"
        elif "logic" in config:
            config["_type"] = "pool"
        elif "trigger_condition" in config:
            config["_type"] = "signal"
        else:
            # Default to signal for simple configs
            config["_type"] = "signal"

        logger.info(f"Fallback parsing succeeded: type={config['_type']}, name={config.get('name')}")
        return config

    except json.JSONDecodeError as e:
        logger.warning(f"Fallback JSON parse failed: {e}")
        return None


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
            "reasoning_snapshot": msg.reasoning_snapshot,
            "tool_calls_log": json.loads(msg.tool_calls_log) if msg.tool_calls_log else None,
            "is_complete": msg.is_complete,
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
    db: Session, symbol: str, timestamps: List[int], time_window: str,
    exchange: str = "hyperliquid"
) -> Dict[str, Any]:
    """Get K-line price data around specific timestamps."""
    # Map time_window to interval format
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

    # Fetch K-lines from exchange API
    try:
        # Get range covering all timestamps with some buffer
        min_ts = min(timestamps) - (10 * interval_ms)
        max_ts = max(timestamps) + (10 * interval_ms)

        if exchange == "binance":
            # Binance USDS-M Futures API
            binance_symbol = f"{symbol.upper()}USDT"
            url = f"https://fapi.binance.com/fapi/v1/klines"
            params = {
                "symbol": binance_symbol,
                "interval": interval,
                "startTime": min_ts,
                "endTime": max_ts,
                "limit": 1500
            }
            resp = requests.get(url, params=params, timeout=10)
        else:
            # Hyperliquid API (default)
            url = "https://api.hyperliquid.xyz/info"
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": SymbolMapper.to_exchange(symbol.upper(), "hyperliquid"),
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

        # Build K-line lookup by timestamp (handle different exchange formats)
        kline_map = {}
        for k in klines:
            if exchange == "binance":
                # Binance format: [openTime, open, high, low, close, volume, ...]
                ts = k[0]
                kline_map[ts] = {
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5])
                }
            else:
                # Hyperliquid format: {"t": timestamp, "o": open, ...}
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
            "exchange": exchange,
            "time_window": time_window,
            "contexts": contexts
        }
    except Exception as e:
        logger.error(f"Error fetching K-line context: {e}")
        return {"error": str(e)}


def _tool_get_indicators_batch(
    db: Session, symbol: str, indicators: List[str], time_window: str,
    exchange: str = "hyperliquid"
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
        "taker_volume": "taker_ratio",  # taker_volume uses same underlying data
    }
    interval_ms = TIMEFRAME_MS.get(time_window, 300000)

    results = {"symbol": symbol.upper(), "exchange": exchange, "time_window": time_window, "indicators": {}}

    for indicator in indicators:
        # Handle factor indicators
        if indicator.startswith("factor:"):
            factor_name = indicator.split(":", 1)[1]
            try:
                from services.market_data import get_kline_data
                from services.factor_resolver import (
                    compute_factor_series,
                    extract_factor_expression,
                )

                market = "binance" if exchange == "binance" else "CRYPTO"
                klines = get_kline_data(symbol.upper(), market=market, period=time_window, count=500)
                if not klines or len(klines) < 50:
                    results["indicators"][indicator] = {"error": "Insufficient K-line data"}
                    continue

                series, factor, err = compute_factor_series(
                    db=db,
                    factor_name=factor_name,
                    symbol=symbol.upper(),
                    period=time_window,
                    exchange=exchange,
                    klines=klines,
                )
                if series is None or len(series) == 0:
                    results["indicators"][indicator] = {"error": err or "Factor computation failed"}
                    continue

                values = series.dropna().astype(float).tolist()
                if not values:
                    results["indicators"][indicator] = {"error": "No valid values"}
                    continue

                arr = np.array(values)
                factor_info = {
                    "type": "factor",
                    "expression": extract_factor_expression(factor or {"name": factor_name}),
                    "data_points": len(values),
                    "min": float(np.min(arr)),
                    "max": float(np.max(arr)),
                    "mean": float(np.mean(arr)),
                    "p50": float(np.percentile(arr, 50)),
                    "p75": float(np.percentile(arr, 75)),
                    "p90": float(np.percentile(arr, 90)),
                    "p95": float(np.percentile(arr, 95)),
                    "p99": float(np.percentile(arr, 99)),
                    "latest": float(arr[-1]),
                    "note": "Factor triggers at K-line close. Use metric format: factor:" + factor_name,
                }
                # Attach decay half-life from effectiveness table
                from sqlalchemy import text as sa_text
                dhl_row = db.execute(sa_text("""
                    SELECT decay_half_life FROM factor_effectiveness
                    WHERE factor_name = :fn AND symbol = :sym AND period = '1h'
                        AND exchange = :ex AND decay_half_life IS NOT NULL
                    ORDER BY calc_date DESC LIMIT 1
                """), {"fn": factor_name, "sym": symbol.upper(), "ex": exchange}).fetchone()
                if dhl_row and dhl_row[0] is not None:
                    factor_info["decay_half_life_hours"] = int(dhl_row[0])
                results["indicators"][indicator] = factor_info
            except Exception as e:
                results["indicators"][indicator] = {"error": str(e)}
            continue

        metric = metric_map.get(indicator, indicator)

        # Special note for taker_volume
        if indicator == "taker_volume":
            results["indicators"][indicator] = {
                "note": "taker_volume is a composite indicator. Use direction (buy/sell/any), ratio_threshold (multiplier), and volume_threshold (USD) instead of operator/threshold.",
                "underlying_metric": "taker_ratio (log scale)",
                "example": {"direction": "buy", "ratio_threshold": 1.5, "volume_threshold": 100000}
            }
            continue
        signal_backtest_service._bucket_cache = {}
        bucket_values = signal_backtest_service._compute_all_bucket_values(
            db, symbol.upper(), metric, interval_ms, exchange
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
    db: Session, symbol: str, signals: List[Dict],
    preloaded_data: Dict[str, List] = None,
    preloaded_indexes: Dict[str, List[int]] = None
) -> set:
    """
    Combine signals using pool-level edge detection (same as real-time detection).
    Evaluates all signals at each check point and triggers only on False->True transition.

    Performance optimization: accepts preloaded_data and preloaded_indexes to avoid
    redundant database queries and enable O(log n) binary search.
    """
    if not signals:
        return set()

    # Get time window from first signal
    time_window = signals[0].get("time_window", "5m")
    timeframe_ms = {
        "1m": 60000, "3m": 180000, "5m": 300000,
        "15m": 900000, "30m": 1800000, "1h": 3600000
    }
    interval_ms = timeframe_ms.get(time_window, 300000)

    import math
    metric_map = {"oi_delta_percent": "oi_delta", "taker_buy_ratio": "taker_ratio"}

    # Use preloaded data if available, otherwise load from database
    if preloaded_data is not None:
        metrics_data = preloaded_data
        metrics_indexes = preloaded_indexes or {}
    else:
        # Fallback: load raw data for all metrics (backward compatibility)
        metrics_data = {}
        metrics_indexes = {}
        for sig in signals:
            metric = sig.get("indicator")
            if metric:
                # taker_volume uses taker_ratio data
                if metric == "taker_volume":
                    mapped_metric = "taker_ratio"
                else:
                    mapped_metric = metric_map.get(metric, metric)
                if mapped_metric not in metrics_data:
                    raw_data = signal_backtest_service._load_raw_data_for_metric(
                        db, symbol, mapped_metric, None, None, interval_ms
                    )
                    metrics_data[mapped_metric] = raw_data
                    metrics_indexes[mapped_metric] = [r[0] for r in raw_data] if raw_data else []

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

            # Handle taker_volume composite signal
            if metric == "taker_volume":
                direction = sig.get("direction", "any")
                ratio_threshold = sig.get("ratio_threshold", 1.5)
                volume_threshold = sig.get("volume_threshold", 0)
                log_threshold = math.log(max(ratio_threshold, 1.01))

                raw_data = metrics_data.get("taker_ratio", [])

                # Use _calc_taker_data_at_time to get both log_ratio and volume
                taker_data = signal_backtest_service._calc_taker_data_at_time(
                    raw_data, check_time, interval_ms
                )

                if taker_data is None:
                    all_met = False
                    break

                log_ratio = taker_data["log_ratio"]
                total_volume = taker_data["volume"]

                # Check ratio condition
                if direction == "buy":
                    ratio_met = log_ratio >= log_threshold
                elif direction == "sell":
                    ratio_met = log_ratio <= -log_threshold
                else:  # any
                    ratio_met = abs(log_ratio) >= log_threshold

                # Check volume condition
                volume_met = total_volume >= volume_threshold

                if not (ratio_met and volume_met):
                    all_met = False
                    break
            else:
                # Standard indicator
                operator = sig.get("operator")
                threshold = sig.get("threshold")

                mapped_metric = metric_map.get(metric, metric)
                raw_data = metrics_data.get(mapped_metric, [])
                ts_index = metrics_indexes.get(mapped_metric)

                value = signal_backtest_service._calculate_indicator_at_time(
                    raw_data, mapped_metric, check_time, interval_ms, ts_index
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
    db: Session, symbol: str, signals: List[Dict], logic: str,
    exchange: str = "hyperliquid"
) -> Dict[str, Any]:
    """
    Predict trigger count when combining multiple signals.

    Performance optimizations:
    1. Preload all required metric data once (avoid redundant DB queries)
    2. Build timestamp indexes for O(log n) binary search
    3. Reuse preloaded data for both individual and combined analysis
    """
    # Limit to 5 signals
    signals = signals[:5]
    if not signals:
        return {"error": "No signals provided"}

    # Get time window from first signal (assume all signals use same time window)
    time_window = signals[0].get("time_window", "5m")
    timeframe_ms = {
        "1m": 60000, "3m": 180000, "5m": 300000,
        "15m": 900000, "30m": 1800000, "1h": 3600000
    }
    interval_ms = timeframe_ms.get(time_window, 300000)

    metric_map = {"oi_delta_percent": "oi_delta", "taker_buy_ratio": "taker_ratio"}

    # Step 1: Preload all required metric data ONCE
    preloaded_data = {}
    preloaded_indexes = {}
    required_metrics = set()

    for sig in signals:
        metric = sig.get("indicator")
        if metric:
            # Factor and taker_volume handled separately
            if metric.startswith("factor:") or metric == "taker_volume":
                if metric == "taker_volume":
                    required_metrics.add("taker_ratio")
            else:
                mapped_metric = metric_map.get(metric, metric)
                required_metrics.add(mapped_metric)

    # Calculate 7-day time range (matching backtest behavior)
    from datetime import datetime
    current_time_ms = int(datetime.utcnow().timestamp() * 1000)
    start_time_ms = current_time_ms - (7 * 24 * 60 * 60 * 1000)  # 7 days ago

    for mapped_metric in required_metrics:
        raw_data = signal_backtest_service._load_raw_data_for_metric(
            db, symbol.upper(), mapped_metric, start_time_ms, current_time_ms, interval_ms, exchange
        )
        preloaded_data[mapped_metric] = raw_data
        # Build timestamp index for binary search (data is already sorted by timestamp)
        preloaded_indexes[mapped_metric] = [r[0] for r in raw_data] if raw_data else []

    # Step 2: Calculate individual signal triggers using preloaded data
    signal_triggers = {}
    individual_counts = {}
    individual_samples = {}

    for i, sig in enumerate(signals):
        metric = sig.get("indicator")

        # Handle factor signal
        if metric and metric.startswith("factor:"):
            factor_triggers = _find_factor_signal_triggers(
                db, symbol.upper(), sig, start_time_ms, current_time_ms, exchange
            )
            if isinstance(factor_triggers, dict) and "error" in factor_triggers:
                return factor_triggers
            triggers = factor_triggers
        # Handle taker_volume composite signal separately
        elif metric == "taker_volume":
            direction = sig.get("direction", "any")
            ratio_threshold = sig.get("ratio_threshold", 1.5)
            volume_threshold = sig.get("volume_threshold", 0)

            raw_data = preloaded_data.get("taker_ratio", [])
            ts_index = preloaded_indexes.get("taker_ratio", [])

            if not raw_data:
                return {"error": f"No data found for taker_volume"}

            triggers = _find_taker_volume_triggers(
                raw_data, ts_index, direction, ratio_threshold, volume_threshold, interval_ms
            )
        else:
            # Standard indicator
            operator = sig.get("operator")
            threshold = sig.get("threshold")

            if not all([metric, operator, threshold is not None]):
                return {"error": f"Signal {i+1} has incomplete configuration"}

            mapped_metric = metric_map.get(metric, metric)
            raw_data = preloaded_data.get(mapped_metric, [])
            ts_index = preloaded_indexes.get(mapped_metric, [])

            if not raw_data:
                return {"error": f"No data found for metric {metric}"}

            # Find triggers using preloaded data with binary search
            triggers = _find_triggers_with_preloaded_data(
                raw_data, ts_index, mapped_metric, operator, threshold, interval_ms
            )

        signal_triggers[i] = set(triggers)
        individual_counts[i] = len(triggers)
        individual_samples[i] = sorted(triggers)[:5]

    # Step 3: Combine based on logic (reuse preloaded data)
    if logic == "AND":
        combined_ts = _combine_signals_with_pool_edge_detection(
            db, symbol.upper(), signals, preloaded_data, preloaded_indexes
        )
    else:  # OR
        combined_ts = set.union(*signal_triggers.values()) if signal_triggers else set()

    combined_count = len(combined_ts)
    combined_samples = sorted(list(combined_ts))[:10]

    # Build response
    response = {
        "symbol": symbol.upper(),
        "exchange": exchange,
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

    if logic == "AND" and combined_count < 3:
        response["recommendation"] = "AND logic too strict. Consider relaxing thresholds or using OR logic."
    elif logic == "OR" and combined_count > 50:
        response["recommendation"] = "OR logic too loose. Consider tightening thresholds or using AND logic."

    return response


def _find_factor_signal_triggers(
    db: Session, symbol: str, sig: Dict,
    start_time_ms: int, current_time_ms: int, exchange: str
) -> List[int]:
    """Find factor signal trigger timestamps using K-line data and edge detection."""
    import pandas as pd
    from sqlalchemy import text
    from services.factor_resolver import compute_factor_series

    metric = sig.get("indicator", "")
    factor_name = metric.split(":", 1)[1] if ":" in metric else metric
    operator = sig.get("operator")
    threshold = sig.get("threshold")
    tw = sig.get("time_window", "1h")

    if not all([operator, threshold is not None]):
        return {"error": f"Factor signal missing operator/threshold"}

    # Load K-lines for time range + warm-up
    from services.signal_backtest_service import TIMEFRAME_MS
    from services.factor_data_provider import get_klines_from_db
    interval_ms = TIMEFRAME_MS.get(tw, 3600000)
    warmup_ms = interval_ms * 200
    load_start_s = (start_time_ms - warmup_ms) // 1000
    end_s = current_time_ms // 1000

    klines = get_klines_from_db(db, exchange, symbol, tw, start_ts=load_start_s, end_ts=end_s)

    if len(klines) < 30:
        return {"error": f"Insufficient K-line data for factor {factor_name}"}

    series, _, err = compute_factor_series(
        db=db,
        factor_name=factor_name,
        symbol=symbol,
        period=tw,
        exchange=exchange,
        klines=klines,
    )
    if series is None or len(series) == 0:
        return {"error": err or f"Factor {factor_name} computation failed"}

    # Iterate with edge detection
    triggers = []
    was_active = False
    backtest_start_s = start_time_ms // 1000

    for idx, kline in enumerate(klines):
        ts = kline["timestamp"]
        if idx >= len(series) or pd.isna(series.iloc[idx]):
            continue
        value = float(series.iloc[idx])
        condition_met = signal_backtest_service._evaluate_condition(value, operator, threshold)
        if ts < backtest_start_s:
            was_active = condition_met
            continue
        if condition_met and not was_active:
            triggers.append(ts * 1000)
        was_active = condition_met

    return triggers


def _find_triggers_with_preloaded_data(
    raw_data: List, ts_index: List[int], metric: str,
    operator: str, threshold: float, interval_ms: int
) -> List[int]:
    """
    Find trigger timestamps using preloaded data with binary search optimization.
    Implements edge detection: only triggers on False -> True transitions.
    """
    if not raw_data:
        return []

    # Generate check points from data timestamps
    check_points = sorted(set(ts_index))
    if not check_points:
        return []

    triggers = []
    was_active = False

    for check_time in check_points:
        value = signal_backtest_service._calculate_indicator_at_time(
            raw_data, metric, check_time, interval_ms, ts_index
        )

        if value is None:
            was_active = False
            continue

        condition_met = signal_backtest_service._evaluate_condition(value, operator, threshold)

        # Edge detection: only trigger on False -> True
        if condition_met and not was_active:
            triggers.append(check_time)

        was_active = condition_met

    return triggers


def _find_taker_volume_triggers(
    raw_data: List, ts_index: List[int], direction: str,
    ratio_threshold: float, volume_threshold: float, interval_ms: int
) -> List[int]:
    """
    Find taker_volume trigger timestamps using log ratio AND volume threshold.
    Uses edge detection: only triggers on False -> True transitions.

    Both conditions must be met:
    1. Ratio condition: |log(buy/sell)| >= log(ratio_threshold) for direction
    2. Volume condition: total_volume (buy + sell) >= volume_threshold
    """
    import math

    if not raw_data:
        return []

    check_points = sorted(set(ts_index))
    if not check_points:
        return []

    # Convert ratio_threshold to log threshold
    log_threshold = math.log(max(ratio_threshold, 1.01))

    triggers = []
    was_active = False

    for check_time in check_points:
        # Get taker data including volume at this time point
        taker_data = signal_backtest_service._calc_taker_data_at_time(
            raw_data, check_time, interval_ms
        )

        if taker_data is None:
            was_active = False
            continue

        log_ratio = taker_data["log_ratio"]
        total_volume = taker_data["volume"]

        # Check BOTH ratio and volume conditions
        ratio_met = False
        if direction == "buy":
            ratio_met = log_ratio >= log_threshold
        elif direction == "sell":
            ratio_met = log_ratio <= -log_threshold
        elif direction == "any":
            ratio_met = abs(log_ratio) >= log_threshold

        volume_met = total_volume >= volume_threshold
        condition_met = ratio_met and volume_met

        # Edge detection: only trigger on False -> True
        if condition_met and not was_active:
            triggers.append(check_time)

        was_active = condition_met

    return triggers


def _execute_tool(db: Session, tool_name: str, arguments: Dict) -> str:
    """Execute a tool and return JSON result."""
    try:
        # Extract exchange parameter with default "hyperliquid" for backward compatibility
        exchange = arguments.get("exchange", "hyperliquid")

        if tool_name == "get_kline_context":
            result = _tool_get_kline_context(
                db=db,
                symbol=arguments.get("symbol", "BTC"),
                timestamps=arguments.get("timestamps", []),
                time_window=arguments.get("time_window", "5m"),
                exchange=exchange
            )
        elif tool_name == "get_indicators_batch":
            result = _tool_get_indicators_batch(
                db=db,
                symbol=arguments.get("symbol", "BTC"),
                indicators=arguments.get("indicators", []),
                time_window=arguments.get("time_window", "5m"),
                exchange=exchange
            )
        elif tool_name == "predict_signal_combination":
            result = _tool_predict_signal_combination(
                db=db,
                symbol=arguments.get("symbol", "BTC"),
                signals=arguments.get("signals", []),
                logic=arguments.get("logic", "AND"),
                exchange=exchange
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


def _format_tool_calls_log(tool_calls_log: List[Dict]) -> str:
    """Format tool calls log as Markdown for storage and display."""
    if not tool_calls_log:
        return ""

    lines = ["<details>", "<summary>Analysis Process</summary>", ""]

    for entry in tool_calls_log:
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
    account_id: Optional[int] = None,
    user_message: str = "",
    conversation_id: Optional[int] = None,
    user_id: int = 1,
    llm_config: Optional[Dict[str, Any]] = None
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
        # Get LLM config: either from llm_config param or from account_id
        if llm_config:
            # Use provided llm_config (e.g., from Hyper AI sub-agent call)
            api_config = {
                "base_url": llm_config.get("base_url"),
                "api_key": llm_config.get("api_key"),
                "model": llm_config.get("model"),
                "api_format": llm_config.get("api_format", "openai")
            }
            model_name = llm_config.get("model", "unknown")
            account = None
        else:
            # Original logic: get from AI account
            account = db.query(Account).filter(
                Account.id == account_id,
                Account.account_type == "AI",
                Account.is_deleted != True
            ).first()

            if not account:
                yield _sse_event("error", {"message": "AI account not found"})
                return

            api_config = {
                "base_url": account.base_url,
                "api_key": account.api_key,
                "model": account.model,
                "api_format": detect_api_format(account.base_url)[1] or "openai"
            }
            model_name = account.model

        yield _sse_event("status", {"message": f"Using model: {model_name}"})

        # Get or create conversation
        conversation = None
        if conversation_id:
            conversation = db.query(AiSignalConversation).filter(
                AiSignalConversation.id == conversation_id,
                AiSignalConversation.user_id == user_id
            ).first()

        is_new_conversation = False
        if not conversation:
            title = user_message[:50] + "..." if len(user_message) > 50 else user_message
            conversation = AiSignalConversation(user_id=user_id, title=title)
            db.add(conversation)
            db.flush()
            is_new_conversation = True

        # Notify frontend of conversation ID immediately (so it can recover from interruptions)
        if is_new_conversation:
            yield _sse_event("conversation_created", {"conversation_id": conversation.id})

        # Save user message
        user_msg = AiSignalMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_message
        )
        db.add(user_msg)
        db.flush()

        # Build message history with compression support
        from services.ai_context_compression_service import (
            compress_messages, update_compression_points,
            restore_tool_calls_to_messages,
            get_last_compression_point, filter_messages_by_compression,
        )

        messages = [{"role": "system", "content": SIGNAL_SYSTEM_PROMPT}]

        # Check compression points - inject summary for compressed messages
        cp = get_last_compression_point(conversation)
        if cp and cp.get("summary"):
            messages.append({
                "role": "system",
                "content": f"[Previous conversation summary]\n{cp['summary']}"
            })

        # Load history, filter by compression point
        history_messages = db.query(AiSignalMessage).filter(
            AiSignalMessage.conversation_id == conversation.id,
            AiSignalMessage.id != user_msg.id
        ).order_by(AiSignalMessage.created_at).limit(100).all()

        history_messages = filter_messages_by_compression(history_messages, cp)

        last_message_id = history_messages[-1].id if history_messages else None

        # Restore tool_calls into proper LLM message format
        history_dicts = [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls_log": m.tool_calls_log,
                "reasoning_snapshot": m.reasoning_snapshot,
            }
            for m in history_messages
        ]
        restored = restore_tool_calls_to_messages(history_dicts, api_config.get("api_format", "openai"), model=api_config.get("model", ""))
        messages.extend(restored)
        messages.append({"role": "user", "content": user_message})

        # Apply compression if needed (api_config already set above)
        comp_result = compress_messages(messages, api_config, db=db)
        messages = comp_result["messages"]

        # Update compression_points if compression occurred
        if comp_result["compressed"] and comp_result["summary"] and last_message_id:
            update_compression_points(
                conversation, last_message_id,
                comp_result["summary"], comp_result["compressed_at"], db
            )

        # Build endpoints and headers
        api_format = api_config.get("api_format", "openai")
        if api_format == 'anthropic':
            ep, _ = detect_api_format(api_config["base_url"])
            endpoints = [ep] if ep else []
        else:
            endpoints = build_chat_completion_endpoints(api_config["base_url"], api_config["model"])
        if not endpoints:
            yield _sse_event("error", {"message": "Invalid base_url configuration"})
            return

        # Use unified headers builder (see build_llm_headers in ai_decision_service)
        headers = build_llm_headers(api_format, api_config["api_key"], api_config["base_url"])

        yield _sse_event("status", {"message": "Analyzing your request..."})

        # Function Calling loop (max 30 rounds)
        max_tool_rounds = 30
        tool_round = 0
        assistant_content = None
        # Accumulate tool calls and reasoning for storage (aligned with other AI assistants)
        tool_calls_log = []
        reasoning_snapshot_parts = []

        while tool_round < max_tool_rounds:
            tool_round += 1
            is_last_round = (tool_round == max_tool_rounds)

            yield _sse_event("tool_round", {
                "round": tool_round,
                "max_rounds": max_tool_rounds
            })

            if is_last_round:
                messages.append({
                    "role": "user",
                    "content": "Output the final signal configuration now. Include the ```signal-config``` block."
                })

            # Use unified payload builder (see build_llm_payload in ai_decision_service)
            if api_format == 'anthropic':
                sys_prompt, anthropic_messages = convert_messages_to_anthropic(messages)
                tools_for_round = convert_tools_to_anthropic(SIGNAL_TOOLS) if not is_last_round else None
                request_payload = build_llm_payload(
                    model=api_config["model"],
                    messages=[{"role": "system", "content": sys_prompt}] + anthropic_messages,
                    api_format=api_format,
                    tools=tools_for_round,
                )
            else:
                request_payload = build_llm_payload(
                    model=api_config["model"],
                    messages=messages,
                    api_format=api_format,
                    tools=SIGNAL_TOOLS if not is_last_round else None,
                    tool_choice="auto" if not is_last_round else None,
                )

            # Call API
            response = None
            last_error = None
            last_status_code = None
            last_response_text = None

            for endpoint in endpoints:
                try:
                    response = requests.post(endpoint, json=request_payload, headers=headers, timeout=120)
                    last_status_code = response.status_code
                    last_response_text = response.text[:2000] if response.text else None
                    if response.status_code == 200:
                        break
                    else:
                        last_error = f"HTTP {response.status_code}"
                        logger.warning(f"[AI Signal Gen Stream {request_id}] Endpoint failed: {response.status_code} - {response.text[:500]}")
                except requests.exceptions.Timeout as e:
                    last_error = f"Timeout after 120s: {str(e)}"
                    logger.warning(f"[AI Signal Gen Stream {request_id}] Endpoint timeout: {e}")
                except requests.exceptions.ConnectionError as e:
                    last_error = f"Connection error: {str(e)}"
                    logger.warning(f"[AI Signal Gen Stream {request_id}] Connection error: {e}")
                except Exception as e:
                    last_error = f"{type(e).__name__}: {str(e)}"
                    logger.warning(f"[AI Signal Gen Stream {request_id}] Endpoint error: {type(e).__name__}: {e}")

            if not response or response.status_code != 200:
                error_parts = []
                if last_error:
                    error_parts.append(f"error={last_error}")
                if last_status_code:
                    error_parts.append(f"status={last_status_code}")
                if last_response_text:
                    error_parts.append(f"response={last_response_text[:500]}")
                error_detail = "; ".join(error_parts) if error_parts else "No response from API"
                logger.error(f"[AI Signal Gen Stream {request_id}] API failed at round {tool_round}: {error_detail}")
                system_logger.add_log("ERROR", "ai_signal_gen", f"API failed at round {tool_round}", {"error": error_detail, "request_id": request_id})

                # If we have tool calls already, save as interrupted (recoverable)
                if tool_calls_log:
                    reasoning_snapshot = "\n\n---\n\n".join(reasoning_snapshot_parts) if reasoning_snapshot_parts else None
                    assistant_msg = AiSignalMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=f"**[Interrupted at round {tool_round}]** {error_detail}",
                        reasoning_snapshot=reasoning_snapshot,
                        tool_calls_log=json.dumps(tool_calls_log),
                        is_complete=False,
                        interrupt_reason=f"Round {tool_round}: {error_detail}"
                    )
                    db.add(assistant_msg)
                    db.commit()
                    yield _sse_event("interrupted", {
                        "message_id": assistant_msg.id,
                        "conversation_id": conversation.id,
                        "round": tool_round,
                        "error": error_detail
                    })
                else:
                    yield _sse_event("error", {"message": f"API request failed: {error_detail}"})
                return

            # Parse response based on API format
            try:
                response_json = response.json()
                if api_format == 'anthropic':
                    content_blocks = response_json.get("content", [])
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
                    api_tool_calls = tool_uses if tool_uses else None
                else:
                    message = response_json["choices"][0]["message"]
                    tool_calls = message.get("tool_calls", [])
                    reasoning_content = message.get("reasoning_content", "") or extract_reasoning(message)
                    content = message.get("content", "")
                    api_tool_calls = tool_calls if tool_calls else None
            except Exception as e:
                logger.error(f"[AI Signal Gen Stream {request_id}] Failed to parse response: {e}")
                system_logger.add_log("ERROR", "ai_signal_gen", f"Failed to parse response", {"error": str(e), "request_id": request_id})
                yield _sse_event("error", {"message": f"Failed to parse response: {e}"})
                return

            # Strip <thinking> text tags from content
            content, tag_thinking = strip_thinking_tags(content)
            if tag_thinking and not reasoning_content:
                reasoning_content = tag_thinking

            # Send reasoning content if present
            if reasoning_content:
                yield _sse_event("reasoning", {"content": reasoning_content})
                reasoning_snapshot_parts.append(reasoning_content)

            # Send content if present
            if content:
                yield _sse_event("content", {"content": content})

            if api_tool_calls:
                if api_format == 'anthropic':
                    messages.append({
                        "role": "assistant",
                        "content": content or "",
                        "tool_use_blocks": response_json.get("content", [])
                    })
                    for tool_use in api_tool_calls:
                        func_name = tool_use.get("name", "")
                        tool_id = tool_use.get("id", "")
                        func_args = tool_use.get("input", {})
                        yield _sse_event("tool_call", {"name": func_name, "arguments": func_args})
                        tool_result = _execute_tool(db, func_name, func_args)
                        tool_result_parsed = json.loads(tool_result)
                        yield _sse_event("tool_result", {"name": func_name, "result": tool_result_parsed})
                        tool_calls_log.append({"tool": func_name, "args": func_args, "result": tool_result})
                        messages.append({"role": "tool", "tool_call_id": tool_id, "content": tool_result})
                else:
                    assistant_msg_dict = {
                        "role": "assistant",
                        "content": content or "",
                        "tool_calls": api_tool_calls
                    }
                    if reasoning_content:
                        assistant_msg_dict["reasoning_content"] = reasoning_content
                    messages.append(assistant_msg_dict)
                    for tool_call in api_tool_calls:
                        func_name = tool_call["function"]["name"]
                        try:
                            func_args = json.loads(tool_call["function"]["arguments"])
                        except json.JSONDecodeError:
                            func_args = {}
                        yield _sse_event("tool_call", {"name": func_name, "arguments": func_args})
                        tool_result = _execute_tool(db, func_name, func_args)
                        tool_result_parsed = json.loads(tool_result)
                        yield _sse_event("tool_result", {"name": func_name, "result": tool_result_parsed})
                        tool_calls_log.append({"tool": func_name, "args": func_args, "result": tool_result})
                        messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": tool_result})
            else:
                # No tool calls - final response
                # Don't add reasoning here - tool_calls_log already has it via <details> format
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

        # Store content without analysis markdown (frontend renders from tool_calls_log/reasoning_snapshot)
        reasoning_snapshot = "\n\n---\n\n".join(reasoning_snapshot_parts) if reasoning_snapshot_parts else None

        # Save assistant message with tool calls log and reasoning
        assistant_msg = AiSignalMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_content,
            signal_configs=json.dumps(signal_configs) if signal_configs else None,
            reasoning_snapshot=reasoning_snapshot,
            tool_calls_log=json.dumps(tool_calls_log) if tool_calls_log else None,
            is_complete=True
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
            "elapsed": round(time.time() - start_time, 2),
            "tool_calls_log": tool_calls_log if tool_calls_log else None,
            "reasoning_snapshot": reasoning_snapshot if reasoning_snapshot else None,
            "compression_points": json.loads(conversation.compression_points) if conversation.compression_points else None,
        })

    except Exception as e:
        logger.error(f"[AI Signal Gen Stream {request_id}] Error: {e}", exc_info=True)
        system_logger.add_log("ERROR", "ai_signal_gen", f"Unexpected error in AI signal generation", {"error": str(e), "request_id": request_id})
        db.rollback()
        yield _sse_event("error", {"message": str(e)})
