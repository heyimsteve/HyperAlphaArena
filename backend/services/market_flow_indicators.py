#!/usr/bin/env python3
"""
Market Flow Indicators Service for AI Prompt Variables

Provides aggregated market flow data formatted for AI prompt injection.
Unlike the chart API which returns time series, this returns:
- Current value
- Last N period values
- Relevant context (e.g., averages for comparison)

Supported variables:
- {SYMBOL}_CVD_{PERIOD} - Cumulative Volume Delta
- {SYMBOL}_TAKER_{PERIOD} - Taker Buy/Sell Volume and Ratio
- {SYMBOL}_OI_{PERIOD} - Open Interest
- {SYMBOL}_FUNDING_{PERIOD} - Funding Rate
- {SYMBOL}_DEPTH_{PERIOD} - Order Book Depth Ratio
"""

import logging
import threading
import time
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from database.models import (
    MarketTradesAggregated,
    MarketOrderbookSnapshots,
    MarketAssetMetrics
)

logger = logging.getLogger(__name__)
INSUFFICIENT_DATA_WARNING_COOLDOWN_SECONDS = 600
_insufficient_data_lock = threading.Lock()
_insufficient_data_warning_at: dict[str, float] = {}


def _log_insufficient_data_once(key: str, message: str) -> None:
    now = time.time()
    with _insufficient_data_lock:
        last_warning_at = _insufficient_data_warning_at.get(key, 0.0)
        if now - last_warning_at < INSUFFICIENT_DATA_WARNING_COOLDOWN_SECONDS:
            logger.debug(message)
            return
        _insufficient_data_warning_at[key] = now
    logger.warning(message)

# Timeframe to milliseconds mapping
TIMEFRAME_MS = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


