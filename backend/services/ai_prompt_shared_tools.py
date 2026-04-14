"""
AI Prompt Shared Tools - Tools for AI Prompt Generation service

Provides tools for:
- Prompt context access (prompt content + bound traders)
- Trader details (config + signal pool)
- Decision history (list + details)
- Market data query (reuse from ai_program_service)
"""

import json
import logging
from typing import Dict, List, Any, Optional
import re

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)


# Tool definitions in OpenAI format
PROMPT_CONTEXT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_prompt_context",
            "description": "Get current prompt content and list of AI Traders using this prompt. Call this first to understand what you're editing.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_trader_details",
            "description": "Get AI Trader configuration including exchange, environment, leverage, selected symbols, and bound signal pool details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trader_id": {
                        "type": "integer",
                        "description": "AI Trader ID (account_id) to get details for"
                    }
                },
                "required": ["trader_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_decision_list",
            "description": "Get recent AI decision history (summary only). Returns decision IDs, time, trigger type, operation, and execution status. Use get_decision_details for full prompt/reasoning content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trader_id": {
                        "type": "integer",
                        "description": "AI Trader ID to get decisions for"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of decisions to return (default: 10, max: 20)",
                        "default": 10
                    }
                },
                "required": ["trader_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_decision_details",
            "description": "Get detailed info for specific decisions including prompt, reasoning, and decision output. Use fields parameter to control what to retrieve.",
            "parameters": {
                "type": "object",
                "properties": {
                    "decision_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of decision IDs to get details for (max 5)"
                    },
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["summary", "prompt", "reasoning", "decision"]
                        },
                        "description": "Fields to include: summary (basic info), prompt (rendered prompt), reasoning (AI thinking), decision (output JSON). Default: ['summary']"
                    }
                },
                "required": ["decision_ids"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_market_data",
            "description": "Query current market data and technical indicators for a symbol. Use this to understand actual indicator value ranges before writing thresholds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Trading symbol (e.g., BTC, ETH)"
                    },
                    "period": {
                        "type": "string",
                        "enum": ["1m", "5m", "15m", "1h", "4h", "1d"],
                        "description": "Time period for indicators (default: 1h)"
                    },
                    "exchange": {
                        "type": "string",
                        "enum": ["hyperliquid", "binance"],
                        "description": "Exchange to query data from (default: hyperliquid)"
                    }
                },
                "required": ["symbol"]
            }
        }
    }
]


def _extract_variables_from_prompt(prompt_text: str) -> List[str]:
    """Extract variable names from prompt text."""
    if not prompt_text:
        return []
    # Match {variable_name} or {variable_name}(params)
    pattern = r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}'
    variables = re.findall(pattern, prompt_text)
    return list(set(variables))


def execute_get_prompt_context(db: Session, prompt_id: Optional[int]) -> str:
    """
    Get prompt content and list of AI Traders using this prompt.

    Args:
        db: Database session
        prompt_id: Prompt template ID (optional, None for new prompt)

    Returns:
        JSON string with prompt info and bound traders
    """
    from database.models import PromptTemplate, AccountPromptBinding, Account, HyperliquidWallet, BinanceWallet
    from repositories.strategy_repo import get_strategy_by_account, parse_signal_pool_ids

    result = {
        "prompt": None,
        "bound_traders": [],
        "bound_count": 0
    }

    try:
        if not prompt_id:
            result["note"] = "No prompt_id provided. Creating new prompt."
            return json.dumps(result, indent=2, ensure_ascii=False)

        # Get prompt template
        template = db.get(PromptTemplate, prompt_id)
        if not template:
            return json.dumps({"error": f"Prompt template with id {prompt_id} not found"})

        # Extract variables from prompt
        variables = _extract_variables_from_prompt(template.template_text)

        result["prompt"] = {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "content_preview": template.template_text[:500] + "..." if len(template.template_text) > 500 else template.template_text,
            "content_length": len(template.template_text),
            "variables_used": variables[:20]  # Limit to 20 variables
        }

        # Find traders bound to this prompt
        bindings = db.query(AccountPromptBinding, Account).join(
            Account, AccountPromptBinding.account_id == Account.id
        ).filter(
            AccountPromptBinding.prompt_template_id == prompt_id,
            Account.is_active == "true"
        ).all()

        for binding, account in bindings:
            trader_info = {
                "trader_id": account.id,
                "trader_name": account.name,
                "exchange": None,
                "environment": None,
                "signal_pool_name": None
            }

            # Get strategy to find exchange and signal pool
            strategy = get_strategy_by_account(db, account.id)
            if strategy:
                trader_info["exchange"] = getattr(strategy, 'exchange', None) or "hyperliquid"

                # Get signal pool names
                pool_ids = parse_signal_pool_ids(strategy)
                if pool_ids:
                    pool_result = db.execute(
                        text("SELECT pool_name FROM signal_pools WHERE id = ANY(:ids) AND (is_deleted IS NULL OR is_deleted = false)"),
                        {"ids": pool_ids}
                    ).fetchall()
                    pool_names = [row[0] for row in pool_result]
                    trader_info["signal_pool_name"] = ", ".join(pool_names) if pool_names else None

            # Get environment from wallet
            if trader_info["exchange"] == "hyperliquid":
                wallet = db.query(HyperliquidWallet).filter(
                    HyperliquidWallet.account_id == account.id,
                    HyperliquidWallet.private_key_encrypted.isnot(None)
                ).first()
                if wallet:
                    trader_info["environment"] = wallet.environment
            elif trader_info["exchange"] == "binance":
                wallet = db.query(BinanceWallet).filter(
                    BinanceWallet.account_id == account.id,
                    BinanceWallet.api_key_encrypted.isnot(None)
                ).first()
                if wallet:
                    trader_info["environment"] = wallet.environment

            result["bound_traders"].append(trader_info)

        result["bound_count"] = len(result["bound_traders"])

        if result["bound_count"] == 0:
            result["note"] = "This prompt is not bound to any AI Trader yet."
        elif result["bound_count"] == 1:
            result["note"] = f"This prompt is used by 1 AI Trader. Use get_trader_details({result['bound_traders'][0]['trader_id']}) for more info."
        else:
            result["note"] = f"This prompt is used by {result['bound_count']} AI Traders. Consider compatibility when making changes."

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[get_prompt_context] Error: {e}")
        return json.dumps({"error": str(e)})


