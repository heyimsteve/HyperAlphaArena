"""
Alpha Arena aggregated data routes.
Provides completed trades, model chat summaries, and consolidated positions
for showcasing multi-model trading activity on the dashboard.
"""

from datetime import datetime, timezone
from math import sqrt
from statistics import mean, pstdev
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database.connection import SessionLocal
from database.snapshot_connection import SnapshotSessionLocal
from database.models import (
    Account,
    Trade,
    Position,
    AIDecisionLog,
    Order,
    AccountStrategyConfig,
    PromptTemplate,
)
from database.snapshot_models import HyperliquidTrade
from services.asset_calculator import calc_positions_value
from services.price_cache import get_cached_price, cache_price
from services.market_data import get_last_price
from services.hyperliquid_trading_client import HyperliquidTradingClient
from services.hyperliquid_cache import (
    get_cached_account_state,
    get_cached_positions,
)
from utils.encryption import decrypt_private_key
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arena", tags=["arena"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_latest_price(symbol: str, market: str = "CRYPTO") -> Optional[float]:
    """Get the latest price using cache when possible, fallback to market feed."""
    price = get_cached_price(symbol, market)
    if price is not None:
        return price

    try:
        price = get_last_price(symbol, market)
        if price:
            cache_price(symbol, market, price)
        return price

    except Exception:
        return None


def _get_hyperliquid_positions(db: Session, account_id: Optional[int], environment: str) -> dict:
    """
    Get real-time positions from Hyperliquid API (testnet or mainnet)

    Args:
        db: Database session
        account_id: Optional account ID to filter
        environment: "testnet" or "mainnet"

    Returns:
        Dict with generated_at, trading_mode, and accounts list
    """
    from database.models import HyperliquidWallet

    # Get all AI accounts or specific account
    accounts_query = db.query(Account).filter(
        Account.account_type == "AI",
        Account.is_active == "true"
    )

    if account_id:
        accounts_query = accounts_query.filter(Account.id == account_id)

    accounts = accounts_query.all()
    snapshots = []

    for account in accounts:
        # Check if wallet exists for this environment (multi-wallet architecture)
        wallet = db.query(HyperliquidWallet).filter(
            HyperliquidWallet.account_id == account.id,
            HyperliquidWallet.environment == environment,
            HyperliquidWallet.is_active == "true"
        ).first()

        if not wallet:
            logger.debug(f"Account {account.name} (ID: {account.id}) has no {environment} wallet configured, skipping")
            continue

        encrypted_key = wallet.private_key_encrypted

        try:
            cached_state = get_cached_account_state(account.id, environment)
            account_state = cached_state["data"] if cached_state else None

            cached_positions = get_cached_positions(account.id, environment)
            positions_data = cached_positions["data"] if cached_positions else None

            wallet_address = None
            if isinstance(account_state, dict):
                wallet_address = account_state.get("wallet_address")

            client: Optional[HyperliquidTradingClient] = None
            needs_state = account_state is None
            needs_positions = positions_data is None
            needs_wallet = wallet_address is None

            if needs_state or needs_positions or needs_wallet:
                # Decrypt private key and fetch live data as needed
                private_key = decrypt_private_key(encrypted_key)
                client = HyperliquidTradingClient(
                    account_id=account.id,
                    private_key=private_key,
                    environment=environment
                )

                if needs_state:
                    account_state = client.get_account_state(db)
                    wallet_address = account_state.get("wallet_address") or client.wallet_address
                if needs_positions:
                    positions_data = client.get_positions(db)
                if wallet_address is None:
                    wallet_address = client.wallet_address

            if account_state is None or positions_data is None:
                logger.warning(f"Account {account.id} has no Hyperliquid data available, skipping")
                continue

            # Transform Hyperliquid positions to frontend format
            position_items = []
            total_unrealized = 0.0

            for p in positions_data:
                unrealized_pnl = p.get("unrealized_pnl", 0)
                total_unrealized += unrealized_pnl

                szi = float(p.get("szi", 0) or 0)
                entry_px = float(p.get("entry_px", 0) or 0)
                position_value = float(p.get("position_value", 0) or 0)
                notional = abs(szi) * entry_px
                avg_cost = entry_px
                current_price = position_value / abs(szi) if szi != 0 else entry_px

                position_items.append({
                    "id": 0,  # Hyperliquid positions don't have local DB ID
                    "symbol": p.get("coin", "") or "",
                    "name": p.get("coin", "") or "",
                    "market": "HYPERLIQUID_PERP",
                    "side": "LONG" if szi > 0 else "SHORT",
                    "quantity": abs(szi),
                    "avg_cost": avg_cost,
                    "current_price": current_price,
                    "notional": notional,
                    "current_value": position_value,
                    "unrealized_pnl": float(unrealized_pnl),
                    "leverage": p.get("leverage"),
                    "margin_used": float(p.get("margin_used", 0) or 0),
                    "return_on_equity": float(p.get("return_on_equity", 0) or 0),
                    "percentage": float(p.get("percentage", 0) or 0),
                    "margin_mode": p.get("margin_mode", "cross"),
                    "liquidation_px": float(p.get("liquidation_px", 0) or 0),
                    "max_leverage": p.get("max_leverage"),
                    "leverage_type": p.get("leverage_type"),
                })

            # Calculate total return
            total_equity = account_state.get("total_equity", 0)
            available_balance = account_state.get("available_balance", 0)
            used_margin = account_state.get("used_margin", 0)

            # Positions value is the used margin (capital tied up in positions)
            # Or equivalently: total_equity - available_balance
            positions_value = used_margin

            initial_capital = float(account.initial_capital or 0)
            total_return = None
            if initial_capital > 0:
                total_return = (total_equity - initial_capital) / initial_capital

            snapshots.append({
                "account_id": account.id,
                "account_name": account.name,
                "model": account.model,
                "environment": environment,
                "wallet_address": wallet_address,
                "total_unrealized_pnl": total_unrealized,
                "available_cash": available_balance,
                "used_margin": used_margin,
                "positions_value": positions_value,  # Add positions_value from Hyperliquid data
                "positions": position_items,
                "total_assets": total_equity,
                "margin_usage_percent": account_state.get("margin_usage_percent", 0),
                "margin_mode": "cross",
                "initial_capital": initial_capital,
                "total_return": total_return,
            })

        except Exception as e:
            logger.error(f"Failed to get Hyperliquid positions for account {account.id}: {e}", exc_info=True)
            # Fallback: still expose the account so frontend doesn't think it's missing
            snapshots.append({
                "account_id": account.id,
                "account_name": account.name,
                "model": account.model,
                "environment": environment,
                "wallet_address": wallet.wallet_address if 'wallet' in locals() and wallet else None,
                "total_unrealized_pnl": 0.0,
                "available_cash": 0.0,
                "used_margin": 0.0,
                "positions_value": 0.0,
                "positions": [],
                "total_assets": float(account.initial_capital or 0),
                "margin_usage_percent": 0.0,
                "margin_mode": "cross",
                "initial_capital": float(account.initial_capital or 0),
                "total_return": 0.0,
            })
            continue

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "trading_mode": environment,
        "accounts": snapshots,
    }