def floor_timestamp(ts_ms: int, interval_ms: int) -> int:
    """Floor timestamp to interval boundary"""
    return (ts_ms // interval_ms) * interval_ms


def decimal_to_float(val) -> Optional[float]:
    """Convert Decimal to float, handling None"""
    if val is None:
        return None
    return float(val)


def format_volume(value: float) -> str:
    """Format volume with appropriate unit (K, M, B)"""
    abs_val = abs(value)
    sign = "+" if value >= 0 else "-"
    if abs_val >= 1_000_000_000:
        return f"{sign}${abs_val/1_000_000_000:.2f}B"
    elif abs_val >= 1_000_000:
        return f"{sign}${abs_val/1_000_000:.2f}M"
    elif abs_val >= 1_000:
        return f"{sign}${abs_val/1_000:.2f}K"
    else:
        return f"{sign}${abs_val:.2f}"


def get_indicator_value(
    db: Session,
    symbol: str,
    indicator: str,
    period: str,
    current_time_ms: Optional[int] = None,
    exchange: str = "hyperliquid"
) -> Optional[float]:
    """
    Get a single indicator's current value for signal detection.

    This is the canonical function for retrieving indicator values.
    Use this for signal detection, alerts, and any feature that needs
    a single numeric value.

    Args:
        db: Database session
        symbol: Trading symbol (e.g., "BTC")
        indicator: Indicator type - one of:
            - "OI_DELTA": Open Interest change percentage
            - "CVD": Cumulative Volume Delta
            - "DEPTH": Order book depth ratio (bid/ask)
            - "IMBALANCE": Order book imbalance (-1 to 1)
            - "TAKER": Taker buy/sell ratio
        period: Time period (e.g., "1m", "5m", "15m", "1h")
        current_time_ms: Current timestamp in ms (defaults to now)
        exchange: Exchange name (e.g., "hyperliquid", "binance")

    Returns:
        Current value as float, or None if data unavailable
    """
    if period not in TIMEFRAME_MS:
        logger.warning(f"Unsupported period: {period}")
        return None

    interval_ms = TIMEFRAME_MS[period]

    if current_time_ms is None:
        from datetime import datetime
        current_time_ms = int(datetime.utcnow().timestamp() * 1000)

    indicator_upper = indicator.upper()

    try:
        if indicator_upper == "OI_DELTA":
            data = _get_oi_delta_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("current") if data else None
        elif indicator_upper == "CVD":
            data = _get_cvd_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("current") if data else None
        elif indicator_upper == "DEPTH":
            data = _get_depth_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("ratio") if data else None
        elif indicator_upper == "IMBALANCE":
            data = _get_imbalance_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("current") if data else None
        elif indicator_upper == "TAKER":
            data = _get_taker_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("ratio") if data else None
        elif indicator_upper == "OI":
            data = _get_oi_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("current") if data else None
        elif indicator_upper == "FUNDING":
            data = _get_funding_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("change") if data else None  # Return change in bps
        elif indicator_upper == "PRICE_CHANGE":
            data = _get_price_change_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("current") if data else None
        elif indicator_upper == "VOLATILITY":
            data = _get_volatility_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            return data.get("current") if data else None
        else:
            logger.warning(f"Unknown indicator: {indicator}")
            return None
    except Exception as e:
        logger.error(f"Error getting indicator {indicator} for {symbol}: {e}")
        return None


def get_flow_indicators_for_prompt(
    db: Session,
    symbol: str,
    period: str,
    indicators: List[str],
    current_time_ms: Optional[int] = None,
    exchange: str = "hyperliquid"
) -> Dict[str, Any]:
    """
    Get market flow indicator data formatted for AI prompt injection.

    Args:
        db: Database session
        symbol: Trading symbol (e.g., "BTC")
        period: Time period (e.g., "15m", "1h")
        indicators: List of indicators to calculate ["CVD", "TAKER", "OI", "FUNDING", "DEPTH"]
        current_time_ms: Current timestamp in ms (defaults to now)
        exchange: Exchange name (e.g., "hyperliquid", "binance")

    Returns:
        Dict with indicator name as key and raw data dict as value
    """
    if period not in TIMEFRAME_MS:
        logger.warning(f"Unsupported period: {period}")
        return {}

    interval_ms = TIMEFRAME_MS[period]

    if current_time_ms is None:
        from datetime import datetime
        current_time_ms = int(datetime.utcnow().timestamp() * 1000)

    results = {}

    for indicator in indicators:
        indicator_upper = indicator.upper()
        try:
            if indicator_upper == "CVD":
                results["CVD"] = _get_cvd_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            elif indicator_upper == "TAKER":
                results["TAKER"] = _get_taker_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            elif indicator_upper == "OI":
                results["OI"] = _get_oi_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            elif indicator_upper == "OI_DELTA":
                results["OI_DELTA"] = _get_oi_delta_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            elif indicator_upper == "FUNDING":
                results["FUNDING"] = _get_funding_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            elif indicator_upper == "DEPTH":
                results["DEPTH"] = _get_depth_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            elif indicator_upper == "IMBALANCE":
                results["IMBALANCE"] = _get_imbalance_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            elif indicator_upper == "PRICE_CHANGE":
                results["PRICE_CHANGE"] = _get_price_change_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            elif indicator_upper == "VOLATILITY":
                results["VOLATILITY"] = _get_volatility_data(db, symbol, period, interval_ms, current_time_ms, exchange)
            else:
                logger.warning(f"Unknown flow indicator: {indicator}")
        except Exception as e:
            logger.error(f"Error calculating flow indicator {indicator}: {e}")
            results[indicator_upper] = None

    return results


def _get_cvd_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid"
) -> Optional[Dict[str, Any]]:
    """
    Get CVD (Cumulative Volume Delta) data.

    CVD = Cumulative(Taker Buy Notional - Taker Sell Notional)
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketTradesAggregated.timestamp,
        MarketTradesAggregated.taker_buy_notional,
        MarketTradesAggregated.taker_sell_notional
    ).filter(
        MarketTradesAggregated.symbol == symbol.upper(),
        MarketTradesAggregated.exchange == exchange.lower(),
        MarketTradesAggregated.timestamp >= start_time,
        MarketTradesAggregated.timestamp <= current_time_ms
    ).order_by(MarketTradesAggregated.timestamp).all()

    if not records:
        from datetime import datetime
        logger.warning(
            f"CVD insufficient data: symbol={symbol}, period={period}, "
            f"query_range=[{datetime.utcfromtimestamp(start_time/1000)} - "
            f"{datetime.utcfromtimestamp(current_time_ms/1000)}], records_found=0"
        )
        return None

    # Aggregate by period
    buckets = {}
    for ts, buy_notional, sell_notional in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"buy": Decimal("0"), "sell": Decimal("0")}
        buckets[bucket_ts]["buy"] += buy_notional or Decimal("0")
        buckets[bucket_ts]["sell"] += sell_notional or Decimal("0")

    # Calculate CVD for each period
    sorted_times = sorted(buckets.keys())
    period_deltas = []

    for ts in sorted_times:
        bucket = buckets[ts]
        delta = float(bucket["buy"] - bucket["sell"])
        period_deltas.append(delta)

    if not period_deltas:
        from datetime import datetime
        logger.warning(
            f"CVD insufficient data: symbol={symbol}, period={period}, "
            f"records_found={len(records)}, buckets=0"
        )
        return None

    last_5 = period_deltas[-5:] if len(period_deltas) >= 5 else period_deltas
    current_delta = period_deltas[-1]
    cumulative = sum(period_deltas)

    return {
        "current": current_delta,
        "last_5": last_5,
        "cumulative": cumulative,
        "period": period
    }


def _get_taker_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid", include_debug: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Get Taker Buy/Sell Volume data.

    Returns buy volume, sell volume, and buy/sell ratio.
    If include_debug=True (for Binance), includes debug_snapshot for troubleshooting.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketTradesAggregated.timestamp,
        MarketTradesAggregated.taker_buy_notional,
        MarketTradesAggregated.taker_sell_notional
    ).filter(
        MarketTradesAggregated.symbol == symbol.upper(),
        MarketTradesAggregated.exchange == exchange.lower(),
        MarketTradesAggregated.timestamp >= start_time,
        MarketTradesAggregated.timestamp <= current_time_ms
    ).order_by(MarketTradesAggregated.timestamp).all()

    if not records:
        return None

    # Aggregate by period
    buckets = {}
    for ts, buy_notional, sell_notional in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"buy": Decimal("0"), "sell": Decimal("0")}
        buckets[bucket_ts]["buy"] += buy_notional or Decimal("0")
        buckets[bucket_ts]["sell"] += sell_notional or Decimal("0")

    sorted_times = sorted(buckets.keys())
    ratios = []
    volumes = []

    for ts in sorted_times:
        bucket = buckets[ts]
        buy = float(bucket["buy"])
        sell = float(bucket["sell"])
        ratio = buy / sell if sell > 0 else 1.0
        ratios.append(ratio)
        volumes.append(buy + sell)

    if not ratios:
        return None

    # Current period data
    current_bucket = buckets[sorted_times[-1]]
    current_buy = float(current_bucket["buy"])
    current_sell = float(current_bucket["sell"])
    current_ratio = current_buy / current_sell if current_sell > 0 else 1.0

    last_5_ratios = ratios[-5:] if len(ratios) >= 5 else ratios
    last_5_volumes = volumes[-5:] if len(volumes) >= 5 else volumes

    result = {
        "buy": current_buy,
        "sell": current_sell,
        "ratio": current_ratio,
        "ratio_last_5": last_5_ratios,
        "volume_last_5": last_5_volumes,
        "period": period
    }

    # Add debug snapshot for Binance troubleshooting
    if include_debug:
        # Build buckets summary for debug
        buckets_debug = {}
        for ts in sorted_times:
            bucket = buckets[ts]
            buckets_debug[str(ts)] = {
                "buy": float(bucket["buy"]),
                "sell": float(bucket["sell"]),
                "total": float(bucket["buy"] + bucket["sell"])
            }

        result["debug_snapshot"] = {
            "query_params": {
                "current_time_ms": current_time_ms,
                "start_time": start_time,
                "interval_ms": interval_ms,
                "exchange": exchange,
                "symbol": symbol
            },
            "records_count": len(records),
            "records_ts_range": [records[0][0], records[-1][0]] if records else [],
            "buckets_count": len(buckets),
            "buckets": buckets_debug,
            "result_bucket_ts": sorted_times[-1],
            "result": {
                "buy": current_buy,
                "sell": current_sell,
                "total": current_buy + current_sell,
                "ratio": current_ratio
            }
        }

    return result


def _get_oi_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid"
) -> Optional[Dict[str, Any]]:
    """
    Get Open Interest USD change data.

    OI change measures the absolute USD value change in open interest over the period.
    Formula: (current_OI - previous_OI) × mark_price
    Returns current change and last 5 change values (in USD, can be positive or negative).
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketAssetMetrics.timestamp,
        MarketAssetMetrics.open_interest,
        MarketAssetMetrics.mark_price
    ).filter(
        MarketAssetMetrics.symbol == symbol.upper(),
        MarketAssetMetrics.exchange == exchange.lower(),
        MarketAssetMetrics.timestamp >= start_time,
        MarketAssetMetrics.timestamp <= current_time_ms
    ).order_by(MarketAssetMetrics.timestamp).all()

    if not records:
        logger.warning(f"OI insufficient data: symbol={symbol}, period={period}, records_found=0")
        return None

    # Aggregate by period - take last value in each bucket
    buckets = {}
    for ts, oi, price in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = (oi, price)

    sorted_times = sorted(buckets.keys())
    if len(sorted_times) < 2:
        logger.warning(f"OI insufficient data: symbol={symbol}, buckets={len(sorted_times)}, need_min=2")
        return None

    # Calculate OI USD changes: (current_OI - previous_OI) × mark_price
    oi_changes = []
    for i in range(1, len(sorted_times)):
        curr_oi, curr_price = buckets[sorted_times[i]]
        prev_oi, _ = buckets[sorted_times[i-1]]
        if curr_oi and prev_oi and curr_price:
            curr_oi_f = decimal_to_float(curr_oi)
            prev_oi_f = decimal_to_float(prev_oi)
            price_f = decimal_to_float(curr_price)
            change_usd = (curr_oi_f - prev_oi_f) * price_f
            oi_changes.append(round(change_usd, 2))

    if not oi_changes:
        _log_insufficient_data_once(
            f"oi:{exchange.lower()}:{symbol.upper()}:{period}:valid_changes",
            f"OI insufficient data: symbol={symbol}, valid_changes=0",
        )
        return None

    return {
        "current": oi_changes[-1],
        "last_5": oi_changes[-5:] if len(oi_changes) >= 5 else oi_changes,
        "period": period
    }


