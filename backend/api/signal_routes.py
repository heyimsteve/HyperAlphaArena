"""Signal system API routes"""
from __future__ import annotations

import logging
import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from database.connection import SessionLocal

logger = logging.getLogger(__name__)
from schemas.signal import (
    SignalDefinitionCreate,
    SignalDefinitionUpdate,
    SignalDefinitionResponse,
    SignalPoolCreate,
    SignalPoolUpdate,
    SignalPoolResponse,
    SignalListResponse,
    SignalTriggerLogResponse,
    SignalTriggerLogsResponse,
)
from services.hyper_insight_wallet_service import hyper_insight_wallet_service

router = APIRouter(prefix="/api/signals", tags=["Signal System"])

MARKET_SIGNAL_SOURCE = "market_signals"
WALLET_TRACKING_SOURCE = "wallet_tracking"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_json_text(value, fallback):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    if value is None:
        return fallback
    return value


def _normalize_source_type(source_type: Optional[str]) -> str:
    normalized = (source_type or MARKET_SIGNAL_SOURCE).strip() or MARKET_SIGNAL_SOURCE
    if normalized not in {MARKET_SIGNAL_SOURCE, WALLET_TRACKING_SOURCE}:
        raise HTTPException(status_code=400, detail="Invalid source_type")
    return normalized


def _normalize_source_config(source_type: str, source_config):
    parsed = _parse_json_text(source_config, {})
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="source_config must be an object")
    if source_type == MARKET_SIGNAL_SOURCE:
        return {}
    return parsed


def _parse_source_config_response(source_type: str, source_config):
    parsed = _parse_json_text(source_config, {})
    if not isinstance(parsed, dict):
        parsed = {}
    if source_type == MARKET_SIGNAL_SOURCE:
        return {}
    return parsed


def _build_pool_response(row) -> SignalPoolResponse:
    signal_ids = _parse_json_text(row[2], [])
    symbols = _parse_json_text(row[3], [])
    source_type = row[8] or MARKET_SIGNAL_SOURCE
    source_config = _parse_source_config_response(source_type, row[9])
    return SignalPoolResponse(
        id=row[0],
        pool_name=row[1],
        signal_ids=signal_ids or [],
        symbols=symbols or [],
        enabled=row[4],
        created_at=row[5],
        logic=row[6] or "OR",
        exchange=row[7] or "hyperliquid",
        source_type=source_type,
        source_config=source_config,
    )


def _schedule_wallet_runtime_refresh() -> None:
    hyper_insight_wallet_service.request_refresh()


class WalletTrackingRuntimeRequest(BaseModel):
    enabled: bool
    access_token: Optional[str] = None


class WalletTrackingTokenRequest(BaseModel):
    access_token: str


# ============ Signal Definitions ============

@router.get("", response_model=SignalListResponse)
@router.get("/", response_model=SignalListResponse)
def list_signals(db: Session = Depends(get_db)) -> SignalListResponse:
    """List all signal definitions and pools"""
    signals_result = db.execute(text("""
        SELECT id, signal_name, description, trigger_condition, enabled, created_at, updated_at, exchange
        FROM signal_definitions WHERE (is_deleted IS NULL OR is_deleted = false) ORDER BY id
    """))
    signals = []
    for row in signals_result:
        # Parse trigger_condition from JSON string if needed
        trigger_condition = row[3]
        trigger_condition = _parse_json_text(trigger_condition, {})
        signals.append(SignalDefinitionResponse(
            id=row[0], signal_name=row[1], description=row[2],
            trigger_condition=trigger_condition, enabled=row[4],
            created_at=row[5], updated_at=row[6], exchange=row[7] or "hyperliquid"
        ))

    pools_result = db.execute(text("""
        SELECT id, pool_name, signal_ids, symbols, enabled, created_at, logic, exchange, source_type, source_config
        FROM signal_pools WHERE (is_deleted IS NULL OR is_deleted = false) ORDER BY id
    """))
    pools = []
    for row in pools_result:
        pools.append(_build_pool_response(row))

    return SignalListResponse(signals=signals, pools=pools)


@router.get("/wallet-tracking/status")
def get_wallet_tracking_status() -> dict[str, Any]:
    """Get runtime status for Hyper Insight wallet tracking integration."""
    return hyper_insight_wallet_service.get_status_snapshot()