def _analyze_balance_series(balances: List[float]) -> Tuple[float, float, List[float], float]:
    '''Return biggest gain/loss deltas, percentage returns, and balance volatility.'''
    if len(balances) < 2:
        return 0.0, 0.0, [], 0.0

    biggest_gain = float('-inf')
    biggest_loss = float('inf')
    returns: List[float] = []

    previous = balances[0]

    for current in balances[1:]:
        delta = current - previous
        if delta > biggest_gain:
            biggest_gain = delta
        if delta < biggest_loss:
            biggest_loss = delta

        if previous not in (0, None):
            try:
                returns.append(delta / previous)
            except ZeroDivisionError:
                pass

        previous = current

    if biggest_gain == float('-inf'):
        biggest_gain = 0.0
    if biggest_loss == float('inf'):
        biggest_loss = 0.0

    volatility = pstdev(balances) if len(balances) > 1 else 0.0

    return biggest_gain, biggest_loss, returns, volatility


def _compute_sharpe_ratio(returns: List[float]) -> Optional[float]:
    '''Compute a simple Sharpe ratio approximation using sample returns.'''
    if len(returns) < 2:
        return None

    avg_return = mean(returns)
    volatility = pstdev(returns)
    if volatility == 0:
        return None

    scaled_factor = sqrt(len(returns))
    return avg_return / volatility * scaled_factor