def _get_oi_delta_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid"
) -> Optional[Dict[str, Any]]:
    """
    Get Open Interest Delta (change percentage) data.

    Returns current OI change % and last 5 changes.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketAssetMetrics.timestamp,
        MarketAssetMetrics.open_interest
    ).filter(
        MarketAssetMetrics.symbol == symbol.upper(),
        MarketAssetMetrics.exchange == exchange.lower(),
        MarketAssetMetrics.timestamp >= start_time,
        MarketAssetMetrics.timestamp <= current_time_ms
    ).order_by(MarketAssetMetrics.timestamp).all()

    if not records:
        from datetime import datetime
        _log_insufficient_data_once(
            f"oi_delta:{exchange.lower()}:{symbol.upper()}:{period}:records",
            f"OI_DELTA insufficient data: symbol={symbol}, period={period}, "
            f"query_range=[{datetime.utcfromtimestamp(start_time/1000)} - "
            f"{datetime.utcfromtimestamp(current_time_ms/1000)}], records_found=0"
        )
        return None

    # Aggregate by period - take last value in each bucket
    buckets = {}
    for ts, oi in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = oi

    sorted_times = sorted(buckets.keys())
    if len(sorted_times) < 2:
        _log_insufficient_data_once(
            f"oi_delta:{exchange.lower()}:{symbol.upper()}:{period}:buckets",
            f"OI_DELTA insufficient data: symbol={symbol}, period={period}, "
            f"records_found={len(records)}, buckets={len(sorted_times)}, need_min=2"
        )
        return None

    # Calculate OI changes
    oi_values = [decimal_to_float(buckets[ts]) for ts in sorted_times]
    oi_changes = []
    for i in range(1, len(oi_values)):
        if oi_values[i] and oi_values[i-1] and oi_values[i-1] != 0:
            change_pct = ((oi_values[i] - oi_values[i-1]) / oi_values[i-1]) * 100
            oi_changes.append(change_pct)

    if not oi_changes:
        _log_insufficient_data_once(
            f"oi_delta:{exchange.lower()}:{symbol.upper()}:{period}:valid_changes",
            f"OI_DELTA insufficient data: symbol={symbol}, period={period}, "
            f"records_found={len(records)}, buckets={len(sorted_times)}, valid_changes=0"
        )
        return None

    current_change = oi_changes[-1]
    last_5 = oi_changes[-5:] if len(oi_changes) >= 5 else oi_changes

    return {
        "current": current_change,
        "last_5": last_5,
        "period": period
    }


def _get_funding_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid"
) -> Optional[Dict[str, Any]]:
    """
    Get Funding Rate data.

    Returns current funding rate and last 5 values.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketAssetMetrics.timestamp,
        MarketAssetMetrics.funding_rate
    ).filter(
        MarketAssetMetrics.symbol == symbol.upper(),
        MarketAssetMetrics.exchange == exchange.lower(),
        MarketAssetMetrics.timestamp >= start_time,
        MarketAssetMetrics.timestamp <= current_time_ms,
        MarketAssetMetrics.funding_rate.isnot(None)
    ).order_by(MarketAssetMetrics.timestamp).all()

    if not records:
        return None

    # Aggregate by period - take last value in each bucket
    buckets = {}
    for ts, funding in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = funding

    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        return None

    # Get funding rate values aligned with K-line chart display
    # Database stores decimal form (e.g., 0.0000125)
    # K-line chart: raw × 100 (to %) × 10000 = raw × 1000000 for display
    # This gives values like 12.5 when raw is 0.0000125
    funding_values = []
    for ts in sorted_times:
        fr = buckets[ts]
        if fr is not None:
            funding_values.append(float(fr) * 1000000)  # Align with K-line display

    if not funding_values:
        return None

    current_val = funding_values[-1]
    # Convert back to percentage for display: val / 10000 = percentage
    current_pct = current_val / 10000

    # Calculate change from previous period
    if len(funding_values) >= 2:
        change_val = current_val - funding_values[-2]
    else:
        change_val = 0.0
    change_pct = change_val / 10000

    last_5 = funding_values[-5:] if len(funding_values) >= 5 else funding_values

    # Calculate annualized rate (assuming 8-hour funding periods, 3 per day)
    annualized = current_pct * 3 * 365

    return {
        "current": current_val,        # Current rate (K-line display unit)
        "current_pct": current_pct,    # Current rate in percentage
        "change": change_val,          # Change from previous period (K-line display unit)
        "change_pct": change_pct,      # Change in percentage
        "last_5": last_5,              # Last 5 values (K-line display unit)
        "annualized": annualized,      # Annualized rate in percentage
        "period": period
    }