@router.put("/wallet-tracking/runtime")
async def update_wallet_tracking_runtime(payload: WalletTrackingRuntimeRequest):
    """Enable or disable runtime integration and optionally sync a fresh access token."""
    await hyper_insight_wallet_service.set_enabled(payload.enabled)
    if payload.access_token:
        await hyper_insight_wallet_service.sync_access_token(payload.access_token)
    return hyper_insight_wallet_service.get_status_snapshot()


@router.post("/wallet-tracking/token")
async def sync_wallet_tracking_token(payload: WalletTrackingTokenRequest):
    """Sync the latest HAA access token for future reconnects."""
    await hyper_insight_wallet_service.sync_access_token(payload.access_token)
    return {"success": True}


@router.delete("/wallet-tracking/token")
async def clear_wallet_tracking_token():
    """Clear the persisted runtime access token."""
    await hyper_insight_wallet_service.clear_access_token()
    return {"success": True}


@router.post("/definitions", response_model=SignalDefinitionResponse)
def create_signal(payload: SignalDefinitionCreate, db: Session = Depends(get_db)):
    """Create a new signal definition"""
    import json
    result = db.execute(text("""
        INSERT INTO signal_definitions (signal_name, description, trigger_condition, enabled, exchange)
        VALUES (:name, :desc, :condition, :enabled, :exchange)
        RETURNING id, signal_name, description, trigger_condition, enabled, created_at, updated_at, exchange
    """), {
        "name": payload.signal_name,
        "desc": payload.description,
        "condition": json.dumps(payload.trigger_condition),
        "enabled": payload.enabled,
        "exchange": payload.exchange
    })
    db.commit()
    row = result.fetchone()
    trigger_condition = row[3]
    if isinstance(trigger_condition, str):
        trigger_condition = json.loads(trigger_condition)
    return SignalDefinitionResponse(
        id=row[0], signal_name=row[1], description=row[2],
        trigger_condition=trigger_condition, enabled=row[4],
        created_at=row[5], updated_at=row[6], exchange=row[7] or "hyperliquid"
    )


