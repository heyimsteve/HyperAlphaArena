"""
Strategy Analytics API routes.
Provides multi-dimensional analysis of trading decisions and performance.
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, case, and_, or_
from sqlalchemy.orm import Session

from database.connection import SessionLocal
from database.models import AIDecisionLog, Account, PromptTemplate, ProgramExecutionLog, TradingProgram
from database.snapshot_connection import SnapshotSessionLocal
from database.snapshot_models import HyperliquidTrade, HyperliquidAccountSnapshot
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============== Pydantic Models ==============

class MetricsResponse(BaseModel):
    total_pnl: float
    total_fee: float
    net_pnl: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_win: Optional[float]
    avg_loss: Optional[float]
    profit_factor: Optional[float]


class DataCompleteness(BaseModel):
    total_decisions: int
    with_strategy: int
    with_signal: int
    with_pnl: int


class TriggerTypeBreakdown(BaseModel):
    count: int
    net_pnl: float


# ============== Helper Functions ==============

def get_fees_for_decisions(decisions: List[AIDecisionLog]) -> Dict[int, float]:
    """
    Batch query HyperliquidTrade to get total fees for each decision.
    Returns a dict mapping decision_id -> total_fee.
    """
    if not decisions:
        return {}

    # Collect all order IDs (main, tp, sl)
    order_ids = set()
    decision_orders: Dict[int, List[str]] = {}  # decision_id -> list of order_ids

    for d in decisions:
        orders = []
        if d.hyperliquid_order_id:
            order_ids.add(d.hyperliquid_order_id)
            orders.append(d.hyperliquid_order_id)
        if d.tp_order_id:
            order_ids.add(d.tp_order_id)
            orders.append(d.tp_order_id)
        if d.sl_order_id:
            order_ids.add(d.sl_order_id)
            orders.append(d.sl_order_id)
        decision_orders[d.id] = orders

    if not order_ids:
        return {d.id: 0.0 for d in decisions}

    # Batch query fees from HyperliquidTrade
    fee_map: Dict[str, float] = {}
    try:
        snapshot_db = SnapshotSessionLocal()
        trades = snapshot_db.query(HyperliquidTrade).filter(
            HyperliquidTrade.order_id.in_(list(order_ids))
        ).all()
        for t in trades:
            if t.order_id:
                fee_map[str(t.order_id)] = float(t.fee or 0)
        snapshot_db.close()
    except Exception as e:
        logger.warning(f"Failed to fetch fees from HyperliquidTrade: {e}")

    # Calculate total fee for each decision
    result: Dict[int, float] = {}
    for d in decisions:
        total_fee = 0.0
        for oid in decision_orders.get(d.id, []):
            total_fee += fee_map.get(oid, 0.0)
        result[d.id] = total_fee

    return result


def calculate_metrics(records: List[Dict]) -> Dict[str, Any]:
    """Calculate standard metrics from a list of decision records."""
    if not records:
        return {
            "total_pnl": 0.0,
            "total_fee": 0.0,
            "net_pnl": 0.0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "avg_win": None,
            "avg_loss": None,
            "profit_factor": None,
        }

    total_pnl = sum(r.get("pnl", 0) or 0 for r in records)
    total_fee = sum(r.get("fee", 0) or 0 for r in records)
    net_pnl = total_pnl - total_fee

    wins = [r for r in records if (r.get("pnl") or 0) > 0]
    losses = [r for r in records if (r.get("pnl") or 0) < 0]

    win_count = len(wins)
    loss_count = len(losses)
    trade_count = len(records)
    win_rate = win_count / trade_count if trade_count > 0 else 0.0

    total_win = sum(r.get("pnl", 0) or 0 for r in wins)
    total_loss = abs(sum(r.get("pnl", 0) or 0 for r in losses))

    avg_win = total_win / win_count if win_count > 0 else None
    avg_loss = -total_loss / loss_count if loss_count > 0 else None
    profit_factor = total_win / total_loss if total_loss > 0 else None

    return {
        "total_pnl": round(total_pnl, 2),
        "total_fee": round(total_fee, 2),
        "net_pnl": round(net_pnl, 2),
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2) if avg_win else None,
        "avg_loss": round(avg_loss, 2) if avg_loss else None,
        "profit_factor": round(profit_factor, 2) if profit_factor else None,
    }


def get_trigger_type(decision: AIDecisionLog) -> str:
    """Determine trigger type for a decision."""
    if decision.signal_trigger_id is not None:
        return "signal"
    elif decision.executed == "true" and decision.operation in ("buy", "sell", "close"):
        return "scheduled"
    return "unknown"


def build_base_query(
    db: Session,
    start_date: Optional[date],
    end_date: Optional[date],
    environment: Optional[str],
    account_id: Optional[int],
    exchange: Optional[str] = None,
):
    """Build base query with common filters.

    Only includes decisions with non-zero realized_pnl (i.e., actually closed positions).
    This ensures statistics only count trades that have settled PnL,
    excluding opening trades (pnl=0) and unsync trades (pnl=NULL).
    """
    query = db.query(AIDecisionLog).filter(
        AIDecisionLog.operation.in_(["buy", "sell", "close"]),
        AIDecisionLog.executed == "true",
        AIDecisionLog.realized_pnl.isnot(None),  # Exclude unsync trades
        AIDecisionLog.realized_pnl != 0,  # Exclude opening trades (no settled PnL)
    )

    if start_date:
        query = query.filter(AIDecisionLog.decision_time >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.filter(AIDecisionLog.decision_time <= datetime.combine(end_date, datetime.max.time()))
    if environment and environment != "all":
        query = query.filter(AIDecisionLog.hyperliquid_environment == environment)
    if account_id:
        query = query.filter(AIDecisionLog.account_id == account_id)
    if exchange and exchange != "all":
        if exchange == "hyperliquid":
            query = query.filter(
                (AIDecisionLog.exchange == "hyperliquid") | (AIDecisionLog.exchange == None)
            )
        else:
            query = query.filter(AIDecisionLog.exchange == exchange)

    return query


# ============== API Endpoints ==============

@router.get("/summary")
def get_analytics_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get overall analytics summary (AI Decision + Program Decision combined)."""
    # === AI Decision data ===
    ai_query = build_base_query(db, start_date, end_date, environment, account_id, exchange)
    decisions = ai_query.all()
    fee_map = get_fees_for_decisions(decisions)

    ai_records = []
    ai_signal_records = []
    ai_scheduled_records = []
    ai_unknown_records = []

    with_strategy = 0
    with_signal = 0
    ai_with_pnl = 0

    for d in decisions:
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = fee_map.get(d.id, 0.0)
        record = {"pnl": pnl, "fee": fee}
        ai_records.append(record)

        trigger_type = get_trigger_type(d)
        if trigger_type == "signal":
            ai_signal_records.append(record)
        elif trigger_type == "scheduled":
            ai_scheduled_records.append(record)
        else:
            ai_unknown_records.append(record)

        if d.prompt_template_id:
            with_strategy += 1
        if d.signal_trigger_id:
            with_signal += 1
        if d.realized_pnl:
            ai_with_pnl += 1

    # === Program Decision data ===
    prog_query = build_program_base_query(db, start_date, end_date, environment, account_id, exchange)
    prog_logs = prog_query.all()
    prog_fee_map = get_fees_for_program_logs(prog_logs)

    prog_records = []
    prog_signal_records = []
    prog_scheduled_records = []

    with_program = 0
    prog_with_signal = 0
    prog_with_pnl = 0

    for log in prog_logs:
        pnl = float(log.realized_pnl) if log.realized_pnl else 0
        fee = prog_fee_map.get(log.id, 0.0)

        if pnl == 0:
            continue

        record = {"pnl": pnl, "fee": fee}
        prog_records.append(record)

        trigger_type = log.trigger_type or "scheduled"
        if trigger_type == "signal":
            prog_signal_records.append(record)
        else:
            prog_scheduled_records.append(record)

        if log.program_id:
            with_program += 1
        if log.signal_pool_id:
            prog_with_signal += 1
        prog_with_pnl += 1

    # === Combined metrics ===
    all_records = ai_records + prog_records
    overview = calculate_metrics(all_records)

    return {
        "period": {
            "start": start_date.isoformat() if start_date else None,
            "end": end_date.isoformat() if end_date else None,
        },
        "overview": overview,
        "data_completeness": {
            "total_decisions": len(decisions),
            "total_program_executions": len(prog_logs),
            "with_strategy": with_strategy,
            "with_program": with_program,
            "with_signal": with_signal + prog_with_signal,
            "with_pnl": ai_with_pnl + prog_with_pnl,
        },
        "by_trigger_type": {
            "signal": {
                "count": len(ai_signal_records) + len(prog_signal_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in ai_signal_records + prog_signal_records), 2),
            },
            "scheduled": {
                "count": len(ai_scheduled_records) + len(prog_scheduled_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in ai_scheduled_records + prog_scheduled_records), 2),
            },
            "unknown": {
                "count": len(ai_unknown_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in ai_unknown_records), 2),
            },
        },
        "by_source": {
            "ai_decision": {
                "count": len(ai_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in ai_records), 2),
            },
            "program": {
                "count": len(prog_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in prog_records), 2),
            },
        },
    }


