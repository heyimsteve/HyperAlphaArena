"""
Signal Detection Service

Detects signal triggers based on market flow data.
Uses edge-triggered logic: only triggers when condition changes from False to True.
"""

import json
import logging
import time
from typing import Dict, List, Optional, Any
from decimal import Decimal
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _get_market_regime_for_trigger(symbol: str, timeframe: str = "5m") -> Optional[str]:
    """Get market regime classification for a trigger and return as JSON string.

    Args:
        symbol: Trading symbol (e.g., "BTC")
        timeframe: Time window for regime calculation (e.g., "5m", "15m", "1h")

    Returns:
        JSON string with regime, direction, confidence, reason, timeframe, and indicators
    """
    try:
        from database.connection import SessionLocal
        from services.market_regime_service import get_market_regime
        from datetime import datetime

        db = SessionLocal()
        try:
            # FIX: Pass current timestamp and use_realtime=True to fetch current K-line from API
            # This ensures regime calculation uses the latest market data including unfinished candles
            timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
            result = get_market_regime(
                db, symbol, timeframe,
                timestamp_ms=timestamp_ms,
                use_realtime=True
            )
            return json.dumps({
                "symbol": symbol,
                "timeframe": timeframe,
                "regime": result.get("regime"),
                "direction": result.get("direction"),
                "confidence": result.get("confidence"),
                "reason": result.get("reason"),
                "indicators": result.get("indicators", {}),
            })
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to get market regime for {symbol}/{timeframe}: {e}")
        return None


@dataclass
class SignalState:
    """Track the active state of a signal for edge detection"""
    signal_id: int
    symbol: str
    is_active: bool = False
    last_value: Optional[float] = None
    last_check_time: float = 0


@dataclass
class PoolState:
    """Track the active state of a signal pool for edge detection"""
    pool_id: int
    symbol: str
    is_active: bool = False
    last_check_time: float = 0
    # Track which signals in the pool are currently meeting their conditions
    signal_conditions_met: Dict[int, bool] = field(default_factory=dict)