def _get_depth_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid"
) -> Optional[Dict[str, Any]]:
    """
    Get Order Book Depth data.

    Returns bid/ask depth ratio and last 5 values.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketOrderbookSnapshots.timestamp,
        MarketOrderbookSnapshots.bid_depth_5,
        MarketOrderbookSnapshots.ask_depth_5,
        MarketOrderbookSnapshots.spread
    ).filter(
        MarketOrderbookSnapshots.symbol == symbol.upper(),
        MarketOrderbookSnapshots.exchange == exchange.lower(),
        MarketOrderbookSnapshots.timestamp >= start_time,
        MarketOrderbookSnapshots.timestamp <= current_time_ms
    ).order_by(MarketOrderbookSnapshots.timestamp).all()

    if not records:
        return None

    # Aggregate by period - take last value in each bucket
    buckets = {}
    for ts, bid_depth, ask_depth, spread in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = {
            "bid": bid_depth,
            "ask": ask_depth,
            "spread": spread
        }

    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        return None

    # Calculate depth ratios
    ratios = []
    for ts in sorted_times:
        bucket = buckets[ts]
        bid = decimal_to_float(bucket["bid"]) or 0
        ask = decimal_to_float(bucket["ask"]) or 0
        ratio = bid / ask if ask > 0 else 1.0
        ratios.append(ratio)

    current_bucket = buckets[sorted_times[-1]]
    current_bid = decimal_to_float(current_bucket["bid"]) or 0
    current_ask = decimal_to_float(current_bucket["ask"]) or 0
    current_ratio = current_bid / current_ask if current_ask > 0 else 1.0
    current_spread = decimal_to_float(current_bucket["spread"])

    last_5_ratios = ratios[-5:] if len(ratios) >= 5 else ratios

    return {
        "bid": current_bid,
        "ask": current_ask,
        "ratio": current_ratio,
        "ratio_last_5": last_5_ratios,
        "spread": current_spread,
        "period": period
    }


def _get_imbalance_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid"
) -> Optional[Dict[str, Any]]:
    """
    Get Order Book Imbalance data.

    Imbalance = (Bid - Ask) / (Bid + Ask), range -1 to 1
    Positive = more bid support, Negative = more ask pressure
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketOrderbookSnapshots.timestamp,
        MarketOrderbookSnapshots.bid_depth_5,
        MarketOrderbookSnapshots.ask_depth_5
    ).filter(
        MarketOrderbookSnapshots.symbol == symbol.upper(),
        MarketOrderbookSnapshots.exchange == exchange.lower(),
        MarketOrderbookSnapshots.timestamp >= start_time,
        MarketOrderbookSnapshots.timestamp <= current_time_ms
    ).order_by(MarketOrderbookSnapshots.timestamp).all()

    if not records:
        return None

    # Aggregate by period - take last value in each bucket
    buckets = {}
    for ts, bid_depth, ask_depth in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = {"bid": bid_depth, "ask": ask_depth}

    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        return None

    # Calculate imbalance values
    imbalances = []
    for ts in sorted_times:
        bucket = buckets[ts]
        bid = decimal_to_float(bucket["bid"]) or 0
        ask = decimal_to_float(bucket["ask"]) or 0
        total = bid + ask
        imbalance = (bid - ask) / total if total > 0 else 0.0
        imbalances.append(imbalance)

    current_imbalance = imbalances[-1]
    last_5 = imbalances[-5:] if len(imbalances) >= 5 else imbalances

    return {
        "current": current_imbalance,
        "last_5": last_5,
        "period": period
    }