def execute_get_trader_details(db: Session, trader_id: int) -> str:
    """
    Get AI Trader configuration including exchange, environment, leverage,
    selected symbols, and bound signal pool details.

    Args:
        db: Database session
        trader_id: AI Trader ID (account_id)

    Returns:
        JSON string with trader config and signal pool details
    """
    from database.models import Account, HyperliquidWallet, BinanceWallet, AIDecisionLog
    from repositories.strategy_repo import get_strategy_by_account, parse_signal_pool_ids
    from datetime import datetime, timedelta

    try:
        # Get account
        account = db.query(Account).filter(
            Account.id == trader_id,
            Account.is_active == "true",
            Account.is_deleted != True
        ).first()

        if not account:
            return json.dumps({"error": f"AI Trader with id {trader_id} not found"})

        result = {
            "trader": {
                "id": account.id,
                "name": account.name,
                "exchange": None,
                "environment": None,
                "max_leverage": None,
                "selected_symbols": []
            },
            "signal_pool": None,
            "stats_24h": {
                "decision_count": 0,
                "trade_count": 0
            }
        }

        # Get strategy config
        strategy = get_strategy_by_account(db, trader_id)
        if strategy:
            result["trader"]["exchange"] = getattr(strategy, 'exchange', None) or "hyperliquid"

            # Get signal pool details
            pool_ids = parse_signal_pool_ids(strategy)
            if pool_ids:
                # Get pool info with signals
                pools_data = []
                for pool_id in pool_ids:
                    pool_row = db.execute(
                        text("SELECT id, pool_name, logic, symbols, signal_ids, source_type, source_config FROM signal_pools WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false)"),
                        {"id": pool_id}
                    ).fetchone()

                    if pool_row:
                        signal_ids = pool_row[4]
                        if isinstance(signal_ids, str):
                            signal_ids = json.loads(signal_ids)

                        symbols = pool_row[3]
                        if isinstance(symbols, str):
                            symbols = json.loads(symbols)
                        source_type = pool_row[5] or "market_signals"
                        source_config = pool_row[6]
                        if isinstance(source_config, str):
                            try:
                                source_config = json.loads(source_config)
                            except json.JSONDecodeError:
                                source_config = {}

                        # Get signal definitions
                        signals = []
                        if source_type == "market_signals" and signal_ids:
                            for sig_id in signal_ids:
                                sig_row = db.execute(
                                    text("SELECT signal_name, trigger_condition FROM signal_definitions WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false)"),
                                    {"id": sig_id}
                                ).fetchone()
                                if sig_row:
                                    condition = sig_row[1]
                                    if isinstance(condition, str):
                                        condition = json.loads(condition)
                                    signals.append({
                                        "name": sig_row[0],
                                        "metric": condition.get("metric"),
                                        "operator": condition.get("operator"),
                                        "threshold": condition.get("threshold"),
                                        "time_window": condition.get("time_window")
                                    })

                        pools_data.append({
                            "pool_id": pool_row[0],
                            "pool_name": pool_row[1],
                            "logic": pool_row[2] or "OR",
                            "symbols": symbols or [],
                            "source_type": source_type,
                            "source_config": source_config if source_type == "wallet_tracking" else {},
                            "signals": signals
                        })

                if len(pools_data) == 1:
                    result["signal_pool"] = pools_data[0]
                elif len(pools_data) > 1:
                    result["signal_pools"] = pools_data

        # Get environment and leverage from wallet
        exchange = result["trader"]["exchange"]
        if exchange == "hyperliquid":
            wallet = db.query(HyperliquidWallet).filter(
                HyperliquidWallet.account_id == trader_id,
                HyperliquidWallet.private_key_encrypted.isnot(None)
            ).first()
            if wallet:
                result["trader"]["environment"] = wallet.environment
                result["trader"]["max_leverage"] = wallet.max_leverage
                if wallet.selected_symbols:
                    symbols = wallet.selected_symbols
                    if isinstance(symbols, str):
                        symbols = json.loads(symbols)
                    result["trader"]["selected_symbols"] = symbols
        elif exchange == "binance":
            wallet = db.query(BinanceWallet).filter(
                BinanceWallet.account_id == trader_id,
                BinanceWallet.api_key_encrypted.isnot(None)
            ).first()
            if wallet:
                result["trader"]["environment"] = wallet.environment
                result["trader"]["max_leverage"] = wallet.max_leverage
                # Note: BinanceWallet doesn't have selected_symbols field
                # Binance traders use all available symbols

        # Get 24h stats
        since_24h = datetime.utcnow() - timedelta(hours=24)
        decision_count = db.query(AIDecisionLog).filter(
            AIDecisionLog.account_id == trader_id,
            AIDecisionLog.decision_time >= since_24h
        ).count()

        trade_count = db.query(AIDecisionLog).filter(
            AIDecisionLog.account_id == trader_id,
            AIDecisionLog.decision_time >= since_24h,
            AIDecisionLog.operation.in_(["buy", "sell", "close"]),
            AIDecisionLog.executed == "true"
        ).count()

        result["stats_24h"]["decision_count"] = decision_count
        result["stats_24h"]["trade_count"] = trade_count

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[get_trader_details] Error: {e}")
        return json.dumps({"error": str(e)})