class SignalDetectionService:
    """
    Service for detecting signal triggers based on market flow data.
    Implements edge-triggered logic to avoid repeated triggers.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Signal states for edge detection: {(signal_id, symbol): SignalState}
        self.signal_states: Dict[tuple, SignalState] = {}

        # Pool states for edge detection: {(pool_id, symbol): PoolState}
        self.pool_states: Dict[tuple, PoolState] = {}

        # Cache of enabled signal pools and their signals
        self._signal_pools_cache: List[dict] = []
        self._signals_cache: Dict[int, dict] = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 60  # Refresh cache every 60 seconds

        # Callbacks for signal triggers (used by trading_strategy.py)
        self._trigger_callbacks: List[callable] = []

        logger.info("SignalDetectionService initialized")

    def subscribe_signal_triggers(self, callback: callable) -> None:
        """Register a callback to be called when a signal pool triggers.

        Callback signature: callback(symbol: str, pool: dict, market_data: dict, triggered_signals: list)
        """
        if callback not in self._trigger_callbacks:
            self._trigger_callbacks.append(callback)
            logger.info(f"Signal trigger callback registered: {callback.__name__ if hasattr(callback, '__name__') else callback}")

    def unsubscribe_signal_triggers(self, callback: callable) -> None:
        """Unregister a signal trigger callback."""
        if callback in self._trigger_callbacks:
            self._trigger_callbacks.remove(callback)
            logger.info(f"Signal trigger callback unregistered: {callback.__name__ if hasattr(callback, '__name__') else callback}")

    def detect_signals(
        self, symbol: str, market_data: Dict[str, Any], exchange: str = None
    ) -> List[dict]:
        """
        Detect triggered signals for a symbol based on current market data.
        Returns list of triggered pools (edge-triggered at pool level).

        Args:
            symbol: Trading symbol (e.g., "BTC")
            market_data: Current market data context
            exchange: Filter by exchange ("hyperliquid", "binance", or None for all)

        Pool-level logic:
        - OR: Pool triggers when ANY signal condition is met (and pool was not active)
        - AND: Pool triggers when ALL signal conditions are met (and pool was not active)
        """
        triggered_pools = []

        try:
            # Refresh cache if needed
            self._refresh_cache_if_needed()

            # Get all enabled signal pools that monitor this symbol
            relevant_pools = [
                pool for pool in self._signal_pools_cache
                if pool.get("enabled") and symbol in pool.get("symbols", [])
                and (exchange is None or pool.get("exchange", "hyperliquid") == exchange)
            ]

            if not relevant_pools:
                return []

            # Process each pool
            for pool in relevant_pools:
                pool_trigger = self._check_pool_trigger(pool, symbol, market_data)
                if pool_trigger:
                    triggered_pools.append(pool_trigger)
                    # Notify all registered callbacks
                    self._notify_callbacks(symbol, pool_trigger, market_data)

        except Exception as e:
            logger.error(f"Error detecting signals for {symbol}: {e}", exc_info=True)

        return triggered_pools

    def _notify_callbacks(self, symbol: str, pool_trigger: dict, market_data: Dict[str, Any]) -> None:
        """Notify all registered callbacks about a signal pool trigger."""
        pool_name = pool_trigger.get("pool_name", "Unknown")
        pool_id = pool_trigger.get("pool_id")
        print(f"[SignalDetection] _notify_callbacks called for pool {pool_name}, callbacks count: {len(self._trigger_callbacks)}")

        # Bot push notification for signal pool triggers
        try:
            from database.connection import SessionLocal
            from api.bot_routes import get_notification_config_dict
            from services.bot_event_service import enqueue_system_event, push_event_to_all_channels
            import asyncio
            db = SessionLocal()
            try:
                notif_config = get_notification_config_dict(db)
                signal_pools_config = notif_config.get("signal_pools", {})
                # Check if this specific pool has notification enabled
                pool_id_str = str(pool_id) if pool_id else None
                if pool_id_str and signal_pools_config.get(pool_id_str, False):
                    triggered_signals = pool_trigger.get("signals_triggered", [])
                    event_data = {
                        "pool_name": pool_name,
                        "pool_id": pool_id,
                        "symbol": symbol,
                        "triggered_signals": triggered_signals,
                    }
                    if pool_trigger.get("trigger_type") == "wallet_signal":
                        event_data["wallet_event"] = pool_trigger.get("wallet_event")
                    results = enqueue_system_event(db, "signal_triggered", event_data)
                    if results:
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(push_event_to_all_channels(db, results))
                        except RuntimeError:
                            asyncio.run(push_event_to_all_channels(db, results))
            finally:
                db.close()
        except Exception as notif_err:
            logger.warning(f"Failed to send bot notification for signal pool: {notif_err}")

        if not self._trigger_callbacks:
            print("[SignalDetection] No callbacks registered, skipping notification!")
            return

        # Note: trigger_result uses "signals_triggered" key
        triggered_signals = pool_trigger.get("signals_triggered", [])
        for callback in self._trigger_callbacks:
            try:
                print(f"[SignalDetection] Calling callback: {callback}")
                callback(symbol, pool_trigger, market_data, triggered_signals)
                print(f"[SignalDetection] Callback completed successfully")
            except Exception as e:
                print(f"[SignalDetection] Error in callback: {e}")
                logger.error(f"Error in signal trigger callback: {e}", exc_info=True)

    def _refresh_cache_if_needed(self):
        """Refresh signal pools and signals cache if TTL expired"""
        now = time.time()
        if now - self._cache_time < self._cache_ttl:
            return

        try:
            from database.connection import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                # Load enabled signal pools
                result = db.execute(
                    text("""
                        SELECT id, pool_name, signal_ids, symbols, enabled, logic, exchange, source_type
                        FROM signal_pools
                        WHERE enabled = true
                          AND (is_deleted IS NULL OR is_deleted = false)
                          AND COALESCE(source_type, 'market_signals') = 'market_signals'
                    """)
                )
                self._signal_pools_cache = []
                for row in result.fetchall():
                    # Parse signal_ids and symbols - ORM defines as Text
                    signal_ids = row[2]
                    if isinstance(signal_ids, str):
                        try:
                            signal_ids = json.loads(signal_ids)
                        except json.JSONDecodeError:
                            signal_ids = []
                    symbols = row[3]
                    if isinstance(symbols, str):
                        try:
                            symbols = json.loads(symbols)
                        except json.JSONDecodeError:
                            symbols = []
                    self._signal_pools_cache.append({
                        "id": row[0],
                        "pool_name": row[1],
                        "signal_ids": signal_ids or [],
                        "symbols": symbols or [],
                        "enabled": row[4],
                        "logic": row[5] or "OR",
                        "exchange": row[6] or "hyperliquid",
                        "source_type": row[7] or "market_signals",
                    })

                # Load all enabled signals
                result = db.execute(
                    text("SELECT id, signal_name, description, trigger_condition, enabled, exchange FROM signal_definitions WHERE enabled = true AND (is_deleted IS NULL OR is_deleted = false)")
                )
                self._signals_cache = {}
                for row in result.fetchall():
                    # Parse trigger_condition - ORM defines as Text, so it may be string
                    trigger_cond = row[3]
                    if isinstance(trigger_cond, str):
                        try:
                            trigger_cond = json.loads(trigger_cond)
                        except json.JSONDecodeError:
                            trigger_cond = {}
                    self._signals_cache[row[0]] = {
                        "id": row[0],
                        "signal_name": row[1],
                        "description": row[2],
                        "trigger_condition": trigger_cond,
                        "enabled": row[4],
                        "exchange": row[5] or "hyperliquid"
                    }

                self._cache_time = now
                logger.debug(f"Signal cache refreshed: {len(self._signal_pools_cache)} pools, {len(self._signals_cache)} signals")

            finally:
                db.close()

        except Exception as e:
            logger.error(f"Failed to refresh signal cache: {e}")

    def _check_pool_trigger(
        self, pool: dict, symbol: str, market_data: Dict[str, Any]
    ) -> Optional[dict]:
        """
        Check if a signal pool should trigger based on its logic (AND/OR).
        Implements edge-triggered logic at pool level.
        """
        pool_id = pool["id"]
        pool_name = pool["pool_name"]
        signal_ids = pool.get("signal_ids", [])
        logic = pool.get("logic", "OR").upper()

        if not signal_ids:
            return None

        # Get or create pool state
        pool_state_key = (pool_id, symbol)
        if pool_state_key not in self.pool_states:
            self.pool_states[pool_state_key] = PoolState(pool_id=pool_id, symbol=symbol)
        pool_state = self.pool_states[pool_state_key]

        # Check each signal's condition (without triggering)
        signals_met = {}
        signal_details = {}

        for signal_id in signal_ids:
            signal_def = self._signals_cache.get(signal_id)
            if not signal_def or not signal_def.get("enabled"):
                continue

            condition_result = self._check_signal_condition(
                signal_id, signal_def, symbol, market_data
            )
            if condition_result is not None:
                signals_met[signal_id] = condition_result["condition_met"]
                signal_details[signal_id] = condition_result

        if not signals_met:
            return None

        # Determine pool condition based on logic
        if logic == "AND":
            pool_condition_met = all(signals_met.values())
        else:  # OR
            pool_condition_met = any(signals_met.values())

        # Edge detection at pool level
        was_active = pool_state.is_active
        should_trigger = pool_condition_met and not was_active

        # DEBUG LOG: Edge detection state (only when state changes or trigger happens)
        if should_trigger or (pool_condition_met != was_active):
            print(f"[EdgeDetect] pool={pool_id}:{pool_name} symbol={symbol} was_active={was_active} pool_met={pool_condition_met} trigger={should_trigger} state_id={id(pool_state)} states_count={len(self.pool_states)}", flush=True)

        # Update pool state
        pool_state.is_active = pool_condition_met
        pool_state.signal_conditions_met = signals_met
        pool_state.last_check_time = time.time()

        # Log signal conditions for debugging
        met_signals = [sid for sid, met in signals_met.items() if met]
        if met_signals:
            logger.info(
                f"[PoolCheck] {pool_name} ({logic}) on {symbol}: "
                f"signals_met={met_signals}, pool_active={pool_condition_met}, "
                f"was_active={was_active}, trigger={should_trigger}"
            )

        if should_trigger:
            # Build trigger result with all signal details
            trigger_result = {
                "pool_id": pool_id,
                "pool_name": pool_name,
                "symbol": symbol,
                "logic": logic,
                "trigger_time": time.time(),
                "signals_triggered": [
                    signal_details[sid] for sid in met_signals
                ],
                "all_signals": signal_details,
            }
            # Log to database and get trigger_log_id for tracking
            trigger_log_id = self._log_pool_trigger(trigger_result)
            trigger_result["trigger_log_id"] = trigger_log_id
            return trigger_result

        return None

    def _check_signal_condition(
        self, signal_id: int, signal_def: dict, symbol: str, market_data: Dict[str, Any]
    ) -> Optional[dict]:
        """
        Check if a signal's condition is met (without edge detection).
        Returns condition details including whether it's met.
        """
        condition = signal_def.get("trigger_condition", {})
        metric = condition.get("metric")
        time_window = condition.get("time_window", "5m")
        exchange = signal_def.get("exchange", "hyperliquid")

        if not metric:
            return None

        # Handle taker_volume composite signal
        if metric == "taker_volume":
            return self._check_taker_condition(signal_id, signal_def, symbol, condition, time_window, exchange)

        # Handle MACD event-based signal
        if metric == "macd":
            return self._check_macd_condition(signal_id, signal_def, symbol, condition, time_window, exchange)

        # Standard single-value signal
        operator = condition.get("operator")
        threshold = condition.get("threshold")

        if not all([operator, threshold is not None]):
            return None

        current_value = self._get_metric_value(metric, symbol, market_data, time_window, exchange)
        if current_value is None:
            return None

        condition_met = self._evaluate_condition(current_value, operator, threshold)

        # DEBUG LOG: Standard signal condition check (all exchanges, all metrics)
        print(f"[SignalCheck] signal={signal_id} symbol={symbol} metric={metric} exchange={exchange} period={time_window} value={current_value:.6f} op={operator} thresh={threshold} met={condition_met}", flush=True)

        result = {
            "signal_id": signal_id,
            "signal_name": signal_def.get("signal_name"),
            "description": signal_def.get("description"),
            "metric": metric,
            "operator": operator,
            "threshold": threshold,
            "current_value": current_value,
            "condition_met": condition_met,
            "time_window": time_window,
            "exchange": exchange,
        }

        # Enrich factor metrics with effectiveness data (IC/ICIR/win_rate/decay)
        if metric.startswith("factor:") and condition_met:
            self._enrich_factor_effectiveness(result, metric, symbol, exchange)

        return result

    def _check_signal_trigger(
        self, signal_id: int, signal_def: dict, symbol: str, market_data: Dict[str, Any]
    ) -> Optional[dict]:
        """
        Check if a signal should trigger based on current market data.
        Implements edge-triggered logic.
        """
        condition = signal_def.get("trigger_condition", {})
        metric = condition.get("metric")
        time_window = condition.get("time_window", "5m")
        exchange = signal_def.get("exchange", "hyperliquid")

        if not metric:
            return None

        # Handle taker_volume composite signal
        if metric == "taker_volume":
            return self._check_taker_volume_trigger(
                signal_id, signal_def, symbol, condition, time_window, exchange
            )

        # Standard single-value signal
        operator = condition.get("operator")
        threshold = condition.get("threshold")

        if not all([operator, threshold is not None]):
            return None

        # Get current metric value
        current_value = self._get_metric_value(metric, symbol, market_data, time_window, exchange)
        if current_value is None:
            return None

        # Check condition
        condition_met = self._evaluate_condition(current_value, operator, threshold)

        # Get or create signal state
        state_key = (signal_id, symbol)
        if state_key not in self.signal_states:
            self.signal_states[state_key] = SignalState(
                signal_id=signal_id, symbol=symbol
            )
        state = self.signal_states[state_key]

        # Edge detection: only trigger when condition changes from False to True
        was_active = state.is_active
        should_trigger = condition_met and not was_active

        # Update state
        state.is_active = condition_met
        state.last_value = current_value
        state.last_check_time = time.time()

        # Debug logging for edge detection (using INFO level for visibility)
        if condition_met:
            logger.info(
                f"[EdgeTrigger] {signal_def.get('signal_name')} on {symbol}: "
                f"value={current_value:.4f}, threshold={threshold}, "
                f"was_active={was_active}, is_active={condition_met}, trigger={should_trigger}"
            )

        if should_trigger:
            trigger_result = {
                "signal_id": signal_id,
                "signal_name": signal_def.get("signal_name"),
                "symbol": symbol,
                "trigger_value": current_value,
                "threshold": threshold,
                "operator": operator,
                "metric": metric,
                "trigger_time": time.time(),
                "description": signal_def.get("description"),
            }

            # For funding metric, add current rate context
            if metric in ("funding", "funding_rate"):
                current_rate = self._get_funding_current_rate(symbol, time_window)
                if current_rate is not None:
                    trigger_result["current_rate"] = current_rate

            self._log_trigger(trigger_result)
            return trigger_result

        return None

    def _get_metric_value(
        self, metric: str, symbol: str, market_data: Dict[str, Any], time_window: int,
        exchange: str = "hyperliquid"
    ) -> Optional[float]:
        """
        Get the current value of a metric from market data or indicators.

        Uses get_indicator_value() from market_flow_indicators for DB-based metrics.
        This ensures proper separation of concerns - signal detection doesn't depend
        on prompt-specific data structures.
        """
        try:
            # taker_volume is handled in _check_signal_trigger directly
            # All other metrics use DB query via market_flow_indicators
            from database.connection import SessionLocal
            from services.market_flow_indicators import get_indicator_value

            # Convert time_window to period string
            period = self._time_window_to_period(time_window)

            # Map old metric names to new names (backward compatibility)
            metric_name_map = {
                "oi_delta_percent": "oi_delta",
                "funding_rate": "funding",
                "taker_buy_ratio": "taker_ratio",
            }
            # Normalize metric name
            metric = metric_name_map.get(metric, metric)

            # Map signal metric names to indicator types (aligned with K-line)
            indicator_map = {
                "oi_delta": "OI_DELTA",
                "cvd": "CVD",
                "depth_ratio": "DEPTH",
                "order_imbalance": "IMBALANCE",
                "taker_ratio": "TAKER",
                "funding": "FUNDING",
                "oi": "OI",
                "price_change": "PRICE_CHANGE",
                "volatility": "VOLATILITY",
            }

            # Factor metric: factor:<factor_name>
            if metric.startswith("factor:"):
                return self._get_factor_metric_value(metric, symbol, period, exchange)

            if metric not in indicator_map:
                logger.warning(f"Unknown metric: {metric}")
                return None

            indicator_type = indicator_map[metric]

            db = SessionLocal()
            try:
                from datetime import datetime
                current_time_ms = int(datetime.utcnow().timestamp() * 1000)
                value = get_indicator_value(db, symbol, indicator_type, period, current_time_ms=current_time_ms, exchange=exchange)
                # DEBUG LOG: Record the exact timestamp used for indicator calculation
                print(f"[MetricValue] symbol={symbol} metric={metric} exchange={exchange} period={period} ts={current_time_ms} value={value}", flush=True)
                return value
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error getting metric {metric} for {symbol}: {e}")
            return None

    def _get_factor_metric_value(
        self, metric: str, symbol: str, period: str, exchange: str
    ) -> Optional[float]:
        """
        Get factor value from K-line data using expression engine.

        Factor metrics use format: factor:<factor_name>
        Loads latest closed K-lines, runs expression engine, returns last value.
        """
        factor_name = metric.split(":", 1)[1]
        try:
            from database.connection import SessionLocal
            from services.market_data import get_kline_data
            from services.factor_resolver import compute_factor_series

            db = SessionLocal()
            try:
                market = "binance" if exchange == "binance" else "CRYPTO"
                klines = get_kline_data(symbol, market=market, period=period, count=300)
                if not klines or len(klines) < 30:
                    logger.warning(f"Insufficient K-line data for factor {factor_name}: {symbol}")
                    return None

                series, _, err = compute_factor_series(
                    db=db,
                    factor_name=factor_name,
                    symbol=symbol,
                    period=period,
                    exchange=exchange,
                    klines=klines,
                )
                if series is None or len(series) == 0:
                    logger.warning(f"Factor {factor_name} execution failed: {err}")
                    return None

                import pandas as pd
                last_val = series.iloc[-1]
                if pd.isna(last_val):
                    return None

                value = float(last_val)
                print(f"[FactorMetric] symbol={symbol} factor={factor_name} exchange={exchange} "
                      f"period={period} value={value:.6f}", flush=True)
                return value
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error computing factor metric {factor_name} for {symbol}: {e}")
            return None

    def _enrich_factor_effectiveness(
        self, result: dict, metric: str, symbol: str, exchange: str
    ) -> None:
        """Enrich a factor signal result with effectiveness data from DB."""
        factor_name = metric.split(":", 1)[1]
        try:
            from database.connection import SessionLocal
            from sqlalchemy import text as sa_text

            db = SessionLocal()
            try:
                row = db.execute(sa_text(
                    "SELECT ic_mean, icir, win_rate, decay_half_life "
                    "FROM factor_effectiveness "
                    "WHERE factor_name = :fn AND symbol = :sym AND exchange = :ex "
                    "AND period = '1h' AND forward_period = '4h' "
                    "ORDER BY created_at DESC LIMIT 1"
                ), {"fn": factor_name, "sym": symbol, "ex": exchange}).fetchone()

                if row:
                    result["factor_effectiveness"] = {
                        "ic": round(float(row[0]), 4) if row[0] is not None else None,
                        "icir": round(float(row[1]), 2) if row[1] is not None else None,
                        "win_rate": round(float(row[2]), 2) if row[2] is not None else None,
                        "decay_half_life_hours": int(row[3]) if row[3] is not None else None,
                    }
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error enriching factor effectiveness for {factor_name}/{symbol}: {e}")

    def _get_funding_current_rate(self, symbol: str, time_window, exchange: str = "hyperliquid") -> Optional[float]:
        """Get current funding rate in bps for context."""
        try:
            from database.connection import SessionLocal
            from services.market_flow_indicators import _get_funding_data, TIMEFRAME_MS
            from datetime import datetime

            period = time_window if isinstance(time_window, str) else self._time_window_to_period(time_window)
            if period not in TIMEFRAME_MS:
                return None

            interval_ms = TIMEFRAME_MS[period]
            current_time_ms = int(datetime.utcnow().timestamp() * 1000)

            db = SessionLocal()
            try:
                data = _get_funding_data(db, symbol, period, interval_ms, current_time_ms, exchange)
                return data.get("current") if data else None
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error getting funding current rate for {symbol}: {e}")
            return None

    def _check_taker_condition(
        self, signal_id: int, signal_def: dict, symbol: str, condition: dict, time_window: str,
        exchange: str = "hyperliquid"
    ) -> Optional[dict]:
        """Check taker_volume condition (without edge detection)."""
        direction = condition.get("direction", "any")
        ratio_threshold = condition.get("ratio_threshold", 1.5)
        volume_threshold = condition.get("volume_threshold", 0)

        from database.connection import SessionLocal
        from services.market_flow_indicators import _get_taker_data, TIMEFRAME_MS
        from datetime import datetime

        period = time_window if isinstance(time_window, str) else self._time_window_to_period(time_window)
        if period not in TIMEFRAME_MS:
            return None

        interval_ms = TIMEFRAME_MS[period]
        current_time_ms = int(datetime.utcnow().timestamp() * 1000)

        # For Binance, include debug snapshot for troubleshooting
        include_debug = (exchange == "binance")

        db = SessionLocal()
        try:
            taker_data = _get_taker_data(db, symbol, period, interval_ms, current_time_ms, exchange, include_debug)
        finally:
            db.close()

        if not taker_data:
            return None

        buy = taker_data.get("buy", 0)
        sell = taker_data.get("sell", 0)
        total = buy + sell

        condition_met = False
        actual_direction = None
        actual_ratio = None

        if total >= volume_threshold and sell > 0:
            actual_ratio = buy / sell
            if direction == "buy" and actual_ratio >= ratio_threshold:
                condition_met = True
                actual_direction = "buy"
            elif direction == "sell" and actual_ratio <= 1 / ratio_threshold:
                condition_met = True
                actual_direction = "sell"
            elif direction == "any":
                if actual_ratio >= ratio_threshold:
                    condition_met = True
                    actual_direction = "buy"
                elif actual_ratio <= 1 / ratio_threshold:
                    condition_met = True
                    actual_direction = "sell"

        # DEBUG LOG: Taker volume condition check (all exchanges)
        ratio_str = f"{actual_ratio:.4f}" if actual_ratio else "N/A"
        print(f"[TakerCheck] signal={signal_id} symbol={symbol} exchange={exchange} period={period} ts={current_time_ms} buy={buy:.2f} sell={sell:.2f} ratio={ratio_str} met={condition_met}", flush=True)

        result = {
            "signal_id": signal_id,
            "signal_name": signal_def.get("signal_name"),
            "metric": "taker_volume",
            "condition_met": condition_met,
            "direction": actual_direction or direction,
            "buy": buy,
            "sell": sell,
            "total": total,
            "ratio": actual_ratio,
            "ratio_threshold": ratio_threshold,
            "volume_threshold": volume_threshold,
            "time_window": time_window,
            "exchange": exchange,
        }

        # Include debug snapshot for Binance
        if include_debug and "debug_snapshot" in taker_data:
            result["debug_snapshot"] = taker_data["debug_snapshot"]

        return result

    def _check_macd_condition(
        self, signal_id: int, signal_def: dict, symbol: str, condition: dict, time_window: str,
        exchange: str = "hyperliquid"
    ) -> Optional[dict]:
        """
        Check MACD event-based condition (without edge detection).
        Supports multiple event types: golden_cross, death_cross, histogram_positive,
        histogram_negative, macd_above_zero, macd_below_zero.
        """
        from database.connection import SessionLocal
        from services.technical_indicators import get_macd_for_signal
        from datetime import datetime

        event_types = condition.get("event_types", [])
        if not event_types:
            return None

        period = time_window if isinstance(time_window, str) else self._time_window_to_period(time_window)
        current_time_ms = int(datetime.utcnow().timestamp() * 1000)

        db = SessionLocal()
        try:
            macd_data = get_macd_for_signal(db, symbol, period, current_time_ms, exchange)
        finally:
            db.close()

        if not macd_data:
            return None

        curr = macd_data['current']
        prev = macd_data['previous']

        # Check each event type
        triggered_event = None
        condition_met = False

        for event_type in event_types:
            if event_type == "golden_cross":
                # Golden cross: histogram crosses from negative to positive
                if prev['histogram'] <= 0 and curr['histogram'] > 0:
                    triggered_event = "golden_cross"
                    condition_met = True
                    break
            elif event_type == "death_cross":
                # Death cross: histogram crosses from positive to negative
                if prev['histogram'] >= 0 and curr['histogram'] < 0:
                    triggered_event = "death_cross"
                    condition_met = True
                    break
            elif event_type == "histogram_positive":
                # Histogram turns positive (same as golden cross)
                if prev['histogram'] <= 0 and curr['histogram'] > 0:
                    triggered_event = "histogram_positive"
                    condition_met = True
                    break
            elif event_type == "histogram_negative":
                # Histogram turns negative (same as death cross)
                if prev['histogram'] >= 0 and curr['histogram'] < 0:
                    triggered_event = "histogram_negative"
                    condition_met = True
                    break
            elif event_type == "macd_above_zero":
                # MACD line crosses above zero
                if prev['macd'] <= 0 and curr['macd'] > 0:
                    triggered_event = "macd_above_zero"
                    condition_met = True
                    break
            elif event_type == "macd_below_zero":
                # MACD line crosses below zero
                if prev['macd'] >= 0 and curr['macd'] < 0:
                    triggered_event = "macd_below_zero"
                    condition_met = True
                    break

        # DEBUG LOG
        print(f"[MACDCheck] signal={signal_id} symbol={symbol} exchange={exchange} period={period} "
              f"events={event_types} curr_hist={curr['histogram']:.6f} prev_hist={prev['histogram']:.6f} "
              f"triggered={triggered_event} met={condition_met}", flush=True)

        return {
            "signal_id": signal_id,
            "signal_name": signal_def.get("signal_name"),
            "description": signal_def.get("description"),
            "metric": "macd",
            "event_types": event_types,
            "triggered_event": triggered_event,
            "condition_met": condition_met,
            "time_window": time_window,
            "exchange": exchange,
            "values": {
                "macd": curr['macd'],
                "signal": curr['signal'],
                "histogram": curr['histogram'],
                "prev_macd": prev['macd'],
                "prev_signal": prev['signal'],
                "prev_histogram": prev['histogram'],
            },
            "cross_strength": macd_data['cross_strength'],
            "latest_kline_ts": macd_data['latest_kline_ts'],
        }

    def _log_pool_trigger(self, trigger_result: dict) -> int | None:
        """Log pool trigger to database and return the trigger_log_id."""
        try:
            import json
            from collections import Counter
            from database.connection import SessionLocal
            from sqlalchemy import text

            # Timeframe order for tie-breaking (smaller = more granular = preferred)
            TIMEFRAME_ORDER = {"1m": 1, "5m": 2, "15m": 3, "30m": 4, "1h": 5, "4h": 6, "1d": 7}

            db = SessionLocal()
            try:
                def _format_signal_for_log(s: dict) -> dict:
                    """Format signal data for logging, handling standard, taker_volume, and macd signals."""
                    base = {
                        "signal_id": s["signal_id"],
                        "signal_name": s["signal_name"],
                        "metric": s["metric"],
                    }
                    if s["metric"] == "taker_volume":
                        # taker_volume uses different field names
                        base["current_value"] = s.get("ratio")  # Use ratio as display value
                        base["threshold"] = s.get("ratio_threshold")
                        base["direction"] = s.get("actual_direction")
                        base["volume"] = s.get("total")
                        base["volume_threshold"] = s.get("volume_threshold")
                    elif s["metric"] == "macd":
                        # MACD event-based signal
                        base["event_types"] = s.get("event_types")
                        base["triggered_event"] = s.get("triggered_event")
                        base["values"] = s.get("values")
                        base["cross_strength"] = s.get("cross_strength")
                    else:
                        base["current_value"] = s.get("current_value")
                        base["threshold"] = s.get("threshold")
                        base["operator"] = s.get("operator")
                    # Persist factor effectiveness if present
                    if "factor_effectiveness" in s:
                        base["factor_effectiveness"] = s["factor_effectiveness"]
                    return base

                # Build trigger value data
                trigger_value_data = {
                    "logic": trigger_result["logic"],
                    "signals_triggered": [
                        _format_signal_for_log(s)
                        for s in trigger_result["signals_triggered"]
                    ],
                }

                # Add debug snapshots for Binance triggers (for troubleshooting)
                debug_snapshots = []
                for s in trigger_result["signals_triggered"]:
                    if "debug_snapshot" in s:
                        debug_snapshots.append({
                            "signal_id": s["signal_id"],
                            "signal_name": s["signal_name"],
                            "snapshot": s["debug_snapshot"]
                        })
                if debug_snapshots:
                    trigger_value_data["debug_snapshots"] = debug_snapshots

                trigger_value_json = json.dumps(trigger_value_data)

                # Determine the most common time_window from triggered signals
                time_windows = [
                    s.get("time_window", "5m")
                    for s in trigger_result["signals_triggered"]
                ]
                if time_windows:
                    # Count occurrences of each time_window
                    tw_counts = Counter(time_windows)
                    max_count = max(tw_counts.values())
                    # Get all time_windows with max count (handle ties)
                    candidates = [tw for tw, count in tw_counts.items() if count == max_count]
                    # On tie, prefer smaller (more granular) timeframe
                    trigger_timeframe = min(candidates, key=lambda x: TIMEFRAME_ORDER.get(x, 99))
                else:
                    logger.warning("No time_window found in triggered signals, using default 5m")
                    trigger_timeframe = "5m"

                # Get market regime for this trigger using the determined timeframe
                market_regime = _get_market_regime_for_trigger(
                    trigger_result["symbol"], trigger_timeframe
                )

                # Insert and return the new trigger_log_id
                result = db.execute(
                    text("""
                        INSERT INTO signal_trigger_logs
                        (pool_id, symbol, trigger_value, triggered_at, market_regime)
                        VALUES (:pool_id, :symbol, CAST(:trigger_value AS jsonb), NOW(), :market_regime)
                        RETURNING id
                    """),
                    {
                        "pool_id": trigger_result["pool_id"],
                        "symbol": trigger_result["symbol"],
                        "trigger_value": trigger_value_json,
                        "market_regime": market_regime,
                    }
                )
                trigger_log_id = result.scalar()
                db.commit()

                signals_info = ", ".join([
                    s["signal_name"] for s in trigger_result["signals_triggered"]
                ])
                logger.info(
                    f"Pool triggered: {trigger_result['pool_name']} ({trigger_result['logic']}) "
                    f"on {trigger_result['symbol']} - signals: [{signals_info}] "
                    f"(trigger_log_id={trigger_log_id}, regime_tf={trigger_timeframe})"
                )
                return trigger_log_id
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Failed to log pool trigger: {e}")
            return None

    def _time_window_to_period(self, time_window: int) -> str:
        """Convert time window (seconds or string) to period string"""
        if isinstance(time_window, str):
            return time_window
        # time_window in seconds
        if time_window <= 60:
            return "1m"
        elif time_window <= 180:
            return "3m"
        elif time_window <= 300:
            return "5m"
        elif time_window <= 900:
            return "15m"
        elif time_window <= 1800:
            return "30m"
        elif time_window <= 3600:
            return "1h"
        elif time_window <= 7200:
            return "2h"
        else:
            return "4h"

    def _check_taker_volume_trigger(
        self, signal_id: int, signal_def: dict, symbol: str, condition: dict, time_window: str,
        exchange: str = "hyperliquid"
    ) -> Optional[dict]:
        """
        Check taker_volume composite signal trigger.
        Uses log(buy/sell) for symmetric ratio detection.
        Condition format:
        {
            "metric": "taker_volume",
            "direction": "buy" | "sell" | "any",
            "ratio_threshold": 1.5,  # User sets multiplier, internally converted to log
            "volume_threshold": 50000,
            "time_window": "5m"
        }
        """
        import math
        direction = condition.get("direction", "any")
        ratio_threshold = condition.get("ratio_threshold", 1.5)
        volume_threshold = condition.get("volume_threshold", 0)

        # Convert user's ratio threshold to log threshold
        # e.g., 1.5 -> log(1.5) = 0.405, so we check if |log_ratio| >= 0.405
        log_threshold = math.log(max(ratio_threshold, 1.01))  # Prevent log(1) = 0

        # Get taker data from DB
        from database.connection import SessionLocal
        from services.market_flow_indicators import _get_taker_data, TIMEFRAME_MS
        from datetime import datetime

        period = time_window if isinstance(time_window, str) else self._time_window_to_period(time_window)
        if period not in TIMEFRAME_MS:
            return None

        interval_ms = TIMEFRAME_MS[period]
        current_time_ms = int(datetime.utcnow().timestamp() * 1000)

        db = SessionLocal()
        try:
            taker_data = _get_taker_data(db, symbol, period, interval_ms, current_time_ms, exchange)
        finally:
            db.close()

        if not taker_data:
            return None

        buy = taker_data.get("buy", 0)
        sell = taker_data.get("sell", 0)
        total = buy + sell

        # Check volume threshold
        if total < volume_threshold:
            return None

        # Check direction and log ratio
        # Log ratio = ln(buy/sell), symmetric around 0
        # >0 means buyers dominate, <0 means sellers dominate
        condition_met = False
        actual_direction = None
        log_ratio = None

        if buy > 0 and sell > 0:
            log_ratio = math.log(buy / sell)

            if direction == "buy":
                # Buy dominant: log_ratio >= log_threshold
                if log_ratio >= log_threshold:
                    condition_met = True
                    actual_direction = "buy"
            elif direction == "sell":
                # Sell dominant: log_ratio <= -log_threshold (symmetric)
                if log_ratio <= -log_threshold:
                    condition_met = True
                    actual_direction = "sell"
            elif direction == "any":
                # Either direction dominant (using abs for symmetry)
                if log_ratio >= log_threshold:
                    condition_met = True
                    actual_direction = "buy"
                elif log_ratio <= -log_threshold:
                    condition_met = True
                    actual_direction = "sell"

        # Edge detection
        state_key = (signal_id, symbol)
        if state_key not in self.signal_states:
            self.signal_states[state_key] = SignalState(signal_id=signal_id, symbol=symbol)
        state = self.signal_states[state_key]

        should_trigger = condition_met and not state.is_active

        state.is_active = condition_met
        state.last_value = log_ratio
        state.last_check_time = time.time()

        if should_trigger and actual_direction and log_ratio is not None:
            trigger_result = {
                "signal_id": signal_id,
                "signal_name": signal_def.get("signal_name"),
                "symbol": symbol,
                "metric": "taker_volume",
                "trigger_time": time.time(),
                "description": signal_def.get("description"),
                # Taker volume specific fields
                "actual_direction": actual_direction,
                "buy": buy,
                "sell": sell,
                "total": total,
                "log_ratio": log_ratio,  # Log transformed ratio
                "ratio": buy / sell,  # Original ratio for display
                "ratio_threshold": ratio_threshold,
                "volume_threshold": volume_threshold,
            }
            self._log_taker_volume_trigger(trigger_result)
            return trigger_result

        return None

    def _log_taker_volume_trigger(self, trigger_result: dict):
        """Log taker_volume signal trigger to database"""
        try:
            import json
            from database.connection import SessionLocal
            from sqlalchemy import text

            db = SessionLocal()
            try:
                trigger_value_json = json.dumps({
                    "direction": trigger_result["actual_direction"],
                    "buy": trigger_result["buy"],
                    "sell": trigger_result["sell"],
                    "total": trigger_result["total"],
                    "ratio": trigger_result["ratio"],
                    "ratio_threshold": trigger_result["ratio_threshold"],
                    "volume_threshold": trigger_result["volume_threshold"],
                })

                # Get market regime for this trigger
                market_regime = _get_market_regime_for_trigger(trigger_result["symbol"])

                db.execute(
                    text("""
                        INSERT INTO signal_trigger_logs
                        (signal_id, symbol, trigger_value, triggered_at, market_regime)
                        VALUES (:signal_id, :symbol, CAST(:trigger_value AS jsonb), NOW(), :market_regime)
                    """),
                    {
                        "signal_id": trigger_result["signal_id"],
                        "symbol": trigger_result["symbol"],
                        "trigger_value": trigger_value_json,
                        "market_regime": market_regime,
                    }
                )
                db.commit()
                logger.info(
                    f"Taker volume signal triggered: {trigger_result['signal_name']} on {trigger_result['symbol']} "
                    f"(direction={trigger_result['actual_direction']}, ratio={trigger_result['ratio']:.2f}, "
                    f"buy={trigger_result['buy']:.0f}, sell={trigger_result['sell']:.0f})"
                )
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Failed to log taker volume trigger: {e}")

    def _evaluate_condition(self, value: float, operator: str, threshold: float) -> bool:
        """Evaluate if a condition is met.

        Supports both symbol and text forms of operators for compatibility
        with AI-generated signal configs.
        """
        # Support both symbol and text forms of operators
        if operator in (">", "greater_than", "gt"):
            return value > threshold
        elif operator in (">=", "greater_than_or_equal", "gte"):
            return value >= threshold
        elif operator in ("<", "less_than", "lt"):
            return value < threshold
        elif operator in ("<=", "less_than_or_equal", "lte"):
            return value <= threshold
        elif operator in ("==", "equal", "eq"):
            return abs(value - threshold) < 1e-9
        elif operator in ("!=", "not_equal", "ne"):
            return abs(value - threshold) >= 1e-9
        elif operator in ("abs_greater_than", "abs_gt"):
            return abs(value) > threshold
        elif operator in ("abs_less_than", "abs_lt"):
            return abs(value) < threshold
        else:
            logger.warning(f"Unknown operator: {operator}")
            return False

    def _log_trigger(self, trigger_result: dict):
        """Log signal trigger to database"""
        try:
            import json
            from database.connection import SessionLocal
            from sqlalchemy import text

            db = SessionLocal()
            try:
                # Store trigger details as JSONB
                trigger_value_json = json.dumps({
                    "value": trigger_result["trigger_value"],
                    "threshold": trigger_result["threshold"],
                    "operator": trigger_result["operator"],
                    "metric": trigger_result["metric"],
                })

                # Get market regime for this trigger
                market_regime = _get_market_regime_for_trigger(trigger_result["symbol"])

                db.execute(
                    text("""
                        INSERT INTO signal_trigger_logs
                        (signal_id, symbol, trigger_value, triggered_at, market_regime)
                        VALUES (:signal_id, :symbol, CAST(:trigger_value AS jsonb), NOW(), :market_regime)
                    """),
                    {
                        "signal_id": trigger_result["signal_id"],
                        "symbol": trigger_result["symbol"],
                        "trigger_value": trigger_value_json,
                        "market_regime": market_regime,
                    }
                )
                db.commit()
                logger.info(
                    f"Signal triggered: {trigger_result['signal_name']} on {trigger_result['symbol']} "
                    f"(value={trigger_result['trigger_value']:.4f}, threshold={trigger_result['threshold']})"
                )
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Failed to log signal trigger: {e}")

    def get_signal_states(self) -> Dict[str, Any]:
        """Get current signal states for debugging/monitoring"""
        return {
            "signal_states": {
                f"{state.signal_id}:{state.symbol}": {
                    "is_active": state.is_active,
                    "last_value": state.last_value,
                    "last_check_time": state.last_check_time,
                }
                for state_key, state in self.signal_states.items()
            },
            "pool_states": {
                f"{state.pool_id}:{state.symbol}": {
                    "is_active": state.is_active,
                    "signal_conditions_met": state.signal_conditions_met,
                    "last_check_time": state.last_check_time,
                }
                for state_key, state in self.pool_states.items()
            }
        }

    def reset_state(self, signal_id: int = None, pool_id: int = None, symbol: str = None):
        """Reset signal and pool states (useful for testing)"""
        if signal_id is None and pool_id is None and symbol is None:
            self.signal_states.clear()
            self.pool_states.clear()
        else:
            # Reset signal states
            if signal_id is not None or symbol is not None:
                keys_to_remove = [
                    k for k in self.signal_states.keys()
                    if (signal_id is None or k[0] == signal_id) and
                       (symbol is None or k[1] == symbol)
                ]
                for k in keys_to_remove:
                    del self.signal_states[k]

            # Reset pool states
            if pool_id is not None or symbol is not None:
                keys_to_remove = [
                    k for k in self.pool_states.keys()
                    if (pool_id is None or k[0] == pool_id) and
                       (symbol is None or k[1] == symbol)
                ]
                for k in keys_to_remove:
                    del self.pool_states[k]


# Singleton instance
signal_detection_service = SignalDetectionService()