def _aggregate_account_stats(db: Session, account: Account) -> Dict[str, Optional[float]]:
    '''Aggregate trade and decision statistics for a given account.'''
    initial_capital = float(account.initial_capital or 0)
    current_cash = float(account.current_cash or 0)
    positions_value = calc_positions_value(db, account.id)
    total_assets = positions_value + current_cash
    total_pnl = total_assets - initial_capital
    total_return_pct = (
        (total_assets - initial_capital) / initial_capital if initial_capital else None
    )

    trades: List[Trade] = (
        db.query(Trade)
        .filter(Trade.account_id == account.id)
        .order_by(Trade.trade_time.asc())
        .all()
    )
    trade_count = len(trades)
    total_fees = sum(float(trade.commission or 0) for trade in trades)
    total_volume = sum(
        abs(float(trade.price or 0) * float(trade.quantity or 0)) for trade in trades
    )
    first_trade_time = trades[0].trade_time.isoformat() if trades else None
    last_trade_time = trades[-1].trade_time.isoformat() if trades else None

    decisions: List[AIDecisionLog] = (
        db.query(AIDecisionLog)
        .filter(AIDecisionLog.account_id == account.id)
        .order_by(AIDecisionLog.decision_time.asc())
        .all()
    )
    balances = [
        float(dec.total_balance)
        for dec in decisions
        if dec.total_balance is not None
    ]

    biggest_gain, biggest_loss, returns, balance_volatility = _analyze_balance_series(
        balances
    )
    sharpe_ratio = _compute_sharpe_ratio(returns)

    wins = len([r for r in returns if r > 0])
    losses = len([r for r in returns if r < 0])
    win_rate = wins / len(returns) if returns else None
    loss_rate = losses / len(returns) if returns else None

    executed_decisions = len([d for d in decisions if d.executed == 'true'])
    decision_execution_rate = (
        executed_decisions / len(decisions) if decisions else None
    )
    avg_target_portion = (
        mean(float(d.target_portion or 0) for d in decisions) if decisions else None
    )

    avg_decision_interval_minutes = None
    if len(decisions) > 1:
        intervals = []
        previous = decisions[0].decision_time
        for decision in decisions[1:]:
            if decision.decision_time and previous:
                delta = decision.decision_time - previous
                intervals.append(delta.total_seconds() / 60.0)
            previous = decision.decision_time
        avg_decision_interval_minutes = mean(intervals) if intervals else None

    return {
        'account_id': account.id,
        'account_name': account.name,
        'model': account.model,
        'initial_capital': initial_capital,
        'current_cash': current_cash,
        'positions_value': positions_value,
        'total_assets': total_assets,
        'total_pnl': total_pnl,
        'total_return_pct': total_return_pct,
        'total_fees': total_fees,
        'trade_count': trade_count,
        'total_volume': total_volume,
        'first_trade_time': first_trade_time,
        'last_trade_time': last_trade_time,
        'biggest_gain': biggest_gain,
        'biggest_loss': biggest_loss,
        'win_rate': win_rate,
        'loss_rate': loss_rate,
        'sharpe_ratio': sharpe_ratio,
        'balance_volatility': balance_volatility,
        'decision_count': len(decisions),
        'executed_decisions': executed_decisions,
        'decision_execution_rate': decision_execution_rate,
        'avg_target_portion': avg_target_portion,
        'avg_decision_interval_minutes': avg_decision_interval_minutes,
    }