def execute_get_decision_list(db: Session, trader_id: int, limit: int = 10) -> str:
    """
    Get recent AI decision history (summary only).

    Args:
        db: Database session
        trader_id: AI Trader ID (account_id)
        limit: Max number of decisions to return (default: 10, max: 20)

    Returns:
        JSON string with decision summaries
    """
    from database.models import AIDecisionLog

    try:
        # Limit to reasonable range
        limit = min(max(limit, 1), 20)

        # Get total count
        total_count = db.query(AIDecisionLog).filter(
            AIDecisionLog.account_id == trader_id
        ).count()

        # Get recent decisions
        decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.account_id == trader_id
        ).order_by(AIDecisionLog.decision_time.desc()).limit(limit).all()

        result = {
            "trader_id": trader_id,
            "decisions": [],
            "total": total_count,
            "showing": len(decisions)
        }

        for d in decisions:
            # Determine trigger type
            if d.signal_trigger_id:
                trigger = f"signal:{d.signal_trigger_id}"
            else:
                trigger = "scheduled"

            decision_info = {
                "id": d.id,
                "time": d.decision_time.strftime("%Y-%m-%d %H:%M UTC") if d.decision_time else None,
                "trigger": trigger,
                "symbol": d.symbol,
                "operation": d.operation,
                "target_portion": float(d.target_portion) if d.target_portion else None,
                "executed": d.executed,
                "exchange": d.exchange or "hyperliquid",
                "has_prompt": bool(d.prompt_snapshot),
                "has_reasoning": bool(d.reasoning_snapshot)
            }

            # Add PnL if available
            if d.realized_pnl is not None:
                decision_info["realized_pnl"] = float(d.realized_pnl)

            result["decisions"].append(decision_info)

        if total_count == 0:
            result["note"] = "No decisions found for this trader."
        else:
            result["note"] = f"Showing {len(decisions)} of {total_count} decisions. Use get_decision_details to see prompt/reasoning."

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[get_decision_list] Error: {e}")
        return json.dumps({"error": str(e)})