@router.get("/by-strategy")
def get_analytics_by_strategy(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by strategy (prompt template)."""
    query = build_base_query(db, start_date, end_date, environment, account_id, exchange)
    decisions = query.all()

    # Get fees for all decisions
    fee_map = get_fees_for_decisions(decisions)

    # Group by strategy
    by_strategy: Dict[Optional[int], List[Dict]] = {}
    strategy_names: Dict[int, str] = {}

    for d in decisions:
        strategy_id = d.prompt_template_id
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = fee_map.get(d.id, 0.0)
        record = {
            "pnl": pnl,
            "fee": fee,
            "trigger_type": get_trigger_type(d),
        }

        if strategy_id not in by_strategy:
            by_strategy[strategy_id] = []
        by_strategy[strategy_id].append(record)

    # Get strategy names
    strategy_ids = [sid for sid in by_strategy.keys() if sid is not None]
    if strategy_ids:
        templates = db.query(PromptTemplate).filter(
            PromptTemplate.id.in_(strategy_ids),
            PromptTemplate.is_deleted == "false"
        ).all()
        strategy_names = {t.id: t.name for t in templates}

    # Build response
    items = []
    for strategy_id, records in by_strategy.items():
        if strategy_id is None:
            continue

        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] == "scheduled"]

        items.append({
            "strategy_id": strategy_id,
            "strategy_name": strategy_names.get(strategy_id, f"Strategy {strategy_id}"),
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2)},
            },
        })

    # Sort by net_pnl descending
    items.sort(key=lambda x: x["metrics"]["net_pnl"], reverse=True)

    # Unattributed (no strategy)
    unattributed_records = by_strategy.get(None, [])

    return {
        "items": items,
        "unattributed": {
            "count": len(unattributed_records),
            "metrics": calculate_metrics(unattributed_records) if unattributed_records else None,
        },
    }


@router.get("/by-account")
def get_analytics_by_account(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by account."""
    query = build_base_query(db, start_date, end_date, environment, None, exchange)
    decisions = query.all()

    # Get fees for all decisions
    fee_map = get_fees_for_decisions(decisions)

    # Group by account
    by_account: Dict[Optional[int], List[Dict]] = {}

    for d in decisions:
        account_id = d.account_id
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = fee_map.get(d.id, 0.0)
        record = {"pnl": pnl, "fee": fee, "trigger_type": get_trigger_type(d)}

        if account_id not in by_account:
            by_account[account_id] = []
        by_account[account_id].append(record)

    # Get account info (name, current model)
    account_ids = [aid for aid in by_account.keys() if aid is not None]
    account_info: Dict[int, Dict] = {}
    if account_ids:
        accounts = db.query(Account).filter(
            Account.id.in_(account_ids),
            Account.is_deleted != True
        ).all()
        account_info = {
            a.id: {"name": a.name, "model": a.model, "environment": a.hyperliquid_environment}
            for a in accounts
        }

    # Build response
    items = []
    for account_id, records in by_account.items():
        if account_id is None:
            continue

        info = account_info.get(account_id, {})
        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] == "scheduled"]

        items.append({
            "account_id": account_id,
            "account_name": info.get("name", f"Account {account_id}"),
            "model": info.get("model"),
            "environment": info.get("environment"),
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2)},
            },
        })

    # Sort by net_pnl descending
    items.sort(key=lambda x: x["metrics"]["net_pnl"], reverse=True)

    # Unattributed (no account)
    unattributed_records = by_account.get(None, [])

    return {
        "items": items,
        "unattributed": {
            "count": len(unattributed_records),
            "metrics": calculate_metrics(unattributed_records) if unattributed_records else None,
        },
    }


@router.get("/by-symbol")
def get_analytics_by_symbol(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by trading symbol."""
    query = build_base_query(db, start_date, end_date, environment, account_id, exchange)
    decisions = query.all()

    # Get fees for all decisions
    fee_map = get_fees_for_decisions(decisions)

    # Group by symbol
    by_symbol: Dict[Optional[str], List[Dict]] = {}

    for d in decisions:
        symbol = d.symbol
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = fee_map.get(d.id, 0.0)
        record = {"pnl": pnl, "fee": fee, "trigger_type": get_trigger_type(d)}

        if symbol not in by_symbol:
            by_symbol[symbol] = []
        by_symbol[symbol].append(record)

    # Build response
    items = []
    for symbol, records in by_symbol.items():
        if symbol is None:
            continue

        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] == "scheduled"]

        items.append({
            "symbol": symbol,
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2)},
            },
        })

    # Sort by net_pnl descending
    items.sort(key=lambda x: x["metrics"]["net_pnl"], reverse=True)

    # Unattributed (no symbol)
    unattributed_records = by_symbol.get(None, [])

    return {
        "items": items,
        "unattributed": {
            "count": len(unattributed_records),
            "metrics": calculate_metrics(unattributed_records) if unattributed_records else None,
        },
    }


@router.get("/by-operation")
def get_analytics_by_operation(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by operation type (buy/sell/close)."""
    query = build_base_query(db, start_date, end_date, environment, account_id, exchange)
    decisions = query.all()

    # Get fees for all decisions
    fee_map = get_fees_for_decisions(decisions)

    # Group by operation
    by_operation: Dict[str, List[Dict]] = {}

    for d in decisions:
        operation = d.operation or "unknown"
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = fee_map.get(d.id, 0.0)
        record = {"pnl": pnl, "fee": fee, "trigger_type": get_trigger_type(d)}

        if operation not in by_operation:
            by_operation[operation] = []
        by_operation[operation].append(record)

    # Build response
    items = []
    for operation, records in by_operation.items():
        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] == "scheduled"]

        items.append({
            "operation": operation,
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2)},
            },
        })

    # Sort by trade_count descending
    items.sort(key=lambda x: x["metrics"]["trade_count"], reverse=True)

    return {"items": items}