@router.get("/trades")
def get_completed_trades(
    limit: int = Query(100, ge=1, le=500),
    account_id: Optional[int] = None,
    trading_mode: Optional[str] = Query(None, regex="^(paper|testnet|mainnet)$"),
    wallet_address: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Return recent trades across all AI accounts, filtered by trading mode."""
    if wallet_address and trading_mode not in ("testnet", "mainnet"):
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "accounts": [],
            "trades": [],
        }
    if trading_mode in ("testnet", "mainnet"):
        snapshot_db = SnapshotSessionLocal()
        try:
            query = snapshot_db.query(HyperliquidTrade).order_by(desc(HyperliquidTrade.trade_time))
            # Strictly filter by environment and exclude NULL
            query = query.filter(
                HyperliquidTrade.environment == trading_mode,
                HyperliquidTrade.environment.isnot(None)
            )
            if account_id:
                query = query.filter(HyperliquidTrade.account_id == account_id)
            if wallet_address:
                query = query.filter(HyperliquidTrade.wallet_address == wallet_address)
            if symbol:
                query = query.filter(HyperliquidTrade.symbol == symbol)

            hyper_trades = query.limit(limit).all()
        finally:
            snapshot_db.close()

        if not hyper_trades:
            return {
                "generated_at": datetime.utcnow().isoformat(),
                "accounts": [],
                "trades": [],
            }

        account_ids = {trade.account_id for trade in hyper_trades}
        account_map = {
            acc.id: acc
            for acc in db.query(Account).filter(Account.id.in_(account_ids)).all()
        }

        # Batch fetch decision logs by hyperliquid_order_id to get signal/prompt info
        order_ids = {trade.order_id for trade in hyper_trades if trade.order_id}
        decision_map = {}
        prompt_template_ids = set()
        if order_ids:
            decisions = db.query(AIDecisionLog).filter(
                AIDecisionLog.hyperliquid_order_id.in_(order_ids)
            ).all()
            for d in decisions:
                if d.hyperliquid_order_id:
                    decision_map[d.hyperliquid_order_id] = d
                    if d.prompt_template_id:
                        prompt_template_ids.add(d.prompt_template_id)

        # Batch fetch prompt template names
        prompt_template_map = {}
        if prompt_template_ids:
            templates = db.query(PromptTemplate).filter(PromptTemplate.id.in_(prompt_template_ids)).all()
            prompt_template_map = {t.id: t.name for t in templates}

        trades: List[dict] = []
        accounts_meta: Dict[int, dict] = {}

        for trade in hyper_trades:
            account = account_map.get(trade.account_id)
            if not account:
                logger.warning(f"Hyperliquid trade references missing account_id={trade.account_id}")
                continue

            quantity = float(trade.quantity)
            price = float(trade.price)
            notional = float(trade.trade_value)
            commission = float(trade.fee or 0)
            side = trade.side.upper()

            # Get decision info for signal/prompt
            decision = decision_map.get(trade.order_id) if trade.order_id else None
            signal_trigger_id = decision.signal_trigger_id if decision else None
            prompt_template_id = decision.prompt_template_id if decision else None
            prompt_template_name = prompt_template_map.get(prompt_template_id) if prompt_template_id else None

            trades.append(
                {
                    "trade_id": trade.id,
                    "order_id": None,
                    "order_no": trade.order_id,
                    "account_id": account.id,
                    "account_name": account.name,
                    "model": account.model,
                    "side": side,
                    "direction": "LONG" if side == "BUY" else "SHORT",
                    "symbol": trade.symbol,
                    "market": "HYPERLIQUID_PERP",
                    "price": price,
                    "quantity": quantity,
                    "notional": notional,
                    "commission": commission,
                    "trade_time": trade.trade_time.isoformat() if trade.trade_time else None,
                    "wallet_address": trade.wallet_address,
                    "signal_trigger_id": signal_trigger_id,
                    "prompt_template_id": prompt_template_id,
                    "prompt_template_name": prompt_template_name,
                }
            )

            accounts_meta[account.id] = {
                "account_id": account.id,
                "name": account.name,
                "model": account.model,
            }

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "accounts": list(accounts_meta.values()),
            "trades": trades,
        }

    # Paper mode (or no filter) falls back to paper trades table
    query = (
        db.query(Trade, Account)
        .join(Account, Trade.account_id == Account.id)
        .order_by(desc(Trade.trade_time))
    )

    if account_id:
        query = query.filter(Trade.account_id == account_id)

    if trading_mode == "paper":
        query = query.filter(Trade.hyperliquid_environment == None)
    elif trading_mode in ("testnet", "mainnet"):
        query = query.filter(Trade.hyperliquid_environment == trading_mode)

    trade_rows = query.limit(limit).all()

    if not trade_rows:
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "accounts": [],
            "trades": [],
        }

    trades: List[dict] = []
    accounts_meta = {}

    for trade, account in trade_rows:
        quantity = float(trade.quantity)
        price = float(trade.price)
        notional = price * quantity

        order_no = None
        if trade.order_id:
            order = db.query(Order).filter(Order.id == trade.order_id).first()
            if order:
                order_no = order.order_no

        trades.append(
            {
                "trade_id": trade.id,
                "order_id": trade.order_id,
                "order_no": order_no,
                "account_id": account.id,
                "account_name": account.name,
                "model": account.model,
                "side": trade.side,
                "direction": "LONG" if (trade.side or "").upper() == "BUY" else "SHORT",
                "symbol": trade.symbol,
                "market": trade.market,
                "price": price,
                "quantity": quantity,
                "notional": notional,
                "commission": float(trade.commission),
                "trade_time": trade.trade_time.isoformat() if trade.trade_time else None,
                "wallet_address": None,
            }
        )

        accounts_meta[account.id] = {
            "account_id": account.id,
            "name": account.name,
            "model": account.model,
        }

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "accounts": list(accounts_meta.values()),
        "trades": trades,
    }


@router.get("/model-chat")
def get_model_chat(
    limit: int = Query(60, ge=1, le=200),
    account_id: Optional[int] = None,
    trading_mode: Optional[str] = Query(None, regex="^(paper|testnet|mainnet)$"),
    wallet_address: Optional[str] = Query(None),
    before_time: Optional[str] = Query(None, description="ISO format timestamp for cursor-based pagination"),
    include_snapshots: bool = Query(False, description="Include prompt/reasoning/decision snapshots (heavy data)"),
    symbol: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Return recent AI decision logs as chat-style summaries, filtered by trading mode."""
    query = (
        db.query(AIDecisionLog, Account)
        .join(Account, AIDecisionLog.account_id == Account.id)
        .order_by(desc(AIDecisionLog.decision_time))
    )

    if account_id:
        query = query.filter(AIDecisionLog.account_id == account_id)

    if wallet_address:
        query = query.filter(AIDecisionLog.wallet_address == wallet_address)

    # Cursor-based pagination: only get records before the specified time
    if before_time:
        try:
            before_dt = datetime.fromisoformat(before_time.replace('Z', '+00:00'))
            query = query.filter(AIDecisionLog.decision_time < before_dt)
        except (ValueError, AttributeError) as e:
            logger.warning(f"Invalid before_time parameter: {before_time}, error: {e}")

    if symbol:
        query = query.filter(AIDecisionLog.symbol == symbol)

    # Filter by trading mode based on hyperliquid_environment field
    if trading_mode:
        if trading_mode == "paper":
            query = query.filter(AIDecisionLog.hyperliquid_environment == None)
        else:
            # For testnet/mainnet, strictly match environment and exclude NULL
            query = query.filter(
                AIDecisionLog.hyperliquid_environment == trading_mode,
                AIDecisionLog.hyperliquid_environment.isnot(None)
            )

    decision_rows = query.limit(limit).all()

    if not decision_rows:
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "entries": [],
        }

    entries: List[dict] = []

    account_ids = {account.id for _, account in decision_rows}
    strategy_map = {
        cfg.account_id: cfg
        for cfg in db.query(AccountStrategyConfig)
        .filter(AccountStrategyConfig.account_id.in_(account_ids))
        .all()
    }

    # Batch fetch prompt template names
    prompt_template_ids = {log.prompt_template_id for log, _ in decision_rows if log.prompt_template_id}
    prompt_template_map = {}
    if prompt_template_ids:
        templates = db.query(PromptTemplate).filter(PromptTemplate.id.in_(prompt_template_ids)).all()
        prompt_template_map = {t.id: t.name for t in templates}

    for log, account in decision_rows:
        strategy = strategy_map.get(account.id)
        last_trigger_iso = None
        trigger_latency = None
        trigger_mode = None
        strategy_enabled = None

        if strategy:
            trigger_mode = "unified"
            strategy_enabled = strategy.enabled == "true"
            if strategy.last_trigger_at:
                last_dt = strategy.last_trigger_at
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                last_trigger_iso = last_dt.isoformat()

                log_dt = log.decision_time
                if log_dt:
                    if log_dt.tzinfo is None:
                        log_dt = log_dt.replace(tzinfo=timezone.utc)
                    try:
                        trigger_latency = abs((log_dt - last_dt).total_seconds())
                    except Exception:
                        trigger_latency = None

        entry = {
            "id": log.id,
            "account_id": account.id,
            "account_name": account.name,
            "model": account.model,
            "operation": log.operation,
            "symbol": log.symbol,
            "reason": log.reason,
            "executed": log.executed == "true",
            "prev_portion": float(log.prev_portion or 0),
            "target_portion": float(log.target_portion or 0),
            "total_balance": float(log.total_balance or 0),
            "order_id": log.order_id,
            "decision_time": log.decision_time.isoformat()
            if log.decision_time
            else None,
            "trigger_mode": trigger_mode,
            "strategy_enabled": strategy_enabled,
            "last_trigger_at": last_trigger_iso,
            "trigger_latency_seconds": trigger_latency,
            "wallet_address": log.wallet_address,
            "signal_trigger_id": log.signal_trigger_id,
            "prompt_template_id": log.prompt_template_id,
            "prompt_template_name": prompt_template_map.get(log.prompt_template_id) if log.prompt_template_id else None,
        }

        # Only include heavy snapshot fields when explicitly requested
        if include_snapshots:
            entry["prompt_snapshot"] = log.prompt_snapshot
            entry["reasoning_snapshot"] = log.reasoning_snapshot
            entry["decision_snapshot"] = log.decision_snapshot

        entries.append(entry)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "entries": entries,
    }