def execute_get_decision_details(db: Session, decision_ids: List[int], fields: List[str] = None) -> str:
    """
    Get detailed info for specific decisions.

    Args:
        db: Database session
        decision_ids: List of decision IDs to get details for (max 5)
        fields: Fields to include: summary, prompt, reasoning, decision

    Returns:
        JSON string with decision details
    """
    from database.models import AIDecisionLog

    try:
        if fields is None:
            fields = ["summary"]

        # Limit to 5 decisions
        decision_ids = decision_ids[:5]

        if not decision_ids:
            return json.dumps({"error": "No decision_ids provided"})

        # Get decisions
        decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.id.in_(decision_ids)
        ).all()

        if not decisions:
            return json.dumps({"error": f"No decisions found for ids: {decision_ids}"})

        result = {"decisions": []}

        for d in decisions:
            item = {"id": d.id}

            # Summary (always included if requested or as default)
            if "summary" in fields:
                # Determine trigger type
                if d.signal_trigger_id:
                    trigger = f"signal:{d.signal_trigger_id}"
                else:
                    trigger = "scheduled"

                item["summary"] = {
                    "time": d.decision_time.strftime("%Y-%m-%d %H:%M UTC") if d.decision_time else None,
                    "trigger": trigger,
                    "symbol": d.symbol,
                    "operation": d.operation,
                    "target_portion": float(d.target_portion) if d.target_portion else None,
                    "executed": d.executed,
                    "exchange": d.exchange or "hyperliquid"
                }

                if d.realized_pnl is not None:
                    item["summary"]["realized_pnl"] = float(d.realized_pnl)

            # Prompt snapshot
            if "prompt" in fields:
                if d.prompt_snapshot:
                    item["prompt"] = d.prompt_snapshot
                else:
                    item["prompt"] = None
                    item["prompt_note"] = "No prompt snapshot available for this decision"

            # Reasoning snapshot
            if "reasoning" in fields:
                if d.reasoning_snapshot:
                    item["reasoning"] = d.reasoning_snapshot
                else:
                    item["reasoning"] = None
                    item["reasoning_note"] = "No reasoning snapshot available for this decision"

            # Decision output
            if "decision" in fields:
                if d.decision_snapshot:
                    try:
                        if isinstance(d.decision_snapshot, str):
                            item["decision"] = json.loads(d.decision_snapshot)
                        else:
                            item["decision"] = d.decision_snapshot
                    except json.JSONDecodeError:
                        item["decision"] = d.decision_snapshot
                else:
                    item["decision"] = None
                    item["decision_note"] = "No decision snapshot available"

            result["decisions"].append(item)

        result["fields_requested"] = fields
        result["count"] = len(result["decisions"])

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[get_decision_details] Error: {e}")
        return json.dumps({"error": str(e)})


def execute_query_market_data(db: Session, symbol: str, period: str = "1h", exchange: str = "hyperliquid") -> str:
    """
    Query current market data for AI to understand indicator value ranges.
    Reuses logic from ai_program_service._query_market_data.

    Args:
        db: Database session
        symbol: Trading symbol (e.g., BTC, ETH)
        period: Time period for indicators (e.g., 1h, 5m)
        exchange: Exchange to query from ('hyperliquid' or 'binance')

    Returns:
        JSON string with market data and indicators
    """
    try:
        from program_trader.data_provider import DataProvider
        import requests

        # Get current price based on exchange
        price = None
        if exchange == "binance":
            # Use Binance public API to get price
            binance_symbol = f"{symbol.upper()}USDT"
            try:
                resp = requests.get(
                    "https://fapi.binance.com/fapi/v1/ticker/price",
                    params={"symbol": binance_symbol},
                    timeout=5
                )
                if resp.status_code == 200:
                    price = float(resp.json().get("price", 0))
            except Exception as e:
                logger.warning(f"[query_market_data] Failed to get Binance price: {e}")
        else:
            from services.hyperliquid_market_data import get_last_price_from_hyperliquid
            price = get_last_price_from_hyperliquid(symbol, "mainnet")

        # Create data provider with exchange parameter
        data_provider = DataProvider(db=db, account_id=0, environment="mainnet", exchange=exchange)

        # Get all indicators
        indicators = {}
        for ind in ["RSI14", "RSI7", "MA5", "MA10", "MA20", "EMA20", "EMA50", "EMA100",
                    "MACD", "BOLL", "ATR14", "VWAP", "STOCH", "OBV"]:
            result = data_provider.get_indicator(symbol, ind, period)
            if result:
                indicators[ind] = result

        # Get all flow metrics
        flow_metrics = {}
        for metric in ["CVD", "OI", "OI_DELTA", "TAKER", "FUNDING", "DEPTH", "IMBALANCE"]:
            result = data_provider.get_flow(symbol, metric, period)
            if result:
                flow_metrics[metric] = result

        # Get regime
        regime = data_provider.get_regime(symbol, period)

        # Format response
        result = {
            "symbol": symbol,
            "period": period,
            "exchange": exchange,
            "current_price": float(price) if price else None,
            "indicators": indicators,
            "flow_metrics": flow_metrics,
            "regime": {"regime": regime.regime, "confidence": regime.conf} if regime else None
        }

        return json.dumps(result, indent=2)

    except Exception as e:
        logger.error(f"[query_market_data] Error: {e}")
        return json.dumps({"error": str(e)})