@router.get("/definitions/{signal_id}", response_model=SignalDefinitionResponse)
def get_signal(signal_id: int, db: Session = Depends(get_db)):
    """Get a signal definition by ID"""
    import json
    result = db.execute(text("""
        SELECT id, signal_name, description, trigger_condition, enabled, created_at, updated_at, exchange
        FROM signal_definitions WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false)
    """), {"id": signal_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")
    trigger_condition = row[3]
    if isinstance(trigger_condition, str):
        trigger_condition = json.loads(trigger_condition)
    return SignalDefinitionResponse(
        id=row[0], signal_name=row[1], description=row[2],
        trigger_condition=trigger_condition, enabled=row[4],
        created_at=row[5], updated_at=row[6], exchange=row[7] or "hyperliquid"
    )


@router.put("/definitions/{signal_id}", response_model=SignalDefinitionResponse)
def update_signal(signal_id: int, payload: SignalDefinitionUpdate, db: Session = Depends(get_db)):
    """Update a signal definition"""
    import json
    # Build dynamic update query
    updates = []
    params = {"id": signal_id}
    if payload.signal_name is not None:
        updates.append("signal_name = :name")
        params["name"] = payload.signal_name
    if payload.description is not None:
        updates.append("description = :desc")
        params["desc"] = payload.description
    if payload.trigger_condition is not None:
        updates.append("trigger_condition = :condition")
        params["condition"] = json.dumps(payload.trigger_condition)
    if payload.enabled is not None:
        updates.append("enabled = :enabled")
        params["enabled"] = payload.enabled
    if payload.exchange is not None:
        updates.append("exchange = :exchange")
        params["exchange"] = payload.exchange

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = CURRENT_TIMESTAMP")
    query = f"UPDATE signal_definitions SET {', '.join(updates)} WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false) RETURNING id, signal_name, description, trigger_condition, enabled, created_at, updated_at, exchange"
    result = db.execute(text(query), params)
    db.commit()
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")
    trigger_condition = row[3]
    if isinstance(trigger_condition, str):
        trigger_condition = json.loads(trigger_condition)
    return SignalDefinitionResponse(
        id=row[0], signal_name=row[1], description=row[2],
        trigger_condition=trigger_condition, enabled=row[4],
        created_at=row[5], updated_at=row[6], exchange=row[7] or "hyperliquid"
    )


@router.delete("/definitions/{signal_id}")
def delete_signal(signal_id: int, db: Session = Depends(get_db)):
    """Soft-delete a signal definition with dependency checking."""
    from services.entity_deletion_service import delete_signal_definition
    result = delete_signal_definition(db, signal_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Signal not found"))
    return result


# ============ Signal Pools ============

@router.post("/pools", response_model=SignalPoolResponse)
def create_pool(payload: SignalPoolCreate, db: Session = Depends(get_db)):
    """Create a new signal pool"""
    source_type = _normalize_source_type(payload.source_type)
    source_config = _normalize_source_config(source_type, payload.source_config)

    # Validate that all signals belong to the same exchange as the pool
    if source_type == MARKET_SIGNAL_SOURCE and payload.signal_ids:
        result = db.execute(text("""
            SELECT id, exchange FROM signal_definitions WHERE id = ANY(:ids) AND (is_deleted IS NULL OR is_deleted = false)
        """), {"ids": payload.signal_ids})
        for row in result.fetchall():
            signal_exchange = row[1] or "hyperliquid"
            if signal_exchange != payload.exchange:
                raise HTTPException(
                    status_code=400,
                    detail=f"Signal {row[0]} belongs to {signal_exchange}, but pool is for {payload.exchange}"
                )

    result = db.execute(text("""
        INSERT INTO signal_pools (pool_name, signal_ids, symbols, enabled, logic, exchange, source_type, source_config)
        VALUES (:name, :signal_ids, :symbols, :enabled, :logic, :exchange, :source_type, :source_config)
        RETURNING id, pool_name, signal_ids, symbols, enabled, created_at, logic, exchange, source_type, source_config
    """), {
        "name": payload.pool_name,
        "signal_ids": json.dumps(payload.signal_ids if source_type == MARKET_SIGNAL_SOURCE else []),
        "symbols": json.dumps(payload.symbols if source_type == MARKET_SIGNAL_SOURCE else []),
        "enabled": payload.enabled,
        "logic": payload.logic,
        "exchange": payload.exchange,
        "source_type": source_type,
        "source_config": json.dumps(source_config),
    })
    db.commit()
    row = result.fetchone()
    response = _build_pool_response(row)
    _schedule_wallet_runtime_refresh()
    return response


@router.get("/pools/{pool_id}", response_model=SignalPoolResponse)
def get_pool(pool_id: int, db: Session = Depends(get_db)):
    """Get a signal pool by ID"""
    result = db.execute(text("""
        SELECT id, pool_name, signal_ids, symbols, enabled, created_at, logic, exchange, source_type, source_config
        FROM signal_pools WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false)
    """), {"id": pool_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pool not found")
    return _build_pool_response(row)


@router.put("/pools/{pool_id}", response_model=SignalPoolResponse)
def update_pool(pool_id: int, payload: SignalPoolUpdate, db: Session = Depends(get_db)):
    """Update a signal pool"""
    current = db.execute(text("""
        SELECT exchange, source_type, source_config
        FROM signal_pools WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false)
    """), {"id": pool_id}).fetchone()
    if not current:
        raise HTTPException(status_code=404, detail="Pool not found")

    target_exchange = payload.exchange if payload.exchange is not None else (current[0] or "hyperliquid")
    target_source_type = _normalize_source_type(payload.source_type if payload.source_type is not None else current[1])
    target_source_config = _normalize_source_config(
        target_source_type,
        payload.source_config if payload.source_config is not None else current[2],
    )

    # Validate that all signals belong to the same exchange as the pool
    signal_ids_to_check = payload.signal_ids
    if target_source_type == MARKET_SIGNAL_SOURCE and signal_ids_to_check and target_exchange:
        result = db.execute(text("""
            SELECT id, exchange FROM signal_definitions WHERE id = ANY(:ids) AND (is_deleted IS NULL OR is_deleted = false)
        """), {"ids": signal_ids_to_check})
        for row in result.fetchall():
            signal_exchange = row[1] or "hyperliquid"
            if signal_exchange != target_exchange:
                raise HTTPException(
                    status_code=400,
                    detail=f"Signal {row[0]} belongs to {signal_exchange}, but pool is for {target_exchange}"
                )

    updates = []
    params = {"id": pool_id}
    if payload.pool_name is not None:
        updates.append("pool_name = :name")
        params["name"] = payload.pool_name
    if payload.signal_ids is not None:
        updates.append("signal_ids = :signal_ids")
        params["signal_ids"] = json.dumps(payload.signal_ids if target_source_type == MARKET_SIGNAL_SOURCE else [])
    if payload.symbols is not None:
        updates.append("symbols = :symbols")
        params["symbols"] = json.dumps(payload.symbols if target_source_type == MARKET_SIGNAL_SOURCE else [])
    if payload.enabled is not None:
        updates.append("enabled = :enabled")
        params["enabled"] = payload.enabled
    if payload.logic is not None:
        updates.append("logic = :logic")
        params["logic"] = payload.logic
    if payload.exchange is not None:
        updates.append("exchange = :exchange")
        params["exchange"] = payload.exchange
    if payload.source_type is not None:
        updates.append("source_type = :source_type")
        params["source_type"] = target_source_type
        if target_source_type == WALLET_TRACKING_SOURCE:
            if payload.signal_ids is None:
                updates.append("signal_ids = '[]'")
            if payload.symbols is None:
                updates.append("symbols = '[]'")
    if payload.source_config is not None or payload.source_type is not None:
        updates.append("source_config = :source_config")
        params["source_config"] = json.dumps(target_source_config)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    query = f"""UPDATE signal_pools SET {', '.join(updates)}
        WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false)
        RETURNING id, pool_name, signal_ids, symbols, enabled, created_at, logic, exchange, source_type, source_config"""
    result = db.execute(text(query), params)
    db.commit()
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pool not found")
    response = _build_pool_response(row)
    _schedule_wallet_runtime_refresh()
    return response


@router.delete("/pools/{pool_id}")
def delete_pool(pool_id: int, db: Session = Depends(get_db)):
    """Soft-delete a signal pool with dependency checking."""
    from services.entity_deletion_service import delete_signal_pool
    result = delete_signal_pool(db, pool_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Pool not found"))
    _schedule_wallet_runtime_refresh()
    return result


# ============ Metric Analysis ============

@router.get("/analyze")
def analyze_metric(
    symbol: str = Query(..., description="Trading symbol (e.g., BTC)"),
    metric: str = Query(..., description="Metric type (e.g., oi_delta_percent)"),
    period: str = Query("5m", description="Time period (e.g., 5m, 15m)"),
    days: int = Query(7, le=30, description="Days of history to analyze"),
    exchange: str = Query("hyperliquid", description="Exchange (hyperliquid or binance)"),
    db: Session = Depends(get_db)
):
    """
    Analyze a metric and provide statistical summary with threshold suggestions.

    Returns statistics and suggested thresholds based on historical data.
    """
    from services.signal_analysis_service import signal_analysis_service

    result = signal_analysis_service.analyze_metric(db, symbol, metric, period, days, exchange)
    return result


# ============ Signal Backtest Preview ============

@router.get("/backtest/{signal_id}")
def backtest_signal(
    signal_id: int,
    symbol: str = Query(..., description="Trading symbol (e.g., BTC)"),
    kline_min_ts: int = Query(None, description="Min K-line timestamp in ms (for filtering triggers)"),
    kline_max_ts: int = Query(None, description="Max K-line timestamp in ms (for filtering triggers)"),
    db: Session = Depends(get_db)
):
    """
    Backtest a signal against historical data.
    Returns only trigger points - K-lines should be fetched via /api/market/kline-with-indicators.
    """
    from services.signal_backtest_service import signal_backtest_service

    try:
        result = signal_backtest_service.backtest_signal(db, signal_id, symbol, kline_min_ts, kline_max_ts)
        return result
    except Exception as e:
        logger.error(
            f"[Backtest API] EXCEPTION: signal_id={signal_id}, symbol={symbol}, "
            f"ts_range=[{kline_min_ts}, {kline_max_ts}], error={e}",
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Backtest failed: {str(e)}")


from pydantic import BaseModel, Field


class TempBacktestRequest(BaseModel):
    """Request for temporary signal backtest (without saving to database)"""
    symbol: str = Field(..., description="Trading symbol (e.g., BTC)")
    trigger_condition: dict = Field(..., alias="triggerCondition", description="Signal trigger condition")
    kline_min_ts: Optional[int] = Field(None, alias="klineMinTs", description="Min K-line timestamp in ms")
    kline_max_ts: Optional[int] = Field(None, alias="klineMaxTs", description="Max K-line timestamp in ms")
    exchange: str = Field("hyperliquid", description="Exchange (hyperliquid or binance)")

    class Config:
        populate_by_name = True


@router.post("/backtest-preview")
def backtest_preview(
    request: TempBacktestRequest,
    db: Session = Depends(get_db)
):
    """
    Backtest a signal configuration without saving to database.
    Used for AI signal creation preview before actually creating the signal.
    """
    from services.signal_backtest_service import signal_backtest_service

    try:
        result = signal_backtest_service.backtest_temp_signal(
            db=db,
            symbol=request.symbol,
            trigger_condition=request.trigger_condition,
            kline_min_ts=request.kline_min_ts,
            kline_max_ts=request.kline_max_ts,
            exchange=request.exchange
        )
        return result
    except Exception as e:
        logger.error(
            f"[Backtest API] EXCEPTION in preview: symbol={request.symbol}, "
            f"condition={request.trigger_condition}, error={e}",
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Backtest preview failed: {str(e)}")


@router.get("/pool-backtest/{pool_id}")
def backtest_pool(
    pool_id: int,
    symbol: str = Query(..., description="Trading symbol (e.g., BTC)"),
    kline_min_ts: int = Query(None, description="Min K-line timestamp in ms"),
    kline_max_ts: int = Query(None, description="Max K-line timestamp in ms"),
    db: Session = Depends(get_db)
):
    """
    Backtest a signal pool against historical data.
    Combines triggers from multiple signals based on pool logic (AND/OR).
    """
    from services.signal_backtest_service import signal_backtest_service

    try:
        pool_meta = db.execute(text("""
            SELECT source_type FROM signal_pools
            WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false)
        """), {"id": pool_id}).fetchone()
        if not pool_meta:
            raise HTTPException(status_code=404, detail="Pool not found")
        if (pool_meta[0] or MARKET_SIGNAL_SOURCE) != MARKET_SIGNAL_SOURCE:
            raise HTTPException(status_code=400, detail="Wallet tracking pools do not support backtest")
        result = signal_backtest_service.backtest_pool(db, pool_id, symbol, kline_min_ts, kline_max_ts)
        return result
    except Exception as e:
        logger.error(
            f"[Backtest API] EXCEPTION in pool: pool_id={pool_id}, symbol={symbol}, "
            f"ts_range=[{kline_min_ts}, {kline_max_ts}], error={e}",
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Pool backtest failed: {str(e)}")


# ============ Trigger Logs ============

@router.get("/logs", response_model=SignalTriggerLogsResponse)
def get_trigger_logs(
    pool_id: Optional[int] = Query(None),
    signal_id: Optional[int] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Get signal trigger logs with optional filters and pagination"""
    conditions = []
    params = {"limit": limit, "offset": offset}

    if pool_id is not None:
        conditions.append("pool_id = :pool_id")
        params["pool_id"] = pool_id
    if signal_id is not None:
        conditions.append("signal_id = :signal_id")
        params["signal_id"] = signal_id
    if symbol is not None:
        conditions.append("symbol = :symbol")
        params["symbol"] = symbol

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, signal_id, pool_id, symbol, trigger_value, triggered_at, market_regime
        FROM signal_trigger_logs {where_clause}
        ORDER BY triggered_at DESC LIMIT :limit OFFSET :offset
    """
    import json
    result = db.execute(text(query), params)
    logs = []
    for row in result:
        # Parse trigger_value - ORM defines as Text, so it may be string
        trigger_val = row[4]
        if isinstance(trigger_val, str):
            try:
                trigger_val = json.loads(trigger_val)
            except json.JSONDecodeError:
                trigger_val = None
        # Parse market_regime - also stored as Text/JSON
        market_regime_val = row[6]
        if isinstance(market_regime_val, str):
            try:
                market_regime_val = json.loads(market_regime_val)
            except json.JSONDecodeError:
                market_regime_val = None
        logs.append(SignalTriggerLogResponse(
            id=row[0], signal_id=row[1], pool_id=row[2],
            symbol=row[3], trigger_value=trigger_val, triggered_at=row[5],
            market_regime=market_regime_val
        ))

    # Get total count
    count_query = f"SELECT COUNT(*) FROM signal_trigger_logs {where_clause}"
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = db.execute(text(count_query), count_params).scalar()

    return SignalTriggerLogsResponse(logs=logs, total=total)


# ============ Signal Testing & Monitoring ============

@router.get("/test/{signal_id}")
def test_signal(
    signal_id: int,
    symbol: str = Query(..., description="Symbol to test against"),
    db: Session = Depends(get_db)
):
    """
    Test a signal against current market data.
    Returns the current metric value and whether the condition is met.
    """
    import json
    from services.signal_detection_service import signal_detection_service
    from services.market_flow_collector import market_flow_collector

    # Get signal definition
    result = db.execute(text("""
        SELECT id, signal_name, description, trigger_condition, enabled
        FROM signal_definitions WHERE id = :id AND (is_deleted IS NULL OR is_deleted = false)
    """), {"id": signal_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")

    # Parse trigger_condition - may be string (TEXT) or dict (JSONB)
    trigger_condition = row[3]
    if isinstance(trigger_condition, str):
        try:
            trigger_condition = json.loads(trigger_condition)
        except json.JSONDecodeError:
            trigger_condition = {}

    signal_def = {
        "id": row[0],
        "signal_name": row[1],
        "description": row[2],
        "trigger_condition": trigger_condition,
        "enabled": row[4]
    }

    # Get current market data from collector
    market_data = {
        "asset_ctx": market_flow_collector.latest_asset_ctx.get(symbol, {}),
        "orderbook": market_flow_collector.latest_orderbook.get(symbol, {}),
    }

    condition = signal_def.get("trigger_condition", {})
    metric = condition.get("metric")
    operator = condition.get("operator")
    threshold = condition.get("threshold")
    time_window = condition.get("time_window", 60)

    # Get current metric value
    current_value = signal_detection_service._get_metric_value(
        metric, symbol, market_data, time_window
    )

    # Evaluate condition
    condition_met = False
    if current_value is not None:
        condition_met = signal_detection_service._evaluate_condition(
            current_value, operator, threshold
        )

    # Get signal state
    state_key = (signal_id, symbol)
    state = signal_detection_service.signal_states.get(state_key)

    return {
        "signal_id": signal_id,
        "signal_name": signal_def["signal_name"],
        "symbol": symbol,
        "metric": metric,
        "operator": operator,
        "threshold": threshold,
        "time_window": time_window,
        "current_value": current_value,
        "condition_met": condition_met,
        "is_active": state.is_active if state else False,
        "would_trigger": condition_met and (not state or not state.is_active),
        "market_data_available": bool(market_data.get("asset_ctx")),
    }


@router.get("/states")
def get_signal_states():
    """Get current signal states for monitoring"""
    from services.signal_detection_service import signal_detection_service
    return {
        "states": signal_detection_service.get_signal_states(),
        "cache_info": {
            "pools_count": len(signal_detection_service._signal_pools_cache),
            "signals_count": len(signal_detection_service._signals_cache),
        }
    }


@router.post("/states/reset")
def reset_signal_states(
    signal_id: Optional[int] = Query(None),
    pool_id: Optional[int] = Query(None),
    symbol: Optional[str] = Query(None)
):
    """Reset signal and pool states (useful for testing)"""
    from services.signal_detection_service import signal_detection_service
    signal_detection_service.reset_state(signal_id, pool_id, symbol)
    return {"message": "Signal and pool states reset successfully"}


# ============ AI Signal Generation Chat APIs ============

from fastapi.responses import StreamingResponse
from services.ai_signal_generation_service import (
    generate_signal_with_ai,
    generate_signal_with_ai_stream,
    get_signal_conversation_history,
    get_signal_conversation_messages
)
from database.models import User


class AiSignalChatRequest(BaseModel):
    """Request to send a message to AI signal generation chat"""
    account_id: int = Field(..., alias="accountId")
    user_message: str = Field(..., alias="userMessage")
    conversation_id: Optional[int] = Field(None, alias="conversationId")
    # SSE direct streaming is unstable (frontend disconnect = task abort). Do NOT set to False.
    use_background_task: bool = Field(True, alias="useBackgroundTask")

    class Config:
        populate_by_name = True


class AiSignalChatResponse(BaseModel):
    """Response from AI signal generation chat"""
    success: bool
    conversation_id: Optional[int] = Field(None, alias="conversationId")
    message_id: Optional[int] = Field(None, alias="messageId")
    content: Optional[str] = None
    signal_configs: Optional[List[dict]] = Field(None, alias="signalConfigs")
    error: Optional[str] = None

    class Config:
        populate_by_name = True


@router.post("/ai-chat", response_model=AiSignalChatResponse)
def ai_signal_chat(
    request: AiSignalChatRequest,
    db: Session = Depends(get_db)
) -> AiSignalChatResponse:
    """Send a message to AI signal generation assistant"""
    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = generate_signal_with_ai(
        db=db,
        account_id=request.account_id,
        user_message=request.user_message,
        conversation_id=request.conversation_id,
        user_id=user.id
    )

    return AiSignalChatResponse(
        success=result.get("success", False),
        conversation_id=result.get("conversation_id"),
        message_id=result.get("message_id"),
        content=result.get("content"),
        signal_configs=result.get("signal_configs"),
        error=result.get("error")
    )


@router.get("/ai-conversations")
def list_ai_signal_conversations(
    limit: int = 20,
    db: Session = Depends(get_db)
) -> dict:
    """Get list of AI signal generation conversations"""
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    conversations = get_signal_conversation_history(
        db=db,
        user_id=user.id,
        limit=limit
    )

    return {"conversations": conversations}


@router.get("/ai-conversations/{conversation_id}/messages")
def get_ai_signal_conversation_messages(
    conversation_id: int,
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
) -> dict:
    """Get all messages in a specific conversation with compression points and token usage"""
    import json as json_module
    from database.models import AiSignalConversation, HyperAiProfile
    from services.ai_context_compression_service import calculate_token_usage, restore_tool_calls_to_messages

    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    messages = get_signal_conversation_messages(
        db=db,
        conversation_id=conversation_id,
        user_id=user.id
    )

    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get compression points from conversation
    compression_points = []
    conversation = db.query(AiSignalConversation).filter(
        AiSignalConversation.id == conversation_id
    ).first()
    if conversation and conversation.compression_points:
        try:
            compression_points = json_module.loads(conversation.compression_points)
        except (json_module.JSONDecodeError, TypeError):
            compression_points = []

    # Determine model for token calculation: prefer account model, fallback to global
    token_model = None
    api_format = "openai"
    if account_id:
        from database.models import Account
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
        from services.ai_context_compression_service import get_last_compression_point
        from database.models import AiSignalMessage

        cp = get_last_compression_point(conversation) if conversation else None
        cp_msg_id = cp.get("message_id", 0) if cp else 0

        history_orm = db.query(AiSignalMessage).filter(
            AiSignalMessage.conversation_id == conversation_id,
            AiSignalMessage.id > cp_msg_id
        ).order_by(AiSignalMessage.created_at).all()

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


# ============ AI Signal Generation SSE Streaming ============

@router.post("/ai-chat-stream")
async def ai_signal_chat_stream(
    request: AiSignalChatRequest,
    db: Session = Depends(get_db)
):
    """
    Send a message to AI signal generation assistant.

    Supports two modes:
    - SSE streaming (default): Returns Server-Sent Events directly
    - Background task (useBackgroundTask=true): Returns task_id for polling

    Event types (SSE mode):
    - status: Progress status message
    - tool_call: Tool being called with arguments
    - tool_result: Result from tool execution
    - reasoning: AI reasoning content (for reasoning models)
    - content: AI response content chunk
    - signal_config: Parsed signal configuration
    - done: Completion with final result
    - error: Error occurred
    """
    from services.ai_stream_service import get_buffer_manager, generate_task_id, run_ai_task_in_background
    from database.connection import SessionLocal

    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Background task mode
    if request.use_background_task:
        task_id = generate_task_id("signal")
        manager = get_buffer_manager()

        # Check for existing running task
        if request.conversation_id:
            existing = manager.get_pending_task_for_conversation(request.conversation_id)
            if existing:
                return {"task_id": existing.task_id, "status": "already_running"}

        manager.create_task(task_id, conversation_id=request.conversation_id)

        # Capture request data
        account_id = request.account_id
        user_message = request.user_message
        conversation_id = request.conversation_id
        user_id = user.id

        def generator_func():
            bg_db = SessionLocal()
            try:
                yield from generate_signal_with_ai_stream(
                    db=bg_db,
                    account_id=account_id,
                    user_message=user_message,
                    conversation_id=conversation_id,
                    user_id=user_id
                )
            finally:
                bg_db.close()

        run_ai_task_in_background(task_id, generator_func)
        return {"task_id": task_id, "status": "started"}

    # SSE streaming mode (default)
    def event_generator():
        for event in generate_signal_with_ai_stream(
            db=db,
            account_id=request.account_id,
            user_message=request.user_message,
            conversation_id=request.conversation_id,
            user_id=user.id
        ):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ============ AI Signal Pool Creation ============

class SignalPoolConfigRequest(BaseModel):
    """Request for creating signal pool from AI-generated config"""
    name: str = Field(..., description="Pool name")
    symbol: str = Field(..., description="Trading symbol (e.g., BTC)")
    description: Optional[str] = Field(None, description="Pool description")
    logic: str = Field("AND", description="Combination logic: AND or OR")
    signals: List[dict] = Field(..., description="List of signal configurations")
    exchange: str = Field("hyperliquid", description="Exchange: hyperliquid or binance")

    class Config:
        populate_by_name = True


@router.post("/create-pool-from-config")
def create_pool_from_config(
    request: SignalPoolConfigRequest,
    db: Session = Depends(get_db)
):
    """
    Create a signal pool from AI-generated configuration.
    Creates individual signals and combines them into a pool.
    """
    import json

    if not request.signals:
        raise HTTPException(status_code=400, detail="No signals provided")

    if len(request.signals) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 signals per pool")

    created_signal_ids = []
    created_signals = []

    try:
        # Create each signal
        for i, sig in enumerate(request.signals):
            # Generate signal name if not provided
            sig_name = sig.get("name") or f"{request.name}_{i+1}"

            # Build trigger condition - handle taker_volume composite signal specially
            metric_name = sig.get("metric") or sig.get("indicator")

            if metric_name == "taker_volume":
                # taker_volume uses direction/ratio_threshold/volume_threshold instead of operator/threshold
                trigger_condition = {
                    "metric": metric_name,
                    "direction": sig.get("direction"),
                    "ratio_threshold": sig.get("ratio_threshold"),
                    "volume_threshold": sig.get("volume_threshold"),
                    "time_window": sig.get("time_window")
                }
                # Validate taker_volume required fields
                if not all([trigger_condition["metric"], trigger_condition["direction"],
                           trigger_condition["ratio_threshold"] is not None,
                           trigger_condition["volume_threshold"] is not None,
                           trigger_condition["time_window"]]):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Signal {i+1} (taker_volume) missing required fields (direction, ratio_threshold, volume_threshold, time_window)"
                    )
            else:
                # Standard signal with operator/threshold
                trigger_condition = {
                    "metric": metric_name,
                    "operator": sig.get("operator"),
                    "threshold": sig.get("threshold"),
                    "time_window": sig.get("time_window")
                }
                # Validate standard signal required fields
                if not all([trigger_condition["metric"], trigger_condition["operator"],
                           trigger_condition["threshold"] is not None, trigger_condition["time_window"]]):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Signal {i+1} missing required fields (metric, operator, threshold, time_window)"
                    )

            # Create signal with exchange
            result = db.execute(text("""
                INSERT INTO signal_definitions (signal_name, description, trigger_condition, enabled, exchange)
                VALUES (:name, :desc, :condition, :enabled, :exchange)
                RETURNING id, signal_name, description, trigger_condition, enabled, created_at, exchange
            """), {
                "name": sig_name,
                "desc": sig.get("description") or f"Part of {request.name}",
                "condition": json.dumps(trigger_condition),
                "enabled": True,
                "exchange": request.exchange
            })
            row = result.fetchone()
            created_signal_ids.append(row[0])
            created_signals.append({
                "id": row[0],
                "signal_name": row[1],
                "trigger_condition": trigger_condition,
                "exchange": request.exchange
            })

        # Create the pool with exchange
        pool_result = db.execute(text("""
            INSERT INTO signal_pools (pool_name, signal_ids, symbols, enabled, logic, exchange, source_type, source_config)
            VALUES (:name, :signal_ids, :symbols, :enabled, :logic, :exchange, :source_type, :source_config)
            RETURNING id, pool_name, signal_ids, symbols, enabled, created_at, logic, exchange, source_type, source_config
        """), {
            "name": request.name,
            "signal_ids": json.dumps(created_signal_ids),
            "symbols": json.dumps([request.symbol]),
            "enabled": True,
            "logic": request.logic,
            "exchange": request.exchange,
            "source_type": MARKET_SIGNAL_SOURCE,
            "source_config": json.dumps({}),
        })
        pool_row = pool_result.fetchone()

        db.commit()

        return {
            "success": True,
            "pool": {
                "id": pool_row[0],
                "pool_name": pool_row[1],
                "signal_ids": created_signal_ids,
                "symbols": [request.symbol],
                "logic": request.logic,
                "exchange": request.exchange,
                "source_type": MARKET_SIGNAL_SOURCE,
                "source_config": {},
            },
            "signals": created_signals
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create pool: {str(e)}")