@router.get("/model-chat/{decision_id}/snapshots")
def get_model_chat_snapshots(
    decision_id: int,
    db: Session = Depends(get_db),
):
    """Return snapshot fields for a single AI decision log entry."""
    log = db.query(AIDecisionLog).filter(AIDecisionLog.id == decision_id).first()

    if not log:
        return {
            "error": "Decision not found",
            "id": decision_id,
        }

    return {
        "id": log.id,
        "prompt_snapshot": log.prompt_snapshot,
        "reasoning_snapshot": log.reasoning_snapshot,
        "decision_snapshot": log.decision_snapshot,
    }


@router.get("/positions")
def get_positions_snapshot(
    account_id: Optional[int] = None,
    trading_mode: Optional[str] = Query(None, regex="^(paper|testnet|mainnet)$"),
    db: Session = Depends(get_db),
):
    """Return consolidated positions and cash for active AI accounts, filtered by trading mode."""

    # For Hyperliquid modes (testnet/mainnet), fetch real-time data from Hyperliquid API
    if trading_mode and trading_mode in ["testnet", "mainnet"]:
        return _get_hyperliquid_positions(db, account_id, trading_mode)

    # For paper mode (or no mode specified), query local database
    accounts_query = db.query(Account).filter(
        Account.account_type == "AI",
        Account.is_active == "true",
    )

    if account_id:
        accounts_query = accounts_query.filter(Account.id == account_id)

    accounts = accounts_query.all()

    snapshots: List[dict] = []

    for account in accounts:
        positions = (
            db.query(Position)
            .filter(Position.account_id == account.id, Position.quantity > 0)
            .order_by(Position.symbol.asc())
            .all()
        )

        position_items: List[dict] = []
        total_unrealized = 0.0

        for pos in positions:
            quantity = float(pos.quantity)
            avg_cost = float(pos.avg_cost)
            base_notional = quantity * avg_cost

            last_price = _get_latest_price(pos.symbol, pos.market)
            if last_price is None:
                last_price = avg_cost

            current_value = last_price * quantity
            unrealized = current_value - base_notional
            total_unrealized += unrealized

            position_items.append(
                {
                    "id": pos.id,
                    "symbol": pos.symbol,
                    "name": pos.name,
                    "market": pos.market,
                    "side": "LONG" if quantity >= 0 else "SHORT",
                    "quantity": quantity,
                    "avg_cost": avg_cost,
                    "current_price": last_price,
                    "notional": base_notional,
                    "current_value": current_value,
                    "unrealized_pnl": unrealized,
                }
            )

        total_assets = (
            calc_positions_value(db, account.id) + float(account.current_cash or 0)
        )
        total_return = None
        if account.initial_capital:
            try:
                total_return = (
                    (total_assets - float(account.initial_capital))
                    / float(account.initial_capital)
                )
            except ZeroDivisionError:
                total_return = None

        snapshots.append(
            {
                "account_id": account.id,
                "account_name": account.name,
                "model": account.model,
                "total_unrealized_pnl": total_unrealized,
                "available_cash": float(account.current_cash or 0),
                "positions": position_items,
                "total_assets": total_assets,
                "initial_capital": float(account.initial_capital or 0),
                "total_return": total_return,
            }
        )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "trading_mode": trading_mode or "paper",
        "accounts": snapshots,
    }