def _get_price_change_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid"
) -> Optional[Dict[str, Any]]:
    """
    Get Price Change data.

    Calculates price change percentage over the specified time window.
    Uses high_price from market_trades_aggregated (15-second granularity).

    Returns:
        current: Price change percentage (e.g., 0.15 means +0.15%)
        start_price: Price at the start of the window
        end_price: Current price
        last_5: Last 5 period price changes
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketTradesAggregated.timestamp,
        MarketTradesAggregated.high_price
    ).filter(
        MarketTradesAggregated.symbol == symbol.upper(),
        MarketTradesAggregated.exchange == exchange.lower(),
        MarketTradesAggregated.timestamp >= start_time,
        MarketTradesAggregated.timestamp <= current_time_ms,
        MarketTradesAggregated.high_price.isnot(None)
    ).order_by(MarketTradesAggregated.timestamp).all()

    if not records or len(records) < 2:
        return None

    # Aggregate by period bucket
    buckets = {}
    for ts, high_price in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"first_price": None, "last_price": None}
        price = decimal_to_float(high_price)
        if buckets[bucket_ts]["first_price"] is None:
            buckets[bucket_ts]["first_price"] = price
        buckets[bucket_ts]["last_price"] = price

    sorted_times = sorted(buckets.keys())
    if len(sorted_times) < 2:
        return None

    # Calculate price change for each period
    price_changes = []
    for i, ts in enumerate(sorted_times):
        if i == 0:
            continue
        prev_ts = sorted_times[i - 1]
        prev_price = buckets[prev_ts]["last_price"]
        curr_price = buckets[ts]["last_price"]
        if prev_price and prev_price > 0:
            change_pct = ((curr_price - prev_price) / prev_price) * 100
            price_changes.append(change_pct)

    if not price_changes:
        return None

    # Current price change: compare current bucket to one period ago
    current_bucket = buckets[sorted_times[-1]]
    prev_bucket = buckets[sorted_times[-2]] if len(sorted_times) >= 2 else None

    if prev_bucket and prev_bucket["last_price"] and prev_bucket["last_price"] > 0:
        current_change = ((current_bucket["last_price"] - prev_bucket["last_price"])
                          / prev_bucket["last_price"]) * 100
    else:
        current_change = price_changes[-1] if price_changes else 0

    last_5 = price_changes[-5:] if len(price_changes) >= 5 else price_changes

    return {
        "current": current_change,
        "start_price": prev_bucket["last_price"] if prev_bucket else None,
        "end_price": current_bucket["last_price"],
        "last_5": last_5,
        "period": period
    }


def _get_volatility_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: str = "hyperliquid"
) -> Optional[Dict[str, Any]]:
    """
    Get Volatility (Price Range) data.

    Calculates price volatility as (high - low) / low percentage over the time window.
    Uses high_price and low_price from market_trades_aggregated (15-second granularity).

    Returns:
        current: Current period volatility percentage (e.g., 0.25 means 0.25%)
        high: Highest price in the window
        low: Lowest price in the window
        last_5: Last 5 period volatility values
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketTradesAggregated.timestamp,
        MarketTradesAggregated.high_price,
        MarketTradesAggregated.low_price
    ).filter(
        MarketTradesAggregated.symbol == symbol.upper(),
        MarketTradesAggregated.exchange == exchange.lower(),
        MarketTradesAggregated.timestamp >= start_time,
        MarketTradesAggregated.timestamp <= current_time_ms,
        MarketTradesAggregated.high_price.isnot(None),
        MarketTradesAggregated.low_price.isnot(None)
    ).order_by(MarketTradesAggregated.timestamp).all()

    if not records:
        return None

    # Aggregate by period bucket - track high and low for each bucket
    buckets = {}
    for ts, high_price, low_price in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        high = decimal_to_float(high_price)
        low = decimal_to_float(low_price)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"high": high, "low": low}
        else:
            if high and (buckets[bucket_ts]["high"] is None or high > buckets[bucket_ts]["high"]):
                buckets[bucket_ts]["high"] = high
            if low and (buckets[bucket_ts]["low"] is None or low < buckets[bucket_ts]["low"]):
                buckets[bucket_ts]["low"] = low

    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        return None

    # Calculate volatility for each period
    volatilities = []
    for ts in sorted_times:
        bucket = buckets[ts]
        high = bucket["high"]
        low = bucket["low"]
        if high and low and low > 0:
            volatility_pct = ((high - low) / low) * 100
            volatilities.append(volatility_pct)

    if not volatilities:
        return None

    current_bucket = buckets[sorted_times[-1]]
    current_high = current_bucket["high"]
    current_low = current_bucket["low"]
    current_volatility = ((current_high - current_low) / current_low * 100
                          if current_high and current_low and current_low > 0 else 0)

    last_5 = volatilities[-5:] if len(volatilities) >= 5 else volatilities

    return {
        "current": current_volatility,
        "high": current_high,
        "low": current_low,
        "last_5": last_5,
        "period": period
    }