@router.get("/by-trigger-type")
def get_analytics_by_trigger_type(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by trigger type (signal/scheduled/unknown)."""
    query = build_base_query(db, start_date, end_date, environment, account_id, exchange)
    decisions = query.all()

    # Get fees for all decisions
    fee_map = get_fees_for_decisions(decisions)

    # Group by trigger type
    by_trigger: Dict[str, List[Dict]] = {"signal": [], "scheduled": [], "unknown": []}

    for d in decisions:
        trigger_type = get_trigger_type(d)
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = fee_map.get(d.id, 0.0)
        record = {"pnl": pnl, "fee": fee}
        by_trigger[trigger_type].append(record)

    # Build response
    items = []
    for trigger_type in ["signal", "scheduled", "unknown"]:
        records = by_trigger[trigger_type]
        if records:
            items.append({
                "trigger_type": trigger_type,
                "metrics": calculate_metrics(records),
            })

    # Sort by trade_count descending
    items.sort(key=lambda x: x["metrics"]["trade_count"], reverse=True)

    return {"items": items}


@router.get("/by-factor")
def get_analytics_by_factor(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by factor signal triggers."""
    from database.models import SignalTriggerLog

    query = build_base_query(db, start_date, end_date, environment, account_id, exchange)
    decisions = query.all()
    fee_map = get_fees_for_decisions(decisions)

    # Collect decision IDs that have signal_trigger_id
    trigger_ids = set()
    decision_by_trigger: Dict[int, List] = {}
    for d in decisions:
        if d.signal_trigger_id:
            trigger_ids.add(d.signal_trigger_id)
            decision_by_trigger.setdefault(d.signal_trigger_id, []).append(d)

    if not trigger_ids:
        return {"items": []}

    # Batch load signal trigger logs that have factor data
    triggers = db.query(SignalTriggerLog).filter(
        SignalTriggerLog.id.in_(list(trigger_ids)),
    ).all()

    # Group by factor name (extracted from trigger_value JSON)
    import json as _json
    by_factor: Dict[str, List[Dict]] = {}
    for trig in triggers:
        # Parse trigger_value JSON to find factor info
        if not trig.trigger_value:
            continue
        try:
            tv = trig.trigger_value if isinstance(trig.trigger_value, dict) else _json.loads(trig.trigger_value)
        except (ValueError, TypeError):
            continue
        # Extract factor name from signals_triggered entries
        for sig in tv.get("signals_triggered", []):
            factor_name = sig.get("signal_name") or sig.get("metric")
            if not factor_name:
                continue
        for d in decision_by_trigger.get(trig.id, []):
            pnl = float(d.realized_pnl) if d.realized_pnl else 0
            fee = fee_map.get(d.id, 0.0)
            by_factor.setdefault(factor_name, []).append({"pnl": pnl, "fee": fee})

    items = []
    for factor_name, records in by_factor.items():
        items.append({
            "factor_name": factor_name,
            "metrics": calculate_metrics(records),
        })

    items.sort(key=lambda x: x["metrics"]["trade_count"], reverse=True)
    return {"items": items}


# ============== AI Attribution Analysis Routes ==============

from fastapi.responses import StreamingResponse
from pydantic import BaseModel as PydanticBaseModel
from services.ai_attribution_service import (
    generate_attribution_analysis_stream,
    get_attribution_conversations,
    get_attribution_messages
)


class AiAttributionChatRequest(PydanticBaseModel):
    accountId: int
    userMessage: str
    conversationId: Optional[int] = None
    # SSE direct streaming is unstable (frontend disconnect = task abort). Do NOT set to False.
    useBackgroundTask: bool = True


@router.post("/ai-attribution/chat-stream")
async def ai_attribution_chat_stream(
    request: AiAttributionChatRequest,
    db: Session = Depends(get_db)
):
    """
    SSE streaming endpoint for AI attribution analysis chat.

    Supports two modes:
    - SSE streaming (default): Returns Server-Sent Events directly
    - Background task (useBackgroundTask=true): Returns task_id for polling
    """
    from services.ai_stream_service import get_buffer_manager, generate_task_id, run_ai_task_in_background
    from database.connection import SessionLocal

    # Background task mode
    if request.useBackgroundTask:
        task_id = generate_task_id("attribution")
        manager = get_buffer_manager()

        # Check for existing running task
        if request.conversationId:
            existing = manager.get_pending_task_for_conversation(request.conversationId)
            if existing:
                return {"task_id": existing.task_id, "status": "already_running"}

        manager.create_task(task_id, conversation_id=request.conversationId)

        # Capture request data
        account_id = request.accountId
        user_message = request.userMessage
        conversation_id = request.conversationId

        def generator_func():
            bg_db = SessionLocal()
            try:
                yield from generate_attribution_analysis_stream(
                    db=bg_db,
                    account_id=account_id,
                    user_message=user_message,
                    conversation_id=conversation_id
                )
            finally:
                bg_db.close()

        run_ai_task_in_background(task_id, generator_func)
        return {"task_id": task_id, "status": "started"}

    # SSE streaming mode (default)
    return StreamingResponse(
        generate_attribution_analysis_stream(
            db=db,
            account_id=request.accountId,
            user_message=request.userMessage,
            conversation_id=request.conversationId
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/ai-attribution/conversations")
async def list_attribution_conversations(db: Session = Depends(get_db)):
    """Get list of AI attribution analysis conversations."""
    conversations = get_attribution_conversations(db)
    return {"conversations": conversations}


@router.get("/ai-attribution/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: int,
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """Get messages for a specific conversation with compression points and token usage."""
    import json as json_module
    from database.models import AiAttributionConversation, HyperAiProfile
    from services.ai_context_compression_service import calculate_token_usage, restore_tool_calls_to_messages

    messages = get_attribution_messages(db, conversation_id)

    # Get compression points from conversation
    compression_points = []
    conversation = db.query(AiAttributionConversation).filter(
        AiAttributionConversation.id == conversation_id
    ).first()
    if conversation and conversation.compression_points:
        try:
            compression_points = json_module.loads(conversation.compression_points)
        except (json_module.JSONDecodeError, TypeError):
            compression_points = []

    # Determine model for token calculation: prefer account model, fallback to global
    from database.models import AiAttributionMessage, Account
    from services.ai_context_compression_service import get_last_compression_point
    token_model = None
    api_format = "openai"
    if account_id:
        acct = db.query(Account).filter(Account.id == account_id, Account.is_deleted != True).first()
        if acct and acct.model:
            token_model = acct.model
            from services.ai_decision_service import detect_api_format
            _, fmt = detect_api_format(acct.base_url or "")
            api_format = fmt or "openai"
    if not token_model:
        profile = db.query(HyperAiProfile).first()
        if profile and profile.llm_model:
            token_model = profile.llm_model
            from services.hyper_ai_service import get_llm_config
            llm_config = get_llm_config(db)
            api_format = llm_config.get("api_format", "openai")

    # Calculate token usage (only messages after compression point + summary)
    token_usage = None
    if token_model and messages:
        cp = get_last_compression_point(conversation) if conversation else None
        cp_msg_id = cp.get("message_id", 0) if cp else 0

        history_orm = db.query(AiAttributionMessage).filter(
            AiAttributionMessage.conversation_id == conversation_id,
            AiAttributionMessage.id > cp_msg_id
        ).order_by(AiAttributionMessage.created_at).all()

        msg_dicts = [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls_log": m.tool_calls_log,
                "reasoning_snapshot": m.reasoning_snapshot,
            }
            for m in history_orm
        ]
        msg_list = restore_tool_calls_to_messages(msg_dicts, api_format, model=token_model or "")
        if cp and cp.get("summary"):
            msg_list.insert(0, {"role": "system", "content": cp["summary"]})
        token_usage = calculate_token_usage(msg_list, token_model)

    return {
        "messages": messages,
        "compression_points": compression_points,
        "token_usage": token_usage
    }


# ============== Trade Details API ==============

def get_exit_type(decision: AIDecisionLog) -> str:
    """Determine exit type based on operation and order IDs."""
    if decision.operation == 'close':
        return 'CLOSE'
    elif decision.tp_order_id or decision.sl_order_id:
        if decision.realized_pnl and float(decision.realized_pnl) > 0:
            return 'TP'
        else:
            return 'SL'
    return 'CLOSE'


def get_entry_type(decision: AIDecisionLog, db: Session) -> str:
    """Determine entry type (BUY/SELL/-).

    For close operations, look up the corresponding opening trade.
    """
    if decision.operation in ('buy', 'sell'):
        return decision.operation.upper()

    # For close operation, find the corresponding opening trade
    if decision.operation == 'close' and decision.symbol and decision.wallet_address:
        # Find the most recent buy/sell for the same symbol before this close
        opening_trade = db.query(AIDecisionLog).filter(
            AIDecisionLog.symbol == decision.symbol,
            AIDecisionLog.wallet_address == decision.wallet_address,
            AIDecisionLog.operation.in_(['buy', 'sell']),
            AIDecisionLog.decision_time < decision.decision_time
        ).order_by(AIDecisionLog.decision_time.desc()).first()

        if opening_trade:
            return opening_trade.operation.upper()

    return '-'


def get_entry_decision(decision: AIDecisionLog, db: Session) -> Optional[AIDecisionLog]:
    """Get the entry decision for a trade.

    For buy/sell operations, return the decision itself.
    For close operations, find the corresponding opening trade.
    """
    if decision.operation in ('buy', 'sell'):
        return decision

    # For close operation, find the corresponding opening trade
    if decision.operation == 'close' and decision.symbol and decision.wallet_address:
        return db.query(AIDecisionLog).filter(
            AIDecisionLog.symbol == decision.symbol,
            AIDecisionLog.wallet_address == decision.wallet_address,
            AIDecisionLog.operation.in_(['buy', 'sell']),
            AIDecisionLog.decision_time < decision.decision_time
        ).order_by(AIDecisionLog.decision_time.desc()).first()

    return None


def calculate_trade_tags(
    decisions: List[AIDecisionLog],
    account_equity: float,
    equity_threshold: float = 0.05
) -> Dict[int, List[str]]:
    """Calculate rule-based tags for each trade."""
    tags: Dict[int, List[str]] = {}
    loss_threshold = account_equity * equity_threshold if account_equity > 0 else 50.0

    # Sort by time for consecutive loss detection
    sorted_decisions = sorted(decisions, key=lambda d: d.decision_time or datetime.min)

    consecutive_losses = 0
    consecutive_loss_ids = []

    for d in sorted_decisions:
        d_tags = []
        pnl = float(d.realized_pnl) if d.realized_pnl else 0

        # Large loss: |pnl| > threshold
        if pnl < 0 and abs(pnl) > loss_threshold:
            d_tags.append('large_loss')

        # SL triggered: has sl_order_id and pnl < 0
        if d.sl_order_id and pnl < 0:
            d_tags.append('sl_triggered')

        # Consecutive losses tracking
        if pnl < 0:
            consecutive_losses += 1
            consecutive_loss_ids.append(d.id)
        else:
            # Mark previous consecutive losses if >= 3
            if consecutive_losses >= 3:
                for loss_id in consecutive_loss_ids:
                    if loss_id not in tags:
                        tags[loss_id] = []
                    if 'consecutive_loss' not in tags[loss_id]:
                        tags[loss_id].append('consecutive_loss')
            consecutive_losses = 0
            consecutive_loss_ids = []

        tags[d.id] = d_tags

    # Handle trailing consecutive losses
    if consecutive_losses >= 3:
        for loss_id in consecutive_loss_ids:
            if 'consecutive_loss' not in tags.get(loss_id, []):
                tags.setdefault(loss_id, []).append('consecutive_loss')

    return tags


@router.get("/trades")
def get_trade_details(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    tag_filter: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    """Get trade details with rule-based tags for micro-analysis.
    Combines both AIDecisionLog and ProgramExecutionLog records.
    """

    # === Helper: apply common filters ===
    def _apply_filters_ai(q):
        if start_date:
            q = q.filter(AIDecisionLog.decision_time >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            q = q.filter(AIDecisionLog.decision_time <= datetime.combine(end_date, datetime.max.time()))
        if environment and environment != "all":
            q = q.filter(AIDecisionLog.hyperliquid_environment == environment)
        if account_id:
            q = q.filter(AIDecisionLog.account_id == account_id)
        if exchange and exchange != "all":
            if exchange == "hyperliquid":
                q = q.filter((AIDecisionLog.exchange == "hyperliquid") | (AIDecisionLog.exchange == None))
            else:
                q = q.filter(AIDecisionLog.exchange == exchange)
        return q

    def _apply_filters_prog(q):
        if start_date:
            q = q.filter(ProgramExecutionLog.created_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            q = q.filter(ProgramExecutionLog.created_at <= datetime.combine(end_date, datetime.max.time()))
        if environment and environment != "all":
            q = q.filter(ProgramExecutionLog.environment == environment)
        if account_id:
            q = q.filter(ProgramExecutionLog.account_id == account_id)
        if exchange and exchange != "all":
            if exchange == "hyperliquid":
                q = q.filter((ProgramExecutionLog.exchange == "hyperliquid") | (ProgramExecutionLog.exchange == None))
            else:
                q = q.filter(ProgramExecutionLog.exchange == exchange)
        return q

    # === Query AI decisions ===
    ai_query = db.query(AIDecisionLog).filter(
        AIDecisionLog.realized_pnl.isnot(None),
        AIDecisionLog.realized_pnl != 0,
        AIDecisionLog.hyperliquid_order_id.isnot(None),
    )
    ai_query = _apply_filters_ai(ai_query)
    ai_decisions = ai_query.order_by(AIDecisionLog.decision_time.desc()).all()

    # === Query Program logs ===
    prog_query = db.query(ProgramExecutionLog).filter(
        ProgramExecutionLog.success == True,
        ProgramExecutionLog.decision_action.in_(["buy", "sell", "close"]),
        ProgramExecutionLog.realized_pnl.isnot(None),
        ProgramExecutionLog.realized_pnl != 0,
    )
    prog_query = _apply_filters_prog(prog_query)
    prog_logs = prog_query.order_by(ProgramExecutionLog.created_at.desc()).all()

    # === Get account equity ===
    account_equity = 0.0
    first_acct = (ai_decisions[0].account_id if ai_decisions
                  else prog_logs[0].account_id if prog_logs else None)
    if first_acct:
        env = environment if environment != "all" else "mainnet"
        try:
            snap_db = SnapshotSessionLocal()
            snapshot = snap_db.query(HyperliquidAccountSnapshot).filter(
                HyperliquidAccountSnapshot.account_id == first_acct,
                HyperliquidAccountSnapshot.environment == env,
            ).order_by(HyperliquidAccountSnapshot.created_at.desc()).first()
            if snapshot and snapshot.total_equity:
                account_equity = float(snapshot.total_equity)
            snap_db.close()
        except Exception as e:
            logger.warning(f"Failed to get account equity: {e}")

    # === Calculate tags (AI decisions only — program logs get empty tags) ===
    ai_trade_tags = calculate_trade_tags(ai_decisions, account_equity)

    # === Get fees ===
    ai_fee_map = get_fees_for_decisions(ai_decisions)
    prog_fee_map = get_fees_for_program_logs(prog_logs)

    # === Build unified trade list ===
    snapshot_db = SnapshotSessionLocal()
    unified: List[Dict] = []

    # -- AI decision trades --
    for d in ai_decisions:
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = ai_fee_map.get(d.id, 0.0)

        entry_decision = get_entry_decision(d, db)
        entry_time = entry_decision.decision_time.isoformat() if entry_decision and entry_decision.decision_time else None

        exit_type = get_exit_type(d)
        exit_time = None
        if exit_type in ('TP', 'SL'):
            tp_sl_order_id = d.tp_order_id or d.sl_order_id
            if tp_sl_order_id:
                hl_trade = snapshot_db.query(HyperliquidTrade).filter(
                    HyperliquidTrade.order_id == str(tp_sl_order_id)
                ).first()
                if hl_trade and hl_trade.trade_time:
                    exit_time = hl_trade.trade_time.isoformat()
            if not exit_time and d.pnl_updated_at:
                exit_time = d.pnl_updated_at.isoformat()
        if not exit_time:
            exit_time = d.decision_time.isoformat() if d.decision_time else None

        unified.append({
            "id": d.id,
            "source": "ai",
            "symbol": d.symbol,
            "sort_time": d.decision_time or datetime.min,
            "decision_time": d.decision_time.isoformat() if d.decision_time else None,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "entry_type": entry_decision.operation.upper() if entry_decision else '-',
            "exit_type": exit_type,
            "gross_pnl": round(pnl, 2),
            "fees": round(fee, 2),
            "net_pnl": round(pnl - fee, 2),
            "tags": ai_trade_tags.get(d.id, []),
            "hyperliquid_order_id": d.hyperliquid_order_id,
            "tp_order_id": d.tp_order_id,
            "sl_order_id": d.sl_order_id,
        })

    # -- Program execution trades --
    for p in prog_logs:
        pnl = float(p.realized_pnl) if p.realized_pnl else 0
        fee = prog_fee_map.get(p.id, 0.0)

        # Entry type from decision_action
        action = (p.decision_action or "").lower()
        if action in ("buy", "sell"):
            entry_type = action.upper()
        elif action == "close" and p.decision_symbol and p.wallet_address:
            opening = db.query(ProgramExecutionLog).filter(
                ProgramExecutionLog.decision_symbol == p.decision_symbol,
                ProgramExecutionLog.wallet_address == p.wallet_address,
                ProgramExecutionLog.decision_action.in_(["buy", "sell"]),
                ProgramExecutionLog.created_at < p.created_at,
            ).order_by(ProgramExecutionLog.created_at.desc()).first()
            entry_type = opening.decision_action.upper() if opening else "-"
        else:
            entry_type = "-"

        # Exit type
        if action == "close":
            exit_type = "CLOSE"
        elif p.tp_order_id or p.sl_order_id:
            exit_type = "TP" if pnl > 0 else "SL"
        else:
            exit_type = "CLOSE"

        # Exit time
        exit_time = None
        if exit_type in ("TP", "SL"):
            tp_sl_oid = p.tp_order_id or p.sl_order_id
            if tp_sl_oid:
                hl_trade = snapshot_db.query(HyperliquidTrade).filter(
                    HyperliquidTrade.order_id == str(tp_sl_oid)
                ).first()
                if hl_trade and hl_trade.trade_time:
                    exit_time = hl_trade.trade_time.isoformat()
        if not exit_time:
            exit_time = p.created_at.isoformat() if p.created_at else None

        # Entry time: look for opening trade
        entry_time = None
        if action in ("buy", "sell"):
            entry_time = p.created_at.isoformat() if p.created_at else None
        elif action == "close" and p.decision_symbol and p.wallet_address:
            opening = db.query(ProgramExecutionLog).filter(
                ProgramExecutionLog.decision_symbol == p.decision_symbol,
                ProgramExecutionLog.wallet_address == p.wallet_address,
                ProgramExecutionLog.decision_action.in_(["buy", "sell"]),
                ProgramExecutionLog.created_at < p.created_at,
            ).order_by(ProgramExecutionLog.created_at.desc()).first()
            entry_time = opening.created_at.isoformat() if opening and opening.created_at else None

        # Tags for program logs (large_loss and sl_triggered)
        p_tags = []
        loss_threshold = account_equity * 0.05 if account_equity > 0 else 50.0
        if pnl < 0 and abs(pnl) >= loss_threshold:
            p_tags.append("large_loss")
        if exit_type == "SL":
            p_tags.append("sl_triggered")

        unified.append({
            "id": p.id,
            "source": "program",
            "symbol": p.decision_symbol,
            "sort_time": p.created_at or datetime.min,
            "decision_time": p.created_at.isoformat() if p.created_at else None,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "entry_type": entry_type,
            "exit_type": exit_type,
            "gross_pnl": round(pnl, 2),
            "fees": round(fee, 2),
            "net_pnl": round(pnl - fee, 2),
            "tags": p_tags,
            "hyperliquid_order_id": p.hyperliquid_order_id,
            "tp_order_id": p.tp_order_id,
            "sl_order_id": p.sl_order_id,
        })

    snapshot_db.close()

    # Sort all trades by time descending
    unified.sort(key=lambda t: t["sort_time"], reverse=True)

    # Apply tag filter
    if tag_filter:
        unified = [t for t in unified if tag_filter in t.get("tags", [])]

    total_count = len(unified)

    # Paginate
    paginated = unified[offset:offset + limit]

    # Remove sort_time from response
    for t in paginated:
        t.pop("sort_time", None)

    return {
        "trades": paginated,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "account_equity": round(account_equity, 2),
        "loss_threshold": round(account_equity * 0.05, 2) if account_equity > 0 else 50.0,
    }


@router.get("/trades/{trade_id}/replay")
def get_trade_replay(
    trade_id: int,
    db: Session = Depends(get_db),
):
    """Get trade replay data including decision chain and trade details."""
    # Get the main trade record
    trade = db.query(AIDecisionLog).filter(AIDecisionLog.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    # Determine if this is an entry or exit record
    is_entry = trade.operation in ('buy', 'sell')
    is_exit = trade.operation == 'close' or (trade.realized_pnl is not None and trade.realized_pnl != 0)

    entry_decision = None
    exit_decision = None
    entry_time = None
    exit_time = None

    # For TP/SL triggered trades, get actual trigger time from HyperliquidTrade
    tp_sl_trigger_time = None
    tp_sl_exit_type = None

    if is_entry and trade.realized_pnl:
        # This is an entry with PnL (TP/SL triggered) - entry and exit are same record
        entry_decision = trade
        exit_decision = trade
        entry_time = trade.decision_time

        # Get actual TP/SL trigger time from HyperliquidTrade
        tp_sl_order_id = trade.tp_order_id or trade.sl_order_id
        if tp_sl_order_id:
            try:
                snapshot_db = SnapshotSessionLocal()
                hl_trade = snapshot_db.query(HyperliquidTrade).filter(
                    HyperliquidTrade.order_id == str(tp_sl_order_id)
                ).first()
                if hl_trade and hl_trade.trade_time:
                    tp_sl_trigger_time = hl_trade.trade_time
                    tp_sl_exit_type = "TP" if float(trade.realized_pnl) > 0 else "SL"
                snapshot_db.close()
            except Exception as e:
                logger.warning(f"Failed to get TP/SL trigger time: {e}")

        exit_time = tp_sl_trigger_time or trade.pnl_updated_at or trade.decision_time
    elif is_entry:
        # Entry without PnL - need to find exit
        entry_decision = trade
        entry_time = trade.decision_time
        # Find corresponding close
        exit_decision = db.query(AIDecisionLog).filter(
            AIDecisionLog.symbol == trade.symbol,
            AIDecisionLog.wallet_address == trade.wallet_address,
            AIDecisionLog.operation == 'close',
            AIDecisionLog.decision_time > trade.decision_time
        ).order_by(AIDecisionLog.decision_time.asc()).first()
        if exit_decision:
            exit_time = exit_decision.decision_time
    else:
        # This is an exit (close) - find corresponding entry
        exit_decision = trade
        exit_time = trade.decision_time
        entry_decision = db.query(AIDecisionLog).filter(
            AIDecisionLog.symbol == trade.symbol,
            AIDecisionLog.wallet_address == trade.wallet_address,
            AIDecisionLog.operation.in_(['buy', 'sell']),
            AIDecisionLog.decision_time < trade.decision_time
        ).order_by(AIDecisionLog.decision_time.desc()).first()
        if entry_decision:
            entry_time = entry_decision.decision_time

    # Build decision chain (all decisions between entry and exit)
    decisions_chain = []
    if entry_time and exit_time:
        chain_query = db.query(AIDecisionLog).filter(
            AIDecisionLog.symbol == trade.symbol,
            AIDecisionLog.wallet_address == trade.wallet_address,
            AIDecisionLog.decision_time >= entry_time,
            AIDecisionLog.decision_time <= exit_time
        ).order_by(AIDecisionLog.decision_time.asc()).all()

        for d in chain_query:
            decisions_chain.append({
                "id": d.id,
                "operation": d.operation,
                "decision_time": d.decision_time.isoformat() if d.decision_time else None,
                "reason": d.reason,
                "target_portion": float(d.target_portion) if d.target_portion else 0,
                "realized_pnl": float(d.realized_pnl) if d.realized_pnl else None,
            })

        # Add TP/SL triggered close to decision chain (when no AI Close decision exists)
        # This handles cases where position was closed by TP/SL order, not by AI Close decision
        if is_entry and trade.realized_pnl and (trade.tp_order_id or trade.sl_order_id):
            has_close_decision = any(d["operation"] == "close" for d in decisions_chain)
            if not has_close_decision:
                # Determine exit type based on PnL
                exit_type = tp_sl_exit_type or ("TP" if float(trade.realized_pnl) > 0 else "SL")
                # Use actual trigger time if available, otherwise use pnl_updated_at
                close_time = tp_sl_trigger_time or trade.pnl_updated_at or trade.decision_time
                decisions_chain.append({
                    "id": -1,  # Virtual ID for TP/SL triggered close
                    "operation": "close",
                    "decision_time": close_time.isoformat() if close_time else None,
                    "reason": f"{exit_type} triggered",
                    "target_portion": 1.0,
                    "realized_pnl": float(trade.realized_pnl),
                })

    # Calculate summary
    entry_price = None
    exit_price = None
    hold_duration = None

    if entry_time and exit_time:
        hold_duration = str(exit_time - entry_time)

    # Get PnL (fees are already deducted in realized_pnl from Hyperliquid)
    pnl = float(trade.realized_pnl) if trade.realized_pnl else 0

    return {
        "trade": {
            "id": trade.id,
            "symbol": trade.symbol,
            "operation": trade.operation,
            "decision_time": trade.decision_time.isoformat() if trade.decision_time else None,
            "wallet_address": trade.wallet_address,
            "hyperliquid_environment": trade.hyperliquid_environment,
            "account_id": trade.account_id,
        },
        "entry_decision": {
            "id": entry_decision.id,
            "operation": entry_decision.operation,
            "decision_time": entry_decision.decision_time.isoformat() if entry_decision.decision_time else None,
            "reason": entry_decision.reason,
        } if entry_decision else None,
        "exit_decision": {
            "id": exit_decision.id,
            "operation": exit_decision.operation,
            "decision_time": exit_decision.decision_time.isoformat() if exit_decision.decision_time else None,
            "reason": exit_decision.reason,
            "exit_type": get_exit_type(exit_decision),
        } if exit_decision else None,
        "decisions_chain": decisions_chain,
        "summary": {
            "entry_time": entry_time.isoformat() if entry_time else None,
            "exit_time": exit_time.isoformat() if exit_time else None,
            "hold_duration": hold_duration,
            "pnl": round(pnl, 2),
        },
        "kline_params": {
            "symbol": trade.symbol,
            "start_time": (entry_time - timedelta(hours=1)).isoformat() if entry_time else None,
            "end_time": (exit_time + timedelta(hours=1)).isoformat() if exit_time else None,
        } if entry_time and exit_time else None,
    }


# ============== Trade Replay K-line API ==============

def _parse_decision_prices(decision: AIDecisionLog) -> dict:
    """Extract price info from decision_snapshot JSON"""
    prices = {"entry_price": None, "tp_price": None, "sl_price": None, "exit_price": None}
    if not decision.decision_snapshot:
        return prices
    try:
        import json
        snapshot = json.loads(decision.decision_snapshot) if isinstance(decision.decision_snapshot, str) else decision.decision_snapshot
        operation = snapshot.get("operation", decision.operation)

        # For BUY: max_price is entry limit (buy no higher than this)
        # For SELL: min_price is entry limit (sell no lower than this)
        if operation == "buy":
            prices["entry_price"] = snapshot.get("max_price") or snapshot.get("entry_price")
        elif operation == "sell":
            prices["entry_price"] = snapshot.get("min_price") or snapshot.get("entry_price")
        else:
            prices["entry_price"] = snapshot.get("max_price") or snapshot.get("min_price") or snapshot.get("entry_price")

        prices["tp_price"] = snapshot.get("take_profit_price") or snapshot.get("tp_price")
        prices["sl_price"] = snapshot.get("stop_loss_price") or snapshot.get("sl_price")
        # For exit/close: min_price is the exit price limit
        prices["exit_price"] = snapshot.get("min_price") if operation == "close" else None
    except:
        pass
    return prices

@router.get("/trades/{trade_id}/kline")
def get_trade_replay_kline(
    trade_id: int,
    period: str = Query("5m", description="K-line period: 5m, 15m, 1h, 4h"),
    db: Session = Depends(get_db)
):
    """
    Get K-line data for trade replay with entry/exit markers.
    Returns historical K-line data centered around the trade's entry and exit times.
    """
    from services.hyperliquid_market_data import get_historical_kline_data_from_hyperliquid

    # Validate period
    valid_periods = ['5m', '15m', '1h', '4h']
    if period not in valid_periods:
        raise HTTPException(status_code=400, detail=f"Invalid period. Valid: {valid_periods}")

    # Get the main trade record (same logic as get_trade_replay)
    trade = db.query(AIDecisionLog).filter(AIDecisionLog.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    # Determine entry and exit decisions (same logic as get_trade_replay)
    is_entry = trade.operation in ('buy', 'sell')

    entry_decision = None
    exit_decision = None
    entry_time = None
    exit_time = None
    tp_sl_trigger_time = None
    tp_sl_exit_type = None

    if is_entry and trade.realized_pnl:
        entry_decision = trade
        exit_decision = trade
        entry_time = trade.decision_time

        # Get actual TP/SL trigger time from HyperliquidTrade
        tp_sl_order_id = trade.tp_order_id or trade.sl_order_id
        if tp_sl_order_id:
            try:
                snapshot_db = SnapshotSessionLocal()
                hl_trade = snapshot_db.query(HyperliquidTrade).filter(
                    HyperliquidTrade.order_id == str(tp_sl_order_id)
                ).first()
                if hl_trade and hl_trade.trade_time:
                    tp_sl_trigger_time = hl_trade.trade_time
                    tp_sl_exit_type = "TP" if float(trade.realized_pnl) > 0 else "SL"
                snapshot_db.close()
            except Exception as e:
                logger.warning(f"Failed to get TP/SL trigger time for kline: {e}")

        exit_time = tp_sl_trigger_time or trade.pnl_updated_at or trade.decision_time
    elif is_entry:
        entry_decision = trade
        entry_time = trade.decision_time
        exit_decision = db.query(AIDecisionLog).filter(
            AIDecisionLog.symbol == trade.symbol,
            AIDecisionLog.wallet_address == trade.wallet_address,
            AIDecisionLog.operation == 'close',
            AIDecisionLog.decision_time > trade.decision_time
        ).order_by(AIDecisionLog.decision_time.asc()).first()
        if exit_decision:
            exit_time = exit_decision.decision_time
    else:
        exit_decision = trade
        exit_time = trade.decision_time
        entry_decision = db.query(AIDecisionLog).filter(
            AIDecisionLog.symbol == trade.symbol,
            AIDecisionLog.wallet_address == trade.wallet_address,
            AIDecisionLog.operation.in_(['buy', 'sell']),
            AIDecisionLog.decision_time < trade.decision_time
        ).order_by(AIDecisionLog.decision_time.desc()).first()
        if entry_decision:
            entry_time = entry_decision.decision_time

    if not entry_time:
        raise HTTPException(status_code=400, detail="Trade has no entry time")

    # Calculate buffer based on period
    period_buffer = {'5m': 30, '15m': 60, '1h': 120, '4h': 480}
    buffer_minutes = period_buffer.get(period, 30)

    # Calculate time range
    start_time = entry_time - timedelta(minutes=buffer_minutes)
    if exit_time:
        end_time = exit_time + timedelta(minutes=buffer_minutes)
    else:
        end_time = entry_time + timedelta(hours=4)

    # Convert to milliseconds
    since_ms = int(start_time.timestamp() * 1000)
    until_ms = int(end_time.timestamp() * 1000)

    # Fetch K-line data from Hyperliquid API first
    environment = trade.hyperliquid_environment or "mainnet"
    klines = get_historical_kline_data_from_hyperliquid(
        symbol=trade.symbol,
        period=period,
        since_ms=since_ms,
        until_ms=until_ms,
        environment=environment
    )

    # Fallback to local database if API returns empty
    if not klines:
        from database.models import CryptoKline

        # Clean symbol (remove -PERP suffix)
        symbol_clean = trade.symbol.upper()
        if symbol_clean.endswith('-PERP'):
            symbol_clean = symbol_clean[:-5]

        # Query local klines
        local_klines = db.query(CryptoKline).filter(
            CryptoKline.symbol == symbol_clean,
            CryptoKline.period == period,
            CryptoKline.timestamp >= int(since_ms / 1000),
            CryptoKline.timestamp <= int(until_ms / 1000),
            CryptoKline.environment == environment
        ).order_by(CryptoKline.timestamp).all()

        if local_klines:
            # Convert to API format
            klines = []
            for k in local_klines:
                open_p = float(k.open_price) if k.open_price else None
                close_p = float(k.close_price) if k.close_price else None
                chg = (close_p - open_p) if open_p and close_p else 0
                pct = (chg / open_p * 100) if open_p else 0
                klines.append({
                    'timestamp': k.timestamp,
                    'datetime': k.datetime_str,
                    'open': open_p,
                    'high': float(k.high_price) if k.high_price else None,
                    'low': float(k.low_price) if k.low_price else None,
                    'close': close_p,
                    'volume': float(k.volume) if k.volume else None,
                    'amount': float(k.amount) if k.amount else None,
                    'chg': chg,
                    'percent': pct,
                })
            logger.info(f"Using {len(klines)} local klines for {symbol_clean} {period}")

    if not klines:
        raise HTTPException(status_code=404, detail="Historical K-line data not available (exchange only keeps recent data)")

    # Build markers with full decision info including prices
    markers = []

    if entry_decision:
        entry_prices = _parse_decision_prices(entry_decision)
        markers.append({
            "type": "entry",
            "time": entry_decision.decision_time.isoformat() if entry_decision.decision_time else None,
            "timestamp": int(entry_decision.decision_time.timestamp()) if entry_decision.decision_time else None,
            "operation": entry_decision.operation,
            "reason": entry_decision.reason,
            "target_portion": float(entry_decision.target_portion) if entry_decision.target_portion else None,
            "symbol": trade.symbol,
            "entry_price": entry_prices["entry_price"],
            "tp_price": entry_prices["tp_price"],
            "sl_price": entry_prices["sl_price"],
        })

    if exit_decision and exit_decision.id != entry_decision.id:
        exit_prices = _parse_decision_prices(exit_decision)
        markers.append({
            "type": "exit",
            "time": exit_decision.decision_time.isoformat() if exit_decision.decision_time else None,
            "timestamp": int(exit_decision.decision_time.timestamp()) if exit_decision.decision_time else None,
            "operation": exit_decision.operation,
            "reason": exit_decision.reason,
            "exit_type": get_exit_type(exit_decision),
            "realized_pnl": float(trade.realized_pnl) if trade.realized_pnl else None,
            "symbol": trade.symbol,
            "exit_price": exit_prices["exit_price"] or exit_prices["entry_price"],  # min_price for close, max_price fallback
        })
    elif exit_decision and exit_decision.id == entry_decision.id and trade.realized_pnl:
        # TP/SL triggered - add exit marker at actual trigger time
        entry_prices = _parse_decision_prices(entry_decision)
        actual_exit_time = tp_sl_trigger_time or trade.pnl_updated_at or trade.decision_time
        # Determine exit type: use detected type, or infer from PnL
        actual_exit_type = tp_sl_exit_type or ("TP" if float(trade.realized_pnl) > 0 else "SL")
        markers.append({
            "type": "exit",
            "time": actual_exit_time.isoformat(),
            "timestamp": int(actual_exit_time.timestamp()),
            "operation": "close",
            "reason": f"{actual_exit_type} triggered",
            "exit_type": actual_exit_type,
            "realized_pnl": float(trade.realized_pnl),
            "symbol": trade.symbol,
            "exit_price": entry_prices["tp_price"] if float(trade.realized_pnl) > 0 else entry_prices["sl_price"],
        })

    # Add HOLD decision markers
    if entry_time and exit_time:
        hold_decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.symbol == trade.symbol,
            AIDecisionLog.wallet_address == trade.wallet_address,
            AIDecisionLog.operation == 'hold',
            AIDecisionLog.decision_time > entry_time,
            AIDecisionLog.decision_time < exit_time
        ).order_by(AIDecisionLog.decision_time.asc()).all()

        for hold in hold_decisions:
            markers.append({
                "type": "hold",
                "time": hold.decision_time.isoformat() if hold.decision_time else None,
                "timestamp": int(hold.decision_time.timestamp()) if hold.decision_time else None,
                "operation": "hold",
                "reason": hold.reason,
                "symbol": trade.symbol,
            })

    # Calculate default period based on hold duration
    default_period = "5m"
    if entry_time and exit_time:
        hold_minutes = (exit_time - entry_time).total_seconds() / 60
        if hold_minutes > 1440:
            default_period = "4h"
        elif hold_minutes > 240:
            default_period = "1h"
        elif hold_minutes > 60:
            default_period = "15m"

    return {
        "symbol": trade.symbol,
        "period": period,
        "default_period": default_period,
        "klines": klines,
        "markers": markers,
        "time_range": {
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
        }
    }


# ============== Program Analytics Helper Functions ==============

def get_fees_for_program_logs(logs: List[ProgramExecutionLog]) -> Dict[int, float]:
    """
    Batch query HyperliquidTrade to get total fees for each program execution log.
    Returns a dict mapping log_id -> total_fee.
    PnL is read directly from ProgramExecutionLog.realized_pnl field.
    """
    if not logs:
        return {}

    # Collect all order IDs (main, tp, sl)
    order_ids = set()
    log_orders: Dict[int, List[str]] = {}  # log_id -> list of order_ids

    for log in logs:
        orders = []
        if log.hyperliquid_order_id:
            order_ids.add(log.hyperliquid_order_id)
            orders.append(log.hyperliquid_order_id)
        if log.tp_order_id:
            order_ids.add(log.tp_order_id)
            orders.append(log.tp_order_id)
        if log.sl_order_id:
            order_ids.add(log.sl_order_id)
            orders.append(log.sl_order_id)
        log_orders[log.id] = orders

    if not order_ids:
        return {log.id: 0.0 for log in logs}

    # Batch query fees from HyperliquidTrade
    fee_map: Dict[str, float] = {}
    try:
        snapshot_db = SnapshotSessionLocal()
        trades = snapshot_db.query(HyperliquidTrade).filter(
            HyperliquidTrade.order_id.in_(list(order_ids))
        ).all()
        for t in trades:
            if t.order_id:
                fee_map[str(t.order_id)] = float(t.fee or 0)
        snapshot_db.close()
    except Exception as e:
        logger.warning(f"Failed to fetch fees from HyperliquidTrade: {e}")

    # Calculate total fee for each log
    result: Dict[int, float] = {}
    for log in logs:
        total_fee = 0.0
        for oid in log_orders.get(log.id, []):
            total_fee += fee_map.get(oid, 0.0)
        result[log.id] = total_fee

    return result


def build_program_base_query(
    db: Session,
    start_date: Optional[date],
    end_date: Optional[date],
    environment: Optional[str],
    account_id: Optional[int],
    exchange: Optional[str] = None,
):
    """Build base query for program execution logs with common filters.

    Similar to build_base_query for AIDecisionLog, but for ProgramExecutionLog.
    Only includes executions with non-zero realized_pnl (closed positions).
    """
    query = db.query(ProgramExecutionLog).filter(
        ProgramExecutionLog.success == True,
        ProgramExecutionLog.decision_action.in_(["buy", "sell", "close"]),
        ProgramExecutionLog.realized_pnl.isnot(None),  # Exclude unsync trades
        ProgramExecutionLog.realized_pnl != 0,  # Exclude opening trades (no settled PnL)
    )

    if start_date:
        query = query.filter(ProgramExecutionLog.created_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.filter(ProgramExecutionLog.created_at <= datetime.combine(end_date, datetime.max.time()))
    if account_id:
        query = query.filter(ProgramExecutionLog.account_id == account_id)

    # Filter by environment directly (like AIDecisionLog.hyperliquid_environment)
    if environment and environment != "all":
        query = query.filter(ProgramExecutionLog.environment == environment)

    # Filter by exchange
    if exchange and exchange != "all":
        if exchange == "hyperliquid":
            query = query.filter(
                (ProgramExecutionLog.exchange == "hyperliquid") | (ProgramExecutionLog.exchange == None)
            )
        else:
            query = query.filter(ProgramExecutionLog.exchange == exchange)

    return query


# ============== Program Analytics API Endpoints ==============

@router.get("/program-summary")
def get_program_analytics_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get overall program analytics summary."""
    query = build_program_base_query(db, start_date, end_date, environment, account_id, exchange)
    logs = query.all()

    # Get fees for all logs (PnL is read from log.realized_pnl)
    fee_map = get_fees_for_program_logs(logs)

    records = []
    signal_records = []
    scheduled_records = []

    with_program = 0
    with_signal = 0

    for log in logs:
        pnl = float(log.realized_pnl) if log.realized_pnl else 0
        fee = fee_map.get(log.id, 0.0)
        record = {"pnl": pnl, "fee": fee}
        records.append(record)

        trigger_type = log.trigger_type or "unknown"
        if trigger_type == "signal":
            signal_records.append(record)
        else:
            scheduled_records.append(record)

        if log.program_id:
            with_program += 1
        if log.signal_pool_id:
            with_signal += 1

    overview = calculate_metrics(records)

    return {
        "period": {
            "start": start_date.isoformat() if start_date else None,
            "end": end_date.isoformat() if end_date else None,
        },
        "overview": overview,
        "data_completeness": {
            "total_executions": len(logs),
            "with_program": with_program,
            "with_signal": with_signal,
            "with_pnl": len(records),
        },
        "by_trigger_type": {
            "signal": {
                "count": len(signal_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2),
            },
            "scheduled": {
                "count": len(scheduled_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2),
            },
        },
    }


@router.get("/program-by-symbol")
def get_program_analytics_by_symbol(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get program analytics grouped by trading symbol."""
    query = build_program_base_query(db, start_date, end_date, environment, account_id, exchange)
    logs = query.all()

    fee_map = get_fees_for_program_logs(logs)

    # Group by symbol
    by_symbol: Dict[Optional[str], List[Dict]] = {}

    for log in logs:
        pnl = float(log.realized_pnl) if log.realized_pnl else 0
        fee = fee_map.get(log.id, 0.0)

        symbol = log.decision_symbol
        record = {"pnl": pnl, "fee": fee, "trigger_type": log.trigger_type or "scheduled"}

        if symbol not in by_symbol:
            by_symbol[symbol] = []
        by_symbol[symbol].append(record)

    # Build response
    items = []
    for symbol, records in by_symbol.items():
        if symbol is None:
            continue

        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] != "signal"]

        items.append({
            "symbol": symbol,
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2)},
            },
        })

    items.sort(key=lambda x: x["metrics"]["net_pnl"], reverse=True)

    unattributed_records = by_symbol.get(None, [])

    return {
        "items": items,
        "unattributed": {
            "count": len(unattributed_records),
            "metrics": calculate_metrics(unattributed_records) if unattributed_records else None,
        },
    }


@router.get("/program-by-program")
def get_program_analytics_by_program(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get program analytics grouped by trading program."""
    query = build_program_base_query(db, start_date, end_date, environment, account_id, exchange)
    logs = query.all()

    fee_map = get_fees_for_program_logs(logs)

    by_program: Dict[Optional[int], List[Dict]] = {}
    program_names: Dict[int, str] = {}

    for log in logs:
        pnl = float(log.realized_pnl) if log.realized_pnl else 0
        fee = fee_map.get(log.id, 0.0)

        program_id = log.program_id
        record = {"pnl": pnl, "fee": fee, "trigger_type": log.trigger_type or "scheduled"}

        if program_id not in by_program:
            by_program[program_id] = []
        by_program[program_id].append(record)

        if program_id and log.program_name:
            program_names[program_id] = log.program_name

    program_ids = [pid for pid in by_program.keys() if pid is not None]
    if program_ids:
        programs = db.query(TradingProgram).filter(
            TradingProgram.id.in_(program_ids),
            TradingProgram.is_deleted != True
        ).all()
        for p in programs:
            program_names[p.id] = p.name

    items = []
    for program_id, records in by_program.items():
        if program_id is None:
            continue

        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] != "signal"]

        items.append({
            "program_id": program_id,
            "program_name": program_names.get(program_id, f"Program {program_id}"),
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2)},
            },
        })

    items.sort(key=lambda x: x["metrics"]["net_pnl"], reverse=True)
    unattributed_records = by_program.get(None, [])

    return {
        "items": items,
        "unattributed": {
            "count": len(unattributed_records),
            "metrics": calculate_metrics(unattributed_records) if unattributed_records else None,
        },
    }


@router.get("/program-by-trigger-type")
def get_program_analytics_by_trigger_type(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get program analytics grouped by trigger type."""
    query = build_program_base_query(db, start_date, end_date, environment, account_id, exchange)
    logs = query.all()

    fee_map = get_fees_for_program_logs(logs)

    by_trigger: Dict[str, List[Dict]] = {"signal": [], "scheduled": []}

    for log in logs:
        pnl = float(log.realized_pnl) if log.realized_pnl else 0
        fee = fee_map.get(log.id, 0.0)

        trigger_type = log.trigger_type if log.trigger_type == "signal" else "scheduled"
        record = {"pnl": pnl, "fee": fee}
        by_trigger[trigger_type].append(record)

    items = []
    for trigger_type in ["signal", "scheduled"]:
        records = by_trigger[trigger_type]
        if records:
            items.append({
                "trigger_type": trigger_type,
                "metrics": calculate_metrics(records),
            })

    items.sort(key=lambda x: x["metrics"]["trade_count"], reverse=True)

    return {"items": items}


@router.get("/program-by-operation")
def get_program_analytics_by_operation(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    exchange: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get program analytics grouped by operation type."""
    query = build_program_base_query(db, start_date, end_date, environment, account_id, exchange)
    logs = query.all()

    fee_map = get_fees_for_program_logs(logs)

    by_operation: Dict[str, List[Dict]] = {}

    for log in logs:
        pnl = float(log.realized_pnl) if log.realized_pnl else 0
        fee = fee_map.get(log.id, 0.0)

        operation = log.decision_action or "unknown"
        record = {"pnl": pnl, "fee": fee, "trigger_type": log.trigger_type or "scheduled"}

        if operation not in by_operation:
            by_operation[operation] = []
        by_operation[operation].append(record)

    items = []
    for operation, records in by_operation.items():
        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] != "signal"]

        items.append({
            "operation": operation,
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2)},
            },
        })

    items.sort(key=lambda x: x["metrics"]["trade_count"], reverse=True)

    return {"items": items}