@router.get("/analytics")
def get_aggregated_analytics(
    account_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    '''Return leaderboard-style analytics for AI accounts.'''
    accounts_query = db.query(Account).filter(
        Account.account_type == "AI",
    )

    if account_id:
        accounts_query = accounts_query.filter(Account.id == account_id)

    accounts = accounts_query.all()

    if not accounts:
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "accounts": [],
            "summary": {
                "total_assets": 0.0,
                "total_pnl": 0.0,
                "total_return_pct": None,
                "total_fees": 0.0,
                "total_volume": 0.0,
                "average_sharpe_ratio": None,
            },
        }

    analytics = []
    total_assets_all = 0.0
    total_initial = 0.0
    total_fees_all = 0.0
    total_volume_all = 0.0
    sharpe_values = []

    for account in accounts:
        stats = _aggregate_account_stats(db, account)
        analytics.append(stats)
        total_assets_all += stats.get("total_assets") or 0.0
        total_initial += stats.get("initial_capital") or 0.0
        total_fees_all += stats.get("total_fees") or 0.0
        total_volume_all += stats.get("total_volume") or 0.0
        if stats.get("sharpe_ratio") is not None:
            sharpe_values.append(stats["sharpe_ratio"])

    analytics.sort(
        key=lambda item: item.get("total_return_pct") if item.get("total_return_pct") is not None else float("-inf"),
        reverse=True,
    )

    average_sharpe = mean(sharpe_values) if sharpe_values else None
    total_pnl_all = total_assets_all - total_initial
    total_return_pct = (
        total_pnl_all / total_initial if total_initial else None
    )

    summary = {
        "total_assets": total_assets_all,
        "total_pnl": total_pnl_all,
        "total_return_pct": total_return_pct,
        "total_fees": total_fees_all,
        "total_volume": total_volume_all,
        "average_sharpe_ratio": average_sharpe,
    }

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "accounts": analytics,
        "summary": summary,
    }


@router.get("/check-pnl-status")
def check_pnl_sync_status(
    trading_mode: Optional[str] = Query(None, regex="^(paper|testnet|mainnet)$"),
    db: Session = Depends(get_db),
):
    """
    Check if there are trades that need PnL synchronization.
    Returns the count of unsynchronized trades.
    """
    query = db.query(AIDecisionLog).filter(
        AIDecisionLog.operation.in_(["buy", "sell", "close"]),
        AIDecisionLog.executed == "true",
        AIDecisionLog.pnl_updated_at == None,
    )

    if trading_mode:
        if trading_mode == "paper":
            query = query.filter(AIDecisionLog.hyperliquid_environment == None)
        else:
            query = query.filter(AIDecisionLog.hyperliquid_environment == trading_mode)
    else:
        # Only check Hyperliquid trades (testnet/mainnet)
        query = query.filter(AIDecisionLog.hyperliquid_environment.isnot(None))

    unsync_count = query.count()

    return {
        "needs_sync": unsync_count > 0,
        "unsync_count": unsync_count,
    }


@router.post("/update-pnl")
def update_pnl_data(db: Session = Depends(get_db)):
    """
    Update realized PnL and fee data for all trades by fetching from Hyperliquid API.

    This endpoint:
    1. Fetches user_fills from all configured wallets (testnet + mainnet)
    2. Updates HyperliquidTrade.fee with actual fee data
    3. Updates AIDecisionLog.realized_pnl for closed positions

    Returns summary of updated records.
    """
    from database.models import HyperliquidWallet, AccountPromptBinding
    from services.hyperliquid_environment import get_hyperliquid_client
    from decimal import Decimal
    from collections import defaultdict

    result = {
        "success": True,
        "environments": {},
        "errors": [],
    }

    snapshot_db = SnapshotSessionLocal()

    try:
        # Get all configured wallets
        wallets = db.query(HyperliquidWallet).all()
        if not wallets:
            return {
                "success": False,
                "message": "No Hyperliquid wallets configured",
                "environments": {},
                "errors": [],
            }

        # Group wallets by (account_id, environment) to avoid duplicates
        wallet_configs = {}
        for w in wallets:
            key = (w.account_id, w.environment)
            if key not in wallet_configs:
                wallet_configs[key] = w

        # Process each wallet
        all_fills_by_env = defaultdict(list)

        for (account_id, environment), wallet in wallet_configs.items():
            try:
                client = get_hyperliquid_client(db, account_id, override_environment=environment)
                fills = client._get_user_fills(db)
                all_fills_by_env[environment].extend(fills)
                logger.info(f"Fetched {len(fills)} fills for account {account_id} on {environment}")
            except Exception as e:
                error_msg = f"Failed to fetch fills for account {account_id} on {environment}: {str(e)}"
                logger.warning(error_msg)
                result["errors"].append(error_msg)

        # Process fills for each environment
        for environment, fills in all_fills_by_env.items():
            env_result = _process_fills_for_environment(
                db, snapshot_db, environment, fills
            )
            result["environments"][environment] = env_result

        # Commit all changes
        snapshot_db.commit()
        db.commit()

    except Exception as e:
        logger.error(f"Error updating PnL data: {e}", exc_info=True)
        result["success"] = False
        result["errors"].append(str(e))
        snapshot_db.rollback()
        db.rollback()
    finally:
        snapshot_db.close()

    return result


def _process_fills_for_environment(
    db: Session,
    snapshot_db: Session,
    environment: str,
    fills: List[dict],
) -> dict:
    """
    Process fills for a specific environment and update database records.

    Returns summary of updates.
    """
    from decimal import Decimal
    from collections import defaultdict

    result = {
        "fills_count": len(fills),
        "unique_orders": 0,
        "trades_updated": 0,
        "decisions_updated": 0,
        "skipped": 0,
    }

    if not fills:
        return result

    # Aggregate fills by order ID
    # One order can have multiple fills (partial executions)
    order_aggregates = defaultdict(lambda: {
        "total_fee": Decimal("0"),
        "total_pnl": Decimal("0"),
        "fills": [],
    })

    for fill in fills:
        oid = str(fill.get("oid", ""))
        if not oid:
            continue

        fee = Decimal(str(fill.get("fee", "0")))
        closed_pnl = Decimal(str(fill.get("closedPnl", "0")))

        order_aggregates[oid]["total_fee"] += fee
        order_aggregates[oid]["total_pnl"] += closed_pnl
        order_aggregates[oid]["fills"].append(fill)

    result["unique_orders"] = len(order_aggregates)

    # Update HyperliquidTrade records
    trades = snapshot_db.query(HyperliquidTrade).filter(
        HyperliquidTrade.environment == environment
    ).all()

    for trade in trades:
        order_id = str(trade.order_id)
        if order_id in order_aggregates:
            agg = order_aggregates[order_id]
            # Update fee
            if trade.fee != agg["total_fee"]:
                trade.fee = agg["total_fee"]
                result["trades_updated"] += 1
        else:
            result["skipped"] += 1

    # Update AIDecisionLog records
    # Match by hyperliquid_order_id, tp_order_id, sl_order_id and accumulate PnL
    decisions = db.query(AIDecisionLog).filter(
        AIDecisionLog.operation.in_(["buy", "sell", "close"]),
        AIDecisionLog.executed == "true",
        AIDecisionLog.hyperliquid_environment == environment,
    ).all()

    for decision in decisions:
        updated = False
        total_pnl = Decimal("0")
        matched_order_ids = set()

        # Try direct match by hyperliquid_order_id, tp_order_id, sl_order_id
        order_ids_to_check = [
            decision.hyperliquid_order_id,
            decision.tp_order_id,
            decision.sl_order_id,
        ]

        for oid in order_ids_to_check:
            if oid:
                order_id_str = str(oid)
                if order_id_str in order_aggregates and order_id_str not in matched_order_ids:
                    agg = order_aggregates[order_id_str]
                    total_pnl += agg["total_pnl"]
                    matched_order_ids.add(order_id_str)

        if total_pnl != 0:
            decision.realized_pnl = total_pnl
            decision.pnl_updated_at = datetime.utcnow()
            updated = True

        # Fallback: match by time window using HyperliquidTrade (only if no direct match)
        if not updated and decision.decision_time:
            from datetime import timedelta
            time_window = timedelta(minutes=5)

            matching_trade = snapshot_db.query(HyperliquidTrade).filter(
                HyperliquidTrade.account_id == decision.account_id,
                HyperliquidTrade.symbol == decision.symbol,
                HyperliquidTrade.environment == environment,
                HyperliquidTrade.trade_time >= decision.decision_time - time_window,
                HyperliquidTrade.trade_time <= decision.decision_time + time_window,
            ).first()

            if matching_trade:
                order_id = str(matching_trade.order_id)
                if order_id in order_aggregates:
                    agg = order_aggregates[order_id]
                    if agg["total_pnl"] != 0:
                        decision.realized_pnl = agg["total_pnl"]
                        decision.pnl_updated_at = datetime.utcnow()
                        updated = True

                    # Also update hyperliquid_order_id for future direct matching
                    if not decision.hyperliquid_order_id:
                        decision.hyperliquid_order_id = order_id

        if updated:
            result["decisions_updated"] += 1

    return result
