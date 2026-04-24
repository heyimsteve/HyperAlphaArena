"""
AI Program Coding Service

Handles AI-assisted program code writing conversations using LLM.
Supports Function Calling for AI to query API docs, validate code, and test run.
"""

import json
import logging
import random
import re
import requests
import time
import traceback
from typing import Dict, List, Optional, Any, Generator
from datetime import datetime

from sqlalchemy.orm import Session

from database.models import (
    AiProgramConversation, AiProgramMessage, TradingProgram, Account,
    BacktestResult, BacktestTriggerLog, AccountProgramBinding
)
from services.ai_decision_service import build_chat_completion_endpoints, detect_api_format, _extract_text_from_message, get_max_tokens, build_llm_payload, build_llm_headers, extract_reasoning, convert_tools_to_anthropic, convert_messages_to_anthropic, strip_thinking_tags
from services.ai_stream_service import format_sse_event
from services.system_logger import system_logger
from services.ai_shared_tools import (
    SHARED_SIGNAL_TOOLS,
    execute_get_signal_pools,
    execute_run_signal_backtest
)

logger = logging.getLogger(__name__)

# Retry configuration for API calls
API_MAX_RETRIES = 5
API_BASE_DELAY = 1.0  # seconds
API_MAX_DELAY = 16.0  # seconds
RETRYABLE_STATUS_CODES = {502, 503, 504, 429}


def _should_retry_api(status_code: Optional[int], error: Optional[str]) -> bool:
    """Check if API error is retryable."""
    if status_code and status_code in RETRYABLE_STATUS_CODES:
        return True
    if error and any(x in error.lower() for x in ['timeout', 'connection', 'reset', 'eof']):
        return True
    return False


def _get_retry_delay(attempt: int) -> float:
    """Calculate retry delay with exponential backoff and jitter."""
    delay = min(API_BASE_DELAY * (2 ** attempt), API_MAX_DELAY)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter


# System prompt for AI program coding
PROGRAM_SYSTEM_PROMPT = """You are an expert Python developer for cryptocurrency trading programs.
You help users write trading strategy code that runs in a sandboxed environment.

## EXCHANGE SUPPORT
This system supports multiple exchanges:
- **hyperliquid**: Hyperliquid perpetual futures (default)
- **binance**: Binance USDT-M futures

When querying market data or signal pools, specify the `exchange` parameter to get data from the correct source.
The exchange should match the signal pool's exchange setting that will trigger your strategy.

## CRITICAL: Query Market Data Before Writing Thresholds
**IMPORTANT**: Before writing ANY threshold comparisons in your code, you MUST use the `query_market_data` tool to check current market values. Indicator values vary significantly:
- RSI: 0-100 (oversold <30, overbought >70)
- CVD: Can range from -50M to +50M depending on market activity
- OI (Open Interest): Can be 100M to 500M+ for BTC
- ATR: Varies from 200 to 1500+ depending on volatility
- MACD: Typically -1000 to +1000 for BTC

**Example workflow**:
1. User asks for "RSI oversold strategy for Binance"
2. Call `query_market_data` with symbol="BTC", exchange="binance" to see current RSI value
3. Now you know the scale and can write appropriate thresholds

## CODE STRUCTURE (REQUIRED)
Your code must define a strategy class with `should_trade` method:

```python
class MyStrategy:
    def init(self, params):
        # Initialize parameters (optional but recommended)
        self.threshold = params.get("threshold", 30)

    def should_trade(self, data):
        # Main decision logic - called when signal triggers
        # Must return a Decision object
        return Decision(
            operation="hold",
            symbol=data.trigger_symbol,
            reason="No trade condition met"
        )
```

## AVAILABLE IN SANDBOX

### Decision - Return value (REQUIRED)
```python
# For BUY (open long):
Decision(
    operation="buy",            # Required: "buy", "sell", "hold", or "close"
    symbol="BTC",               # Required: Trading symbol
    target_portion_of_balance=0.5,  # Required for buy/sell/close: 0.1-1.0
    leverage=10,                # Required for buy/sell/close: 1-50
    max_price=95000.0,          # Required for buy: maximum entry price
    time_in_force="Ioc",        # Optional: "Ioc", "Gtc", "Alo" (default: "Ioc")
    take_profit_price=100000.0, # Optional: TP trigger price
    stop_loss_price=90000.0,    # Optional: SL trigger price
    tp_execution="limit",       # Optional: "market" or "limit" (default: "limit")
    sl_execution="limit",       # Optional: "market" or "limit" (default: "limit")
    reason="RSI oversold",      # Optional: Reason for decision
    trading_strategy="..."      # Optional: Entry thesis, risk controls, exit plan
)

# For SELL (open short):
Decision(
    operation="sell",
    symbol="BTC",
    target_portion_of_balance=0.5,
    leverage=10,
    min_price=95000.0,          # Required for sell: minimum entry price
    ...
)

# For CLOSE (close position):
Decision(
    operation="close",
    symbol="BTC",
    target_portion_of_balance=1.0,  # Portion of position to close
    leverage=10,
    min_price=95000.0,          # Required for closing LONG position
    # OR max_price=95000.0,     # Required for closing SHORT position
    ...
)

# For HOLD (no action):
Decision(operation="hold", symbol="BTC", reason="No trade condition")
```

**IMPORTANT: Price Precision**
- All calculated prices (max_price, min_price, take_profit_price, stop_loss_price) should use round() to control decimal places
- Match the precision of market prices - different assets have different precision requirements
- BTC/ETH typically use 1-2 decimals, small-cap coins may need 4-8 decimals
- This ensures clean, readable prices and avoids floating-point precision issues (e.g., 93622.54776373146)

### data (MarketData) - Input parameter
```python
# Account info
data.available_balance    # float: Available balance in USD
data.total_equity         # float: Total equity (includes unrealized PnL)
data.used_margin          # float: Currently used margin
data.margin_usage_percent # float: Margin usage percentage (0-100 scale)
data.maintenance_margin   # float: Maintenance margin requirement
data.positions            # Dict[str, Position]: Current positions by symbol
data.recent_trades        # List[Trade]: Recent closed trades history
data.open_orders          # List[Order]: Current open orders (TP/SL, limit orders)

# Trigger info
data.trigger_symbol       # str: Symbol that triggered this execution (empty string "" for scheduled triggers)
data.trigger_type         # str: "signal" or "scheduled"

# Trigger context (detailed) - only populated for signal triggers
data.signal_pool_name     # str: Name of the signal pool that triggered (empty for scheduled)
data.pool_logic           # str: "OR" or "AND" - how signals in the pool are combined
data.triggered_signals    # List[Dict]: Full details of each triggered signal (see Signal section below)
data.trigger_market_regime  # RegimeInfo or None: Market regime snapshot at trigger time

# Environment info
data.environment          # str: "mainnet" or "testnet"
data.max_leverage         # int: Maximum allowed leverage for this account
data.default_leverage     # int: Default leverage setting

# Methods
data.get_indicator(symbol, indicator, period) -> dict  # Technical indicators
data.get_klines(symbol, period, count) -> list         # K-line data (default count=50)
                                                       # Example: [{"timestamp": 1768644000, "open": 95287.0, "high": 95296.0,
                                                       #            "low": 95119.0, "close": 95120.0, "volume": 259.17}, ...]
data.get_price_change(symbol, period) -> dict          # Price change info
                                                       # Example: {"change_percent": 0.0, "change_usd": 0.0}
data.get_market_data(symbol) -> dict                   # Complete market data (price, volume, OI, funding rate)
                                                       # Example: {"symbol": "BTC", "price": 95460.0, "oracle_price": 95251.0,
                                                       #           "change24h": 360.0, "volume24h": 1778510.45, "percentage24h": 0.378,
                                                       #           "open_interest": 10898599.47, "funding_rate": 0.0000425}
data.get_flow(symbol, metric, period) -> dict          # Market flow metrics
data.get_regime(symbol, period) -> RegimeInfo          # Market regime classification
data.get_factor(symbol, factor_name, period="5m") -> dict  # Factor value + effectiveness (IC/ICIR/win_rate/decay)
data.get_factor_ranking(symbol, top_n=10) -> list      # Top factors by |ICIR|
```

### Position - Current position info (from data.positions)
```python
# Access: pos = data.positions.get("BTC")
pos.symbol            # str: Trading symbol
pos.side              # str: "long" or "short"
pos.size              # float: Position size
pos.entry_price       # float: Entry price
pos.unrealized_pnl    # float: Unrealized PnL
pos.leverage          # int: Leverage used
pos.liquidation_price # float: Liquidation price
# Position timing (for time-based exit strategies)
pos.opened_at              # int or None: Timestamp in milliseconds when position was opened
pos.opened_at_str          # str or None: Human-readable opened time (e.g., "2026-01-15 10:30:00 UTC")
pos.holding_duration_seconds  # float or None: How long position has been held in seconds
pos.holding_duration_str   # str or None: Human-readable duration (e.g., "2h 30m")
# Example: Position(symbol="BTC", side="long", size=0.001, entry_price=95400.0,
#                   unrealized_pnl=0.03, leverage=1, liquidation_price=0.0,
#                   opened_at=1736942400000, opened_at_str="2026-01-15 10:30:00 UTC",
#                   holding_duration_seconds=7200.0, holding_duration_str="2h 0m")
```

### Trade - Recent trade record (from data.recent_trades)
```python
# Access: trades = data.recent_trades (list, most recent first)
trade.symbol      # str: Trading symbol
trade.side        # str: "Long" or "Short"
trade.size        # float: Trade size
trade.price       # float: Close price
trade.timestamp   # int: Close timestamp in milliseconds
trade.pnl         # float: Realized profit/loss in USD
trade.close_time  # str: Close time in UTC string format
# Example: Trade(symbol="BTC", side="Sell", size=0.001, price=95367.0,
#                timestamp=1768665292968, pnl=-0.033, close_time="2026-01-17 15:54:52 UTC")
```

### Order - Open order info (from data.open_orders)
```python
# Access: orders = data.open_orders (list of all open orders)
order.order_id       # int: Unique order ID
order.symbol         # str: Trading symbol
order.side           # str: "Buy" or "Sell"
order.direction      # str: "Open Long", "Open Short", "Close Long", "Close Short"
order.order_type     # str: Order type
                     # Possible values:
                     #   - "Market": Market order (immediate execution at best price)
                     #   - "Limit": Limit order (execute at specified price or better)
                     #   - "Stop Market": Stop loss market order (trigger → market execution)
                     #   - "Stop Limit": Stop loss limit order (trigger → limit order)
                     #   - "Take Profit Market": Take profit market order (trigger → market execution)
                     #   - "Take Profit Limit": Take profit limit order (trigger → limit order)
order.size           # float: Order size
order.price          # float: Limit price
order.trigger_price  # float: Trigger price (for stop/TP orders)
order.reduce_only    # bool: Whether this is a reduce-only order
order.timestamp      # int: Order placement timestamp in milliseconds
# Example: Order(order_id=46731293990, symbol="BTC", side="Sell", direction="Close Long",
#                order_type="Limit", size=0.001, price=76320.0, trigger_price=None,
#                reduce_only=True, timestamp=1768665293187)
```

### Kline - K-line data (from get_klines)
```python
# Access: klines = data.get_klines(symbol, "1h", 50)
kline.timestamp  # int: Unix timestamp in seconds
kline.open       # float: Open price
kline.high       # float: High price
kline.low        # float: Low price
kline.close      # float: Close price
kline.volume     # float: Volume
# Example: Kline(timestamp=1768658400, open=95673.0, high=95673.0, low=95160.0,
#                close=95400.0, volume=2.98375)
```

### RegimeInfo - Market regime (from get_regime or trigger_market_regime)
```python
# Access: regime = data.get_regime(symbol, "1h")
# Or: regime = data.trigger_market_regime (snapshot at trigger time, None for scheduled)
regime.regime     # str: "breakout", "absorption", "stop_hunt", "exhaustion", "trap", "continuation", "noise"
regime.conf       # float: Confidence 0.0-1.0
regime.direction  # str: "bullish", "bearish", "neutral"
regime.reason     # str: Human-readable explanation
regime.indicators # dict: Indicator values used for classification
# Example: RegimeInfo(regime="noise", conf=0.467, direction="neutral",
#           reason="No clear market regime detected",
#           indicators={"cvd_ratio": 0.9968, "oi_delta": 0.051, "taker_ratio": 627.585,
#                       "price_atr": -0.719, "rsi": 44.2})
```

### Signal - Triggered signal info (from data.triggered_signals)
```python
# Access: signals = data.triggered_signals (list, only populated for signal triggers)

# Supported metric types:
# - oi_delta: Open Interest change percentage
# - cvd: Cumulative Volume Delta
# - depth_ratio: Order book depth ratio (bid/ask)
# - order_imbalance: Order book imbalance (-1 to +1)
# - taker_ratio: Taker buy/sell ratio
# - funding: Funding rate change (bps)
# - oi: Open Interest change (USD)
# - price_change: Price change percentage
# - volatility: Price volatility
# - taker_volume: Taker volume (special composite signal)

# Standard signal format (all metrics except taker_volume):
signal["signal_id"]     # int: Signal ID
signal["signal_name"]   # str: Name of the signal
signal["description"]   # str: Description of what the signal detects
signal["metric"]        # str: Metric type (see list above)
signal["time_window"]   # str: Time window (e.g., "5m", "1h")
signal["operator"]      # str: Comparison operator ("<", ">", "<=", ">=", "abs_greater_than")
signal["threshold"]     # float: Threshold value
signal["current_value"] # float: Current value that triggered the signal
signal["condition_met"] # bool: Whether condition was met
# Example: {"signal_id": 31, "signal_name": "OI Delta Spike", "metric": "oi_delta",
#           "time_window": "5m", "operator": ">", "threshold": 1.0,
#           "current_value": 1.52, "condition_met": True}

# Taker volume signal format (special composite signal):
signal["signal_id"]        # int: Signal ID
signal["signal_name"]      # str: Name of the signal
signal["metric"]           # str: Always "taker_volume"
signal["time_window"]      # str: Time window
signal["direction"]        # str: "buy" or "sell" - dominant side
signal["buy"]              # float: Taker buy volume in USD
signal["sell"]             # float: Taker sell volume in USD
signal["total"]            # float: Total volume (buy + sell)
signal["ratio"]            # float: Buy/sell ratio
signal["ratio_threshold"]  # float: Threshold ratio that triggered
signal["volume_threshold"] # float: Minimum volume threshold
signal["condition_met"]    # bool: Whether condition was met
# Example: {"signal_id": 42, "signal_name": "Taker Buy Surge", "metric": "taker_volume",
#           "time_window": "5m", "direction": "buy", "buy": 5234567.89, "sell": 2345678.9,
#           "total": 7580246.79, "ratio": 2.23, "ratio_threshold": 1.5,
#           "volume_threshold": 1000000, "condition_met": True}
```

### Debug function
- log(message): Print debug message (visible in test run output)

### Available indicators for get_indicator():
- "RSI14", "RSI7" - RSI (returns {"value": float})
  Example: {"value": 46.76, "series": [50.0, 0.0, 0.0, 5.94, ...]}
- "MACD" - MACD (returns {"macd": float, "signal": float, "histogram": float})
  Example: {"macd": -73.27, "signal": -81.88, "histogram": 8.60}
- "EMA20", "EMA50", "EMA100" - EMA (returns {"value": float})
- "MA5", "MA10", "MA20" - Moving Average (returns {"value": float})
- "BOLL" - Bollinger Bands (returns {"upper": float, "middle": float, "lower": float})
- "ATR14" - Average True Range (returns {"value": float})
- "VWAP" - Volume Weighted Average Price (returns {"value": float})
- "STOCH" - Stochastic (returns {"k": float, "d": float})
- "OBV" - On Balance Volume (returns {"value": float})

### Available metrics for get_flow():
All flow metrics return a dict with `last_5` (historical values) and `period` fields for trend analysis.

**CVD** - Cumulative Volume Delta (taker buy - sell notional)
```python
data.get_flow("BTC", "CVD", "1h")
# Returns:
{
    "current": 14877256.20,      # Current period's delta (USD)
    "last_5": [11371465.41, 13850815.24, 319912.24, -13948838.70, 14877256.20],  # Last 5 periods
    "cumulative": 17906808.24,   # Cumulative sum over lookback window
    "period": "1h"
}
# Usage: Positive = net buying pressure, Negative = net selling pressure
# Trend check: if last_5[-1] > last_5[-2] > last_5[-3]: # CVD trending up
```

**OI** - Open Interest USD change
```python
data.get_flow("BTC", "OI", "1h")
# Returns:
{
    "current": 16826201.53,      # Current period's OI change (USD)
    "last_5": [-11304403.21, 974887.72, 12684888.56, -7948264.33, 16826201.53],
    "period": "1h"
}
# Usage: Positive = new positions opening, Negative = positions closing
```

**OI_DELTA** - Open Interest Change Percentage
```python
data.get_flow("BTC", "OI_DELTA", "1h")
# Returns:
{
    "current": 0.595,            # Current period's OI change (%)
    "last_5": [-0.398, 0.035, 0.449, -0.281, 0.595],
    "period": "1h"
}
# Usage: > 1% = significant new positions, < -1% = significant liquidations
```

**TAKER** - Taker Buy/Sell Volume
```python
data.get_flow("BTC", "TAKER", "1h")
# Returns:
{
    "buy": 18915411.13,          # Taker buy volume (USD)
    "sell": 4038154.92,          # Taker sell volume (USD)
    "ratio": 4.684,              # Buy/Sell ratio (>1 = buyers dominate)
    "ratio_last_5": [1.665, 2.580, 1.019, 0.663, 4.684],  # Historical ratios
    "volume_last_5": [45596648.74, 31381884.86, 34341736.69, 68742754.71, 22953566.05],
    "period": "1h"
}
# Usage: ratio > 1.5 = strong buying, ratio < 0.7 = strong selling
```

**FUNDING** - Funding Rate
```python
data.get_flow("BTC", "FUNDING", "1h")
# Returns:
{
    "current": 11.2,             # Current rate (display unit: raw × 1000000)
    "current_pct": 0.00112,      # Current rate as percentage (0.00112%)
    "change": 1.55,              # Rate change from previous period
    "change_pct": 0.000155,      # Rate change as percentage
    "last_5": [12.37, 12.5, 12.5, 9.65, 11.2],
    "annualized": 1.2264,        # Annualized rate percentage
    "period": "1h"
}
# Usage: Positive = longs pay shorts (bullish sentiment), Negative = shorts pay longs
# Signal triggers on rate CHANGE, not absolute value
```

**DEPTH** - Order Book Depth
```python
data.get_flow("BTC", "DEPTH", "1h")
# Returns:
{
    "bid": 28.34,                # Bid depth (USD millions)
    "ask": 0.04,                 # Ask depth (USD millions)
    "ratio": 635.07,             # Bid/Ask ratio (>1 = more buy orders)
    "ratio_last_5": [0.024, 0.907, 437.95, 0.033, 635.07],
    "spread": 1.0,               # Bid-ask spread
    "period": "1h"
}
# Usage: ratio > 1.5 = strong bid support, ratio < 0.7 = strong ask pressure
```

**IMBALANCE** - Order Book Imbalance
```python
data.get_flow("BTC", "IMBALANCE", "1h")
# Returns:
{
    "current": 0.997,            # Imbalance score (-1 to +1)
    "last_5": [-0.953, -0.049, 0.995, -0.936, 0.997],
    "period": "1h"
}
# Usage: > 0.3 = bullish imbalance, < -0.3 = bearish imbalance
```

### Periods: "1m", "5m", "15m", "1h", "4h", "1d"

### Multi-Timeframe Signal Pools
A single Signal Pool can contain signals with different time windows. When triggered, `data.triggered_signals` may include signals from various timeframes:

```python
# Example: Signal pool with mixed timeframes
# - CVD signal on 1m (quick momentum)
# - OI Delta signal on 5m (position building)
# - Funding signal on 1h (sentiment extreme)

for sig in data.triggered_signals:
    timeframe = sig.get("time_window")  # "1m", "5m", "1h", etc.
    metric = sig.get("metric")
    if timeframe == "1m" and metric == "cvd":
        # Fast signal - use for timing
        pass
    elif timeframe == "1h" and metric == "funding":
        # Slow signal - use for direction bias
        pass
```

### Scheduled vs Signal Trigger (IMPORTANT)
Your strategy may be triggered by signal pool or scheduled interval. Handle both cases:

| Field | Signal Trigger | Scheduled Trigger |
|-------|---------------|-------------------|
| `data.trigger_type` | `"signal"` | `"scheduled"` |
| `data.trigger_symbol` | `"BTC"` (triggered symbol) | `""` (empty string) |
| `data.triggered_signals` | `[{signal details...}]` | `[]` (empty list) |
| `data.trigger_market_regime` | `RegimeInfo(...)` | `None` |
| `data.signal_pool_name` | `"OI Surge Monitor"` | `""` (empty string) |

```python
# Example: Handle both trigger types
def should_trade(self, data):
    if data.trigger_type == "scheduled":
        # Scheduled trigger: only check exit conditions, no new entries
        # Must specify symbol explicitly since trigger_symbol is empty
        symbol = "BTC"
        if symbol in data.positions:
            # Check exit conditions...
            pass
        return Decision(operation="hold", symbol=symbol, reason="Scheduled check - no action")

    # Signal trigger: use trigger_symbol and triggered_signals
    symbol = data.trigger_symbol
    for sig in data.triggered_signals:
        if sig.get("metric") == "oi_delta" and sig.get("current_value", 0) > 1.0:
            # OI spike detected...
            pass
```

### Additional sandbox objects available
- `time`: Pre-injected sandbox object for timestamp operations (use `time.time()`, do NOT write `import time`)
- `math`: Pre-injected sandbox object for math helpers (use `math.sqrt()`, `math.log()`, etc.; do NOT write `import math`)

## EXAMPLE STRATEGY
```python
class RSIStrategy:
    def init(self, params):
        self.threshold = params.get("threshold", 30)

    def should_trade(self, data):
        symbol = data.trigger_symbol
        market_data = data.get_market_data(symbol)
        price = market_data.get("price", 0)
        rsi = data.get_indicator(symbol, "RSI14", "5m")
        rsi_value = rsi.get("value", 50) if rsi else 50

        if rsi_value < self.threshold and price > 0:
            return Decision(
                operation="buy",
                symbol=symbol,
                target_portion_of_balance=0.5,
                leverage=10,
                max_price=price * 1.002,  # Allow 0.2% slippage
                take_profit_price=price * 1.05,
                stop_loss_price=price * 0.97,
                reason=f"RSI oversold: {rsi_value:.1f}"
            )

        return Decision(operation="hold", symbol=symbol)
```

## WORKFLOW
1. **FIRST**: Use `query_market_data` to check current indicator values for the target symbol
2. Use `get_current_code` to see existing code (if editing)
3. Use `get_api_docs` to check available methods if needed
4. Write code with appropriate thresholds based on queried data
5. Use `validate_code` to check syntax
6. Use `test_run_code` to test with real market data
7. **PAUSE**: After test passes, ask user if they want to verify strategy performance on historical data
8. If user agrees to verify:
   a. Ask user which exchange they plan to trade on (Hyperliquid or Binance)
   b. Use `get_signal_pools` with the chosen exchange to list available signal pools
   c. Present signal pools to user in friendly format (name + description)
   d. Ask user to choose signal pool AND/OR scheduled trigger interval (can use both together)
   e. Use `quick_verify_strategy` with user's choices to run verification
   f. Analyze results based on performance metrics (see VERIFICATION STANDARDS below)
   g. If performance is poor, suggest adjustments and re-verify
   h. **IMPORTANT**: Once verification passes, IMMEDIATELY call `suggest_save_code` - do NOT wait for user
9. If user declines verification (says "no", "skip", "just save", etc.):
   - **IMMEDIATELY** call `suggest_save_code` - do NOT ask again, do NOT wait
10. **CRITICAL**: The workflow is NOT complete until `suggest_save_code` is called. User cannot access the code otherwise.

## VERIFICATION STANDARDS
Analyze `quick_verify_strategy` results to determine if strategy is viable:

**Good signs:**
- total_pnl_percent > 0 (profitable)
- win_rate > 40%
- profit_factor > 1.5
- max_drawdown_percent < 20%

**Warning signs (suggest adjustments):**
- total_trades = 0: Strategy never trades - conditions too strict
- win_rate < 30%: Too many losing trades
- max_drawdown_percent > 30%: Risk too high
- profit_factor < 1.0: Losing money overall

**Key insight:** Signal pool triggers are typically for entry signals, scheduled triggers for exit/management. Many strategies use BOTH together.

## BACKTEST ANALYSIS (only when user asks to analyze backtest results)
When user asks to analyze strategy backtest results or performance:
1. Use `get_backtest_history` to get list of backtests with official stats (PnL, win_rate, etc.)
   - IMPORTANT: Use these stats directly! Do NOT recalculate from trigger list.
   - winning_trades = TP count, losing_trades = SL count
2. Use `get_trigger_list` to see all triggers (for identifying specific trades to analyze)
3. Use `get_trigger_details` to deep dive into specific triggers
   - fields: summary, input, output, queries, logs
   - Example: get_trigger_details(backtest_id=123, indexes=[5,8,12], fields=["summary","input"])

## IMPORTANT RULES
- Class must have `should_trade(self, data)` method
- `should_trade` must return a `Decision` object
- Use operation strings: "buy", "sell", "close", "hold"
- For buy/sell/close: must set target_portion_of_balance (0.1-1.0), leverage (1-50)
- For buy: must set max_price; For sell: must set min_price
- For close: set min_price (closing long) or max_price (closing short)
- Access trigger symbol via `data.trigger_symbol`
- Access balance via `data.available_balance`
- Always validate and test code before suggesting to save
"""

# Tools for AI program coding
PROGRAM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_market_data",
            "description": "Query current market data for a symbol from specified exchange. MUST call this FIRST before writing any threshold comparisons to understand actual indicator value ranges.",
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
                        "description": "Exchange to query market data from (default: hyperliquid)"
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_api_docs",
            "description": "Get detailed documentation for MarketData properties/methods and Decision object.",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_type": {
                        "type": "string",
                        "enum": ["market", "decision", "all"],
                        "description": "Which API documentation to retrieve (market=MarketData, decision=Decision/ActionType)"
                    }
                },
                "required": ["api_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_code",
            "description": "Get the current code of the program being edited. Returns empty if creating new program.",
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
            "name": "validate_code",
            "description": "Validate Python code syntax and check for common errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to validate"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "test_run_code",
            "description": "Test run code with real market data. Returns execution result or detailed error.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to test"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Symbol for market data context (e.g., BTC, ETH)"
                    }
                },
                "required": ["code", "symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "quick_verify_strategy",
            "description": "Quick verify strategy code on historical data. Simulates real execution with signal pool triggers and/or scheduled triggers. Returns full backtest metrics including PnL, win rate, max drawdown, profit factor. Run this BEFORE suggesting to save code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Strategy code to verify"
                    },
                    "exchange": {
                        "type": "string",
                        "enum": ["hyperliquid", "binance"],
                        "description": "Exchange to use for historical data"
                    },
                    "signal_pool_id": {
                        "type": "integer",
                        "description": "Signal pool ID for signal-based triggers (optional, can combine with scheduled)"
                    },
                    "scheduled_interval_minutes": {
                        "type": "integer",
                        "description": "Scheduled trigger interval in minutes (optional, can combine with signal pool)"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Trading symbol (default: BTC)"
                    },
                    "hours": {
                        "type": "integer",
                        "description": "Backtest duration in hours (default: 168 = 7 days)"
                    }
                },
                "required": ["code", "exchange"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_save_code",
            "description": "Propose code to save. Does NOT save directly - returns suggestion for user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Final Python code to suggest saving"
                    },
                    "name": {
                        "type": "string",
                        "description": "Suggested program name"
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what the program does"
                    }
                },
                "required": ["code", "name", "description"]
            }
        }
    }
]

# Backtest analysis tools - for analyzing strategy backtest results
BACKTEST_ANALYSIS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_backtest_history",
            "description": "Get backtest history for the current program. Use this when user asks to analyze backtest results or strategy performance. Returns list of backtests with key metrics (PnL, win rate, drawdown).",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of backtests to return (default: 10)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_trigger_list",
            "description": "Get trigger summary list for a specific backtest. Returns overview of each trigger: index, time, symbol, action, equity change, PnL. Use this to identify problematic triggers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "backtest_id": {
                        "type": "integer",
                        "description": "Backtest ID from get_backtest_history"
                    }
                },
                "required": ["backtest_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_trigger_details",
            "description": "Get detailed info for specific triggers. Use this to analyze why certain decisions were made. Supports batch query and field filtering to save tokens.",
            "parameters": {
                "type": "object",
                "properties": {
                    "backtest_id": {
                        "type": "integer",
                        "description": "Backtest ID"
                    },
                    "indexes": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Trigger indexes to query (e.g., [5, 8, 12])"
                    },
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["summary", "input", "output", "queries", "logs"]
                        },
                        "description": "Fields to include. Default all. summary=basic info, input=decision_input, output=decision_output, queries=data_queries, logs=execution_logs"
                    }
                },
                "required": ["backtest_id", "indexes"]
            }
        }
    }
]

# Factor query tool (reused from hyper_ai_tools)
FACTOR_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_factors",
        "description": "Query factor library and effectiveness data. Without symbol: returns factor list with names (for use in data.get_factor(symbol, factor_name, period)). With symbol: returns factor values and effectiveness ranking. Response includes IC, ICIR, win_rate, decay_half_life_hours.",
        "parameters": {
            "type": "object",
            "properties": {
                "exchange": {"type": "string", "enum": ["hyperliquid", "binance"], "description": "Exchange (required)"},
                "symbol": {"type": "string", "description": "Trading symbol (e.g., BTC). If omitted, returns factor library list."},
                "factor_name": {"type": "string", "description": "Specific factor name for detailed info"},
                "forward_period": {"type": "string", "enum": ["1h", "4h", "12h", "24h"], "description": "Forward period for effectiveness (default: 4h)"}
            },
            "required": ["exchange"]
        }
    }
}

# Combine all tools
PROGRAM_TOOLS = PROGRAM_TOOLS + BACKTEST_ANALYSIS_TOOLS + SHARED_SIGNAL_TOOLS + [FACTOR_QUERY_TOOL]


def _call_anthropic_streaming(endpoint: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    """
    Call Anthropic API with streaming to avoid Cloudflare timeout.

    Streaming keeps the connection alive by sending data chunks continuously,
    preventing gateway timeouts (504) from Cloudflare or other proxies.

    Returns: dict with same structure as non-streaming response
        {"content": [...], "stop_reason": "..."}
    """
    # Enable streaming
    payload = payload.copy()
    payload["stream"] = True

    content_blocks = []  # Accumulated content blocks
    current_block = None  # Current block being built
    current_block_index = -1
    stop_reason = None

    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=timeout, stream=True)
    except requests.exceptions.Timeout as e:
        raise Exception(f"Timeout after {timeout}s: {str(e)}")
    except requests.exceptions.ConnectionError as e:
        raise Exception(f"Connection error: {str(e)}")
    except Exception as e:
        raise Exception(f"{type(e).__name__}: {str(e)}")

    if response.status_code != 200:
        # Return error info for caller to handle
        error_body = response.text[:1000] if response.text else "empty response"
        raise Exception(f"HTTP {response.status_code}: {error_body}")

    # Parse SSE stream - use explicit UTF-8 decoding to avoid encoding issues
    for line_bytes in response.iter_lines():
        if not line_bytes:
            continue
        # Decode with UTF-8 explicitly
        line = line_bytes.decode('utf-8')
        if line.startswith("event:"):
            continue  # Skip event type lines, we parse data directly
        if not line.startswith("data:"):
            continue

        data_str = line[5:].strip()  # Remove "data:" prefix
        if data_str == "[DONE]":
            break

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        event_type = data.get("type", "")

        if event_type == "content_block_start":
            # New content block starting
            current_block_index = data.get("index", 0)
            block_data = data.get("content_block", {})
            block_type = block_data.get("type", "")

            if block_type == "text":
                current_block = {"type": "text", "text": ""}
            elif block_type == "thinking":
                current_block = {"type": "thinking", "thinking": ""}
            elif block_type == "tool_use":
                current_block = {
                    "type": "tool_use",
                    "id": block_data.get("id", ""),
                    "name": block_data.get("name", ""),
                    "input": ""  # Will accumulate JSON string, parse at end
                }

        elif event_type == "content_block_delta":
            # Incremental content
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")

            if delta_type == "text_delta" and current_block:
                current_block["text"] += delta.get("text", "")
            elif delta_type == "thinking_delta" and current_block:
                current_block["thinking"] += delta.get("thinking", "")
            elif delta_type == "input_json_delta" and current_block:
                current_block["input"] += delta.get("partial_json", "")

        elif event_type == "content_block_stop":
            # Block complete, add to list
            if current_block:
                # Parse tool_use input from accumulated JSON string
                if current_block.get("type") == "tool_use":
                    input_str = current_block.get("input", "")
                    if input_str:
                        try:
                            current_block["input"] = json.loads(input_str)
                        except json.JSONDecodeError:
                            current_block["input"] = {}
                    else:
                        current_block["input"] = {}
                content_blocks.append(current_block)
                current_block = None

        elif event_type == "message_delta":
            # Message-level delta (contains stop_reason)
            delta = data.get("delta", {})
            stop_reason = delta.get("stop_reason")

    return {
        "content": content_blocks,
        "stop_reason": stop_reason
    }


# Anthropic format tools (pre-converted for efficiency)
PROGRAM_TOOLS_ANTHROPIC = convert_tools_to_anthropic(PROGRAM_TOOLS)


# API Documentation content
MARKET_API_DOCS = """
## MarketData Object (passed to should_trade as 'data')

### Properties (Direct Access)
- data.available_balance: float - Available balance in USD (e.g., 10000.0)
- data.total_equity: float - Total account equity including unrealized PnL (e.g., 10250.5)
- data.used_margin: float - Currently used margin (e.g., 1500.0)
- data.margin_usage_percent: float - Margin usage percentage 0-100 (e.g., 15.0 means 15%)
- data.maintenance_margin: float - Maintenance margin requirement (e.g., 750.0)
- data.trigger_symbol: str - Symbol that triggered this evaluation (empty string "" for scheduled triggers)
- data.trigger_type: str - "signal" or "scheduled"
- data.positions: Dict[str, Position] - Current open positions (keyed by symbol)
- data.recent_trades: List[Trade] - Recent closed trades history (most recent first)
- data.open_orders: List[Order] - Current open orders (TP/SL, limit orders)

### Position Object (from data.positions)
- pos.symbol: str - Trading symbol
- pos.side: str - "long" or "short"
- pos.size: float - Position size
- pos.entry_price: float - Entry price
- pos.unrealized_pnl: float - Unrealized PnL
- pos.leverage: int - Leverage used
- pos.liquidation_price: float - Liquidation price
- pos.opened_at: int or None - Timestamp in milliseconds when position was opened
- pos.opened_at_str: str or None - Human-readable opened time (e.g., "2026-01-15 10:30:00 UTC")
- pos.holding_duration_seconds: float or None - How long position has been held in seconds
- pos.holding_duration_str: str or None - Human-readable duration (e.g., "2h 30m")

### Trade Object (from data.recent_trades)
- trade.symbol: str - Trading symbol (e.g., "BTC")
- trade.side: str - "Long" or "Short"
- trade.size: float - Trade size (e.g., 0.5)
- trade.price: float - Close price (e.g., 95000.0)
- trade.timestamp: int - Close timestamp in milliseconds (e.g., 1736690000000)
- trade.pnl: float - Realized profit/loss in USD (e.g., 125.50)
- trade.close_time: str - Close time in UTC string format (e.g., "2026-01-12 15:30:00 UTC")

### Order Object (from data.open_orders)
- order.order_id: int - Unique order ID (e.g., 12345678)
- order.symbol: str - Trading symbol (e.g., "BTC")
- order.side: str - "Buy" or "Sell"
- order.direction: str - "Open Long", "Open Short", "Close Long", "Close Short"
- order.order_type: str - "Limit", "Stop Limit", "Take Profit Limit"
- order.size: float - Order size (e.g., 0.1)
- order.price: float - Limit price (e.g., 95000.0)
- order.trigger_price: float - Trigger price for stop/TP orders (e.g., 94500.0)
- order.reduce_only: bool - Whether this is a reduce-only order
- order.timestamp: int - Order placement timestamp in milliseconds (e.g., 1736697952000)

### Methods

#### data.get_indicator(symbol: str, indicator: str, period: str) -> dict
Get technical indicator values.
- symbol: "BTC", "ETH", etc.
- indicator: "RSI14", "RSI7", "MA5", "MA10", "MA20", "EMA20", "EMA50", "EMA100", "MACD", "BOLL", "ATR14", "VWAP", "STOCH", "OBV"
- period: "1m", "5m", "15m", "1h", "4h", "1d"
- Returns:
  - RSI/MA/EMA/ATR/VWAP/OBV: {"value": 45.2} (float)
  - MACD: {"macd": 123.5, "signal": 98.2, "histogram": 25.3}
  - BOLL: {"upper": 96500.0, "middle": 95000.0, "lower": 93500.0}
  - STOCH: {"k": 65.3, "d": 58.7}

#### data.get_klines(symbol: str, period: str, count: int = 50) -> list
Get K-line (candlestick) data.
- symbol: "BTC", "ETH", etc.
- period: "1m", "5m", "15m", "1h", "4h", "1d"
- count: Number of candles to return (default 50)
- Returns: List of Kline objects with: timestamp (int seconds), open, high, low, close, volume (all float)

#### data.get_market_data(symbol: str) -> dict
Get complete market data (price, volume, open interest, funding rate).
**Reuses AI Trader's data layer** - same source as {BTC_market_data} variable.
- symbol: "BTC", "ETH", "SOL", etc.
- Returns: Dict with fields:
  - "symbol": "BTC"
  - "price": 95220.0 (mark price)
  - "oracle_price": 95172.0
  - "change24h": 159.0 (USD)
  - "percentage24h": 0.167 (%)
  - "volume24h": 1781547.32 (USD)
  - "open_interest": 10872198.65 (USD)
  - "funding_rate": 0.0000125
- Example: btc_data = data.get_market_data("BTC"); funding = btc_data.get("funding_rate", 0)

**IMPORTANT: Price Access**
- To get current price, use data.get_market_data(symbol) and extract the "price" field
- Example: market_data = data.get_market_data("BTC"); price = market_data.get("price", 0)
- This method returns complete market data (price, volume, OI, funding rate) in one API call
- Do NOT use data.prices (removed) - always use get_market_data() instead

#### data.get_flow(symbol: str, metric: str, period: str) -> dict
Get market flow metrics. All metrics include `last_5` for trend analysis.
- symbol: "BTC", "ETH", etc.
- metric: "CVD", "OI", "OI_DELTA", "TAKER", "FUNDING", "DEPTH", "IMBALANCE"
- period: "1m", "5m", "15m", "1h", "4h", "1d"
- Returns (with real data examples):
  - "CVD": {"current": 14877256.20, "last_5": [...], "cumulative": 17906808.24, "period": "1h"}
  - "OI": {"current": 16826201.53, "last_5": [...], "period": "1h"}
  - "OI_DELTA": {"current": 0.595, "last_5": [...], "period": "1h"} (% change)
  - "TAKER": {"buy": 18915411.13, "sell": 4038154.92, "ratio": 4.684, "ratio_last_5": [...], "volume_last_5": [...], "period": "1h"}
  - "FUNDING": {"current": 11.2, "current_pct": 0.00112, "change": 1.55, "change_pct": 0.000155, "last_5": [...], "annualized": 1.2264, "period": "1h"}
  - "DEPTH": {"bid": 28.34, "ask": 0.04, "ratio": 635.07, "ratio_last_5": [...], "spread": 1.0, "period": "1h"}
  - "IMBALANCE": {"current": 0.997, "last_5": [...], "period": "1h"} (-1 to +1)

#### data.get_regime(symbol: str, period: str) -> RegimeInfo
Get market regime classification.
- symbol: "BTC", "ETH", etc.
- period: "1m", "5m", "15m", "1h", "4h", "1d"
- Returns: RegimeInfo object
  - regime.regime: "breakout", "absorption", "stop_hunt", "exhaustion", "trap", "continuation", "noise"
  - regime.conf: 0.85 (confidence 0.0-1.0)
  - regime.direction: "bullish", "bearish", "neutral"
  - regime.reason: "Strong buying pressure with OI expansion"
  - regime.indicators: {"cvd_ratio": 0.997, "oi_delta": 0.595, "taker_ratio": 4.684, "price_atr": 0.5, "rsi": 55.2}

#### data.get_price_change(symbol: str, period: str) -> dict
Get price change over period.
- symbol: "BTC", "ETH", etc.
- period: "1m", "5m", "15m", "1h", "4h", "1d"
- Returns: {"change_percent": 2.5, "change_usd": 2350.0}

#### data.get_factor(symbol: str, factor_name: str, period: str = "5m") -> dict
Get factor value and effectiveness metrics for a specific K-line period.
- symbol: "BTC", "ETH", etc.
- factor_name: "RSI21", "MOM10", "VOL_RATIO", or any custom factor name
- period: "1m", "5m", "15m", "1h", "4h", "1d" (explicit for new code; omitted defaults to 5m for backward compatibility)
- Returns: {"factor_name": "RSI21", "symbol": "BTC", "period": "1h", "id": 5, "expression": "RSI(close, 21)", "description": "...", "category": "momentum", "value": 0.0234, "ic": 0.05, "icir": 1.35, "win_rate": 58.2, "decay_half_life_hours": -1}
- decay_half_life_hours: -1=persistent, positive=half-life hours, None=insufficient data
- Use `query_factors` tool to see all available factor names

#### data.get_factor_ranking(symbol: str, top_n: int = 10) -> list
Get top factors ranked by |ICIR| (most reliable first).
- Returns: [{"factor_name": "SKEW20", "id": 12, "expression": "SKEW(RET(close,1),20)", "description": "...", "ic": -0.08, "icir": -2.1, "win_rate": 62.0, "decay_half_life_hours": -1}, ...]
- Not available in backtest mode

### Available in Sandbox
- time: Pre-injected sandbox object, use `time.time()` directly without importing
- math: Pre-injected sandbox object, use `math.sqrt()` / `math.log()` / `math.exp()` directly without importing
- log(message): Debug output function
"""

DECISION_API_DOCS = """
## Decision Object (return from should_trade)

Your should_trade method must return a Decision object:

```python
# For BUY (open long position):
return Decision(
    operation="buy",                    # Required: "buy", "sell", "close", "hold"
    symbol="BTC",                       # Required: Trading symbol
    target_portion_of_balance=0.5,      # Required: 0.1-1.0 (portion of balance to use)
    leverage=10,                        # Required: 1-50
    max_price=95000.0,                  # Required for buy: maximum entry price
    time_in_force="Ioc",                # Optional: "Ioc", "Gtc", "Alo" (default: "Ioc")
    take_profit_price=100000.0,         # Optional: TP trigger price
    stop_loss_price=90000.0,            # Optional: SL trigger price
    tp_execution="limit",               # Optional: "market" or "limit" (default: "limit")
    sl_execution="limit",               # Optional: "market" or "limit" (default: "limit")
    reason="RSI oversold",              # Optional: Reason for decision
    trading_strategy="Entry thesis..."  # Optional: Strategy description
)

# For SELL (open short position):
return Decision(
    operation="sell",
    symbol="BTC",
    target_portion_of_balance=0.5,
    leverage=10,
    min_price=95000.0,                  # Required for sell: minimum entry price
    ...
)

# For CLOSE (close existing position):
return Decision(
    operation="close",
    symbol="BTC",
    target_portion_of_balance=1.0,      # Portion of position to close
    leverage=10,
    min_price=95000.0,                  # Required for closing LONG position
    # OR max_price=95000.0,             # Required for closing SHORT position
    ...
)

# For HOLD (no action):
return Decision(operation="hold", symbol="BTC", reason="No trade condition")
```

### Operation Types
- "buy" - Open long position (requires max_price)
- "sell" - Open short position (requires min_price)
- "close" - Close existing position (requires min_price for long, max_price for short)
- "hold" - No action

### Decision Fields
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| operation | str | Yes | - | "buy", "sell", "close", "hold" |
| symbol | str | Yes | - | Trading symbol (e.g., "BTC") |
| target_portion_of_balance | float | For buy/sell/close | 0.0 | 0.1-1.0 |
| leverage | int | For buy/sell/close | 10 | 1-50 |
| max_price | float | For buy/close short | None | Maximum entry price |
| min_price | float | For sell/close long | None | Minimum entry price |
| time_in_force | str | No | "Ioc" | "Ioc", "Gtc", "Alo" |
| take_profit_price | float | No | None | TP trigger price |
| stop_loss_price | float | No | None | SL trigger price |
| tp_execution | str | No | "limit" | "market" or "limit" |
| sl_execution | str | No | "limit" | "market" or "limit" |
| reason | str | No | "" | Reason for decision |
| trading_strategy | str | No | "" | Entry thesis, risk controls |

### Time In Force Options
- "Ioc" (Immediate or Cancel): Fill immediately or cancel unfilled portion
- "Gtc" (Good Till Cancel): Order stays in orderbook until filled or cancelled
- "Alo" (Add Liquidity Only): Maker-only order, rejected if would take liquidity
"""


def _query_market_data(db: Session, symbol: str, period: str, exchange: str = "hyperliquid") -> str:
    """Query current market data for AI to understand indicator value ranges.

    Args:
        db: Database session
        symbol: Trading symbol (e.g., BTC, ETH)
        period: Time period for indicators (e.g., 1h, 5m)
        exchange: Exchange to query from ('hyperliquid' or 'binance')
    """
    try:
        from program_trader.data_provider import DataProvider
        import requests

        # Get current price based on exchange
        if exchange == "binance":
            # Use Binance public API to get price
            binance_symbol = f"{symbol.upper()}USDT"
            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/price",
                params={"symbol": binance_symbol},
                timeout=5
            )
            if resp.status_code == 200:
                price = float(resp.json().get("price", 0))
            else:
                price = None
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
            "regime": {"regime": regime.regime, "confidence": regime.conf}
        }

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _get_backtest_history(db: Session, program_id: Optional[int], user_id: int, limit: int = 10) -> str:
    """Get backtest history for the current program."""
    try:
        if not program_id:
            return json.dumps({"error": "No program selected. This tool only works when editing an existing program."})

        # Find all bindings for this program (a program can have multiple bindings)
        bindings = db.query(AccountProgramBinding).filter(
            AccountProgramBinding.program_id == program_id,
            AccountProgramBinding.is_deleted != True
        ).all()

        if not bindings:
            return json.dumps({"error": "No binding found for this program. Run a backtest first."})

        binding_ids = [b.id for b in bindings]

        # Get backtest history from all bindings
        backtests = db.query(BacktestResult).filter(
            BacktestResult.binding_id.in_(binding_ids),
            BacktestResult.status == "completed"
        ).order_by(BacktestResult.created_at.desc()).limit(limit).all()

        if not backtests:
            return json.dumps({"error": "No backtest results found. Run a backtest first."})

        results = []
        for bt in backtests:
            results.append({
                "id": bt.id,
                "time_range": f"{bt.start_time.strftime('%Y-%m-%d %H:%M') if bt.start_time else 'N/A'} ~ {bt.end_time.strftime('%Y-%m-%d %H:%M') if bt.end_time else 'N/A'}",
                "initial_balance": bt.initial_balance,
                "final_equity": round(bt.final_equity, 2) if bt.final_equity else 0,
                "total_pnl": round(bt.total_pnl, 2) if bt.total_pnl else 0,
                "total_pnl_percent": round(bt.total_pnl_percent, 2) if bt.total_pnl_percent else 0,
                "max_drawdown_percent": round(bt.max_drawdown_percent, 2) if bt.max_drawdown_percent else 0,
                "total_triggers": bt.total_triggers,
                "total_trades": bt.total_trades,  # Closed trades count
                "winning_trades": bt.winning_trades,  # TP count
                "losing_trades": bt.losing_trades,  # SL count
                "win_rate": round(bt.win_rate, 2) if bt.win_rate else 0,  # Already 0-100 scale
                "profit_factor": round(bt.profit_factor, 2) if bt.profit_factor else 0,
                "created_at": bt.created_at.strftime('%Y-%m-%d %H:%M') if bt.created_at else None
            })

        return json.dumps({
            "note": "Use these official stats directly. Do NOT recalculate from trigger list.",
            "backtests": results
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _get_trigger_list(db: Session, backtest_id: int) -> str:
    """Get trigger summary list for a backtest."""
    try:
        triggers = db.query(BacktestTriggerLog).filter(
            BacktestTriggerLog.backtest_id == backtest_id
        ).order_by(BacktestTriggerLog.trigger_index).all()

        if not triggers:
            return json.dumps({"error": f"No triggers found for backtest {backtest_id}"})

        results = []
        for t in triggers:
            pnl = t.realized_pnl or 0
            results.append({
                "index": t.trigger_index,
                "time": t.trigger_time.strftime('%Y-%m-%d %H:%M:%S') if t.trigger_time else None,
                "type": t.trigger_type,
                "symbol": t.symbol,
                "action": t.decision_action,
                "side": t.decision_side,
                "size": round(t.decision_size, 4) if t.decision_size else None,
                "equity": f"${t.equity_before:.2f} -> ${t.equity_after:.2f}" if t.equity_before and t.equity_after else None,
                "pnl": round(pnl, 2) if pnl != 0 else None,
                "reason": t.decision_reason[:80] if t.decision_reason else None
            })

        return json.dumps({"total": len(results), "triggers": results}, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _get_trigger_details(db: Session, backtest_id: int, indexes: List[int], fields: List[str] = None) -> str:
    """Get detailed info for specific triggers."""
    try:
        if not indexes:
            return json.dumps({"error": "indexes is required"})

        # Default to all fields
        if not fields:
            fields = ["summary", "input", "output", "queries", "logs"]

        triggers = db.query(BacktestTriggerLog).filter(
            BacktestTriggerLog.backtest_id == backtest_id,
            BacktestTriggerLog.trigger_index.in_(indexes)
        ).order_by(BacktestTriggerLog.trigger_index).all()

        if not triggers:
            return json.dumps({"error": f"No triggers found for indexes {indexes}"})

        results = []
        for t in triggers:
            detail = {"index": t.trigger_index}

            if "summary" in fields:
                detail["summary"] = {
                    "time": t.trigger_time.strftime('%Y-%m-%d %H:%M:%S') if t.trigger_time else None,
                    "type": t.trigger_type,
                    "symbol": t.symbol,
                    "action": t.decision_action,
                    "side": t.decision_side,
                    "size": t.decision_size,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "equity_before": t.equity_before,
                    "equity_after": t.equity_after,
                    "unrealized_pnl": t.unrealized_pnl,
                    "realized_pnl": t.realized_pnl,
                    "fee": t.fee,
                    "reason": t.decision_reason
                }

            if "input" in fields and t.decision_input:
                try:
                    detail["input"] = json.loads(t.decision_input)
                except:
                    detail["input"] = t.decision_input

            if "output" in fields and t.decision_output:
                try:
                    detail["output"] = json.loads(t.decision_output)
                except:
                    detail["output"] = t.decision_output

            if "queries" in fields and t.data_queries:
                try:
                    detail["queries"] = json.loads(t.data_queries)
                except:
                    detail["queries"] = t.data_queries

            if "logs" in fields and t.execution_logs:
                try:
                    detail["logs"] = json.loads(t.execution_logs)
                except:
                    detail["logs"] = t.execution_logs

            results.append(detail)

        return json.dumps({"triggers": results}, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _quick_verify_strategy(
    db: Session,
    code: str,
    exchange: str,
    signal_pool_id: Optional[int] = None,
    scheduled_interval_minutes: Optional[int] = None,
    symbol: str = "BTC",
    hours: int = 168
) -> str:
    """
    Quick verify strategy code on historical data without storing results.
    Reuses ProgramBacktestEngine for accurate simulation.
    Returns core metrics from BacktestResult for AI analysis.
    """
    from backtest import BacktestConfig, ProgramBacktestEngine
    from datetime import datetime, timezone

    try:
        # Must have at least one trigger source
        if signal_pool_id is None and scheduled_interval_minutes is None:
            return json.dumps({"error": "Must specify signal_pool_id and/or scheduled_interval_minutes"})

        # Calculate time range (UTC)
        end_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time_ms = end_time_ms - (hours * 60 * 60 * 1000)

        # Build config - support combined triggers
        signal_pool_ids = [signal_pool_id] if signal_pool_id else []
        scheduled_interval_sec = scheduled_interval_minutes * 60 if scheduled_interval_minutes else None

        # Get symbols from signal pool if available
        symbols = [symbol]
        if signal_pool_id:
            from database.models import SignalPool
            pool = db.query(SignalPool).filter(SignalPool.id == signal_pool_id, SignalPool.is_deleted != True).first()
            if pool and pool.symbols:
                pool_symbols = pool.symbols
                if isinstance(pool_symbols, str):
                    pool_symbols = json.loads(pool_symbols)
                if pool_symbols:
                    symbols = pool_symbols

        config = BacktestConfig(
            code=code,
            signal_pool_ids=signal_pool_ids,
            symbols=symbols,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            scheduled_interval_sec=scheduled_interval_sec,
            initial_balance=10000.0,
            slippage_percent=0.05,
            fee_rate=0.035,
            exchange=exchange,
        )

        # Run backtest using existing engine (no DB storage)
        engine = ProgramBacktestEngine(db)
        result = engine.run(config)

        if not result.success:
            return json.dumps({"error": result.error or "Backtest failed"})

        # Extract sample trades (max 3)
        sample_trades = []
        for trade in result.trades[:3]:
            time_str = datetime.utcfromtimestamp(trade.timestamp / 1000).strftime('%Y-%m-%d %H:%M')
            sample_trades.append({
                "time": time_str,
                "action": trade.operation,
                "symbol": trade.symbol,
                "side": trade.side,
                "pnl": round(trade.pnl, 2) if trade.pnl else None,
                "reason": trade.reason[:50] if trade.reason else ''
            })

        # Return core metrics from BacktestResult
        return json.dumps({
            "success": True,
            "duration_hours": hours,
            "exchange": exchange,
            "trigger_config": {
                "signal_pool_id": signal_pool_id,
                "scheduled_interval_minutes": scheduled_interval_minutes
            },
            "triggers": {
                "total": result.total_triggers,
                "signal": result.signal_triggers,
                "scheduled": result.scheduled_triggers
            },
            "performance": {
                "total_pnl": round(result.total_pnl, 2),
                "total_pnl_percent": round(result.total_pnl_percent, 2),
                "max_drawdown_percent": round(result.max_drawdown_percent, 2),
                "sharpe_ratio": round(result.sharpe_ratio, 2) if result.sharpe_ratio else None
            },
            "trades": {
                "total": result.total_trades,
                "winning": result.winning_trades,
                "losing": result.losing_trades,
                "win_rate": round(result.win_rate, 1),
                "profit_factor": round(result.profit_factor, 2) if result.profit_factor else None
            },
            "sample_trades": sample_trades
        })

    except Exception as e:
        logger.error(f"Quick verify strategy error: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


def _execute_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    db: Session,
    program_id: Optional[int],
    user_id: int
) -> str:
    """Execute a tool and return result as string."""
    try:
        if tool_name == "query_market_data":
            symbol = arguments.get("symbol", "BTC")
            period = arguments.get("period", "1h")
            exchange = arguments.get("exchange", "hyperliquid")
            return _query_market_data(db, symbol, period, exchange)

        elif tool_name == "get_api_docs":
            api_type = arguments.get("api_type", "all")
            if api_type == "market":
                return MARKET_API_DOCS
            elif api_type == "decision":
                return DECISION_API_DOCS
            else:
                return MARKET_API_DOCS + "\n" + DECISION_API_DOCS

        elif tool_name == "get_current_code":
            if program_id:
                program = db.query(TradingProgram).filter(
                    TradingProgram.id == program_id,
                    TradingProgram.user_id == user_id,
                    TradingProgram.is_deleted != True
                ).first()
                if program:
                    return f"Current program: {program.name}\n\n```python\n{program.code}\n```"
            return "No existing code. This is a new program."

        elif tool_name == "validate_code":
            code = arguments.get("code", "")
            return _validate_python_code(code)

        elif tool_name == "test_run_code":
            code = arguments.get("code", "")
            symbol = arguments.get("symbol", "BTC")
            return _test_run_code(db, code, symbol)

        elif tool_name == "quick_verify_strategy":
            code = arguments.get("code", "")
            exchange = arguments.get("exchange", "hyperliquid")
            signal_pool_id = arguments.get("signal_pool_id")
            scheduled_interval_minutes = arguments.get("scheduled_interval_minutes")
            symbol = arguments.get("symbol", "BTC")
            hours = arguments.get("hours", 168)
            return _quick_verify_strategy(
                db, code, exchange,
                signal_pool_id, scheduled_interval_minutes, symbol, hours
            )

        elif tool_name == "suggest_save_code":
            code = arguments.get("code", "")
            name = arguments.get("name", "Untitled Program")
            description = arguments.get("description", "")
            # Return suggestion format - frontend will show confirmation dialog
            return json.dumps({
                "type": "save_suggestion",
                "code": code,
                "name": name,
                "description": description,
                "message": "Code ready to save. User confirmation required."
            })

        elif tool_name == "get_signal_pools":
            exchange = arguments.get("exchange", "all")
            return execute_get_signal_pools(db, exchange)

        elif tool_name == "run_signal_backtest":
            pool_id = arguments.get("pool_id")
            if pool_id is None:
                return json.dumps({"error": "pool_id is required"})
            symbol = arguments.get("symbol", "BTC")
            hours = arguments.get("hours", 24)
            return execute_run_signal_backtest(db, pool_id, symbol, hours)

        # Backtest analysis tools
        elif tool_name == "get_backtest_history":
            limit = arguments.get("limit", 10)
            return _get_backtest_history(db, program_id, user_id, limit)

        elif tool_name == "get_trigger_list":
            backtest_id = arguments.get("backtest_id")
            if backtest_id is None:
                return json.dumps({"error": "backtest_id is required"})
            return _get_trigger_list(db, backtest_id)

        elif tool_name == "get_trigger_details":
            backtest_id = arguments.get("backtest_id")
            indexes = arguments.get("indexes", [])
            fields = arguments.get("fields")
            if backtest_id is None:
                return json.dumps({"error": "backtest_id is required"})
            return _get_trigger_details(db, backtest_id, indexes, fields)

        elif tool_name == "query_factors":
            from services.hyper_ai_tools import execute_query_factors
            exchange = arguments.get("exchange", "hyperliquid")
            symbol = arguments.get("symbol")
            factor_name = arguments.get("factor_name")
            forward_period = arguments.get("forward_period", "4h")
            return execute_query_factors(db, exchange, symbol, factor_name, forward_period)

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        logger.error(f"Tool execution error: {tool_name} - {e}")
        return f"Error executing {tool_name}: {str(e)}"


def _format_tool_calls_log(tool_calls_log: List[Dict], reasoning_snapshot: str) -> str:
    """Format tool calls log and reasoning as Markdown for storage and display.

    Interleaves reasoning and tool calls by round number for better readability.
    """
    if not tool_calls_log and not reasoning_snapshot:
        return ""

    lines = ["<details>", "<summary>Analysis Process</summary>", ""]

    # Parse reasoning by rounds into a dict
    reasoning_by_round = {}
    if reasoning_snapshot:
        rounds = reasoning_snapshot.split("\n[Round ")
        for round_text in rounds:
            if not round_text.strip():
                continue
            if round_text.startswith("[Round "):
                round_text = round_text[7:]
            parts = round_text.split("]\n", 1)
            if len(parts) == 2:
                try:
                    round_num = int(parts[0])
                    content = parts[1].strip()
                    if len(content) > 500:
                        content = content[:500] + "..."
                    content = content.replace("```", "'''")
                    reasoning_by_round[round_num] = content
                except ValueError:
                    pass

    # Determine max round from both sources
    max_round = 0
    if reasoning_by_round:
        max_round = max(max_round, max(reasoning_by_round.keys()))
    if tool_calls_log:
        max_round = max(max_round, len(tool_calls_log))

    # Interleave by round
    tool_idx = 0
    for round_num in range(1, max_round + 1):
        # Add reasoning for this round if exists
        if round_num in reasoning_by_round:
            lines.append(f"**Round {round_num} - Reasoning:**")
            lines.append(f"> {reasoning_by_round[round_num]}")
            lines.append("")

        # Add tool call for this round if exists
        if tool_idx < len(tool_calls_log):
            entry = tool_calls_log[tool_idx]
            tool_name = entry.get("tool", "unknown")
            args = entry.get("args", {})
            result = entry.get("result", "")

            lines.append(f"**Round {round_num} - Tool: `{tool_name}`**")
            # Include all arguments except code in one line
            args_str = ", ".join(f"{k}={v}" for k, v in args.items() if k != "code")
            if args_str:
                lines.append(f"- Arguments: {args_str}")
            # Include code separately in a code block for full context
            if "code" in args:
                code_content = args["code"]
                lines.append("- Code:")
                lines.append("```python")
                lines.append(code_content)
                lines.append("```")
            result_preview = result[:200] + "..." if len(result) > 200 else result
            result_preview = result_preview.replace("```", "'''").replace("\n", " ")
            lines.append(f"- Result: {result_preview}")
            lines.append("")
            tool_idx += 1

    lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def _validate_python_code(code: str) -> str:
    """Validate Python code using system validator."""
    from program_trader import validate_strategy_code

    result = validate_strategy_code(code)
    if result.is_valid:
        if result.warnings:
            return f"Syntax OK. Warnings: {'; '.join(result.warnings)}"
        return "Syntax OK. Code structure is valid."
    else:
        return f"Validation failed: {'; '.join(result.errors)}"


def _test_run_code(db: Session, code: str, symbol: str) -> str:
    """Test run code with real market data."""
    try:
        from program_trader.executor import SandboxExecutor
        from program_trader.models import MarketData
        from program_trader.data_provider import DataProvider

        # Create data provider with test account
        # Note: account_id=0 has no real wallet, so we use simulated account data
        # Strategy code can still call data_provider methods to get market data
        data_provider = DataProvider(db=db, account_id=0, environment="mainnet")

        # Create MarketData object with simulated account data
        market_data = MarketData(
            available_balance=10000.0,  # Simulated balance for testing
            total_equity=10000.0,
            used_margin=0.0,
            margin_usage_percent=0.0,
            maintenance_margin=0.0,
            positions={},  # No positions in test mode
            trigger_symbol=symbol,
            trigger_type="signal",
            _data_provider=data_provider,
        )

        # Create executor and run
        executor = SandboxExecutor(timeout_seconds=10)
        result = executor.execute(code, market_data, {})

        if result.success:
            decision = result.decision
            # Handle both old (action) and new (operation) Decision formats
            action_str = "none"
            if decision:
                if hasattr(decision, 'operation'):
                    action_str = decision.operation
                elif hasattr(decision, 'action'):
                    action_str = decision.action.value if hasattr(decision.action, 'value') else str(decision.action)
            return json.dumps({
                "success": True,
                "decision": decision.to_dict() if decision else None,
                "message": f"Test passed! Decision: {action_str}"
            }, indent=2)
        else:
            return json.dumps({
                "success": False,
                "error_type": "ExecutionError",
                "error": result.error,
            }, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error_type": type(e).__name__,
            "error": str(e),
            "traceback": traceback.format_exc()[:500]
        }, indent=2)


def generate_program_with_ai_stream(
    db: Session,
    account_id: Optional[int] = None,
    user_message: str = "",
    conversation_id: Optional[int] = None,
    program_id: Optional[int] = None,
    user_id: int = 1,
    llm_config: Optional[Dict[str, Any]] = None
) -> Generator[str, None, None]:
    """
    Generate program code using AI with SSE streaming.
    Yields SSE events for real-time updates.
    """
    import requests

    start_time = time.time()
    request_id = f"program_gen_{int(start_time)}"

    logger.info(f"[AI Program {request_id}] Starting: account_id={account_id}, "
                f"conversation_id={conversation_id}, program_id={program_id}")

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
            account = None
        else:
            # Original logic: get from AI account
            account = db.query(Account).filter(
                Account.id == account_id,
                Account.account_type == "AI",
                Account.is_deleted != True
            ).first()

            if not account:
                yield format_sse_event("error", {"content": "AI account not found"})
                return

            api_config = {
                "base_url": account.base_url,
                "api_key": account.api_key,
                "model": account.model,
                "api_format": detect_api_format(account.base_url)[1] or "openai"
            }

        # Get or create conversation
        conversation = None
        if conversation_id:
            conversation = db.query(AiProgramConversation).filter(
                AiProgramConversation.id == conversation_id,
                AiProgramConversation.user_id == user_id
            ).first()

        if not conversation:
            title = user_message[:50] + "..." if len(user_message) > 50 else user_message
            conversation = AiProgramConversation(
                user_id=user_id,
                program_id=program_id,
                title=title
            )
            db.add(conversation)
            db.flush()
            yield format_sse_event("conversation_created", {"conversation_id": conversation.id})

        # Save user message
        user_msg = AiProgramMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_message
        )
        db.add(user_msg)
        db.flush()

        # Build dynamic system prompt based on edit/new mode
        system_prompt = PROGRAM_SYSTEM_PROMPT
        if program_id:
            # Edit mode - add context about the program being edited
            program = db.query(TradingProgram).filter(
                TradingProgram.id == program_id,
                TradingProgram.user_id == user_id,
                TradingProgram.is_deleted != True
            ).first()
            if program:
                system_prompt += f"""
You are editing an existing program:
- **Program ID**: {program.id}
- **Program Name**: {program.name}
- **Description**: {program.description or 'No description'}

**IMPORTANT**: Before making any changes, you MUST first call `get_current_code` to understand the existing implementation. Then modify the code based on user's requirements while preserving the overall structure unless explicitly asked to rewrite.
"""
            else:
                system_prompt += """

## CURRENT CONTEXT
You are creating a new program. Start fresh and design the strategy based on user's requirements.
"""
        else:
            system_prompt += """

## CURRENT CONTEXT
You are creating a new program. Start fresh and design the strategy based on user's requirements.
"""

        # Build message history with compression support
        from services.ai_context_compression_service import (
            compress_messages, update_compression_points,
            restore_tool_calls_to_messages,
            get_last_compression_point, filter_messages_by_compression,
        )

        messages = [{"role": "system", "content": system_prompt}]

        # Check compression points - inject summary for compressed messages
        cp = get_last_compression_point(conversation)
        if cp and cp.get("summary"):
            messages.append({
                "role": "system",
                "content": f"[Previous conversation summary]\n{cp['summary']}"
            })

        # Load history, filter by compression point
        history = db.query(AiProgramMessage).filter(
            AiProgramMessage.conversation_id == conversation.id,
            AiProgramMessage.id != user_msg.id
        ).order_by(AiProgramMessage.created_at).limit(100).all()

        history = filter_messages_by_compression(history, cp)

        last_message_id = history[-1].id if history else None

        # Restore tool_calls into proper LLM message format
        history_dicts = [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls_log": m.tool_calls_log,
                "reasoning_snapshot": m.reasoning_snapshot,
            }
            for m in history
        ]
        restored = restore_tool_calls_to_messages(history_dicts, api_config.get("api_format", "openai"), model=api_config.get("model", ""))
        messages.extend(restored)

        messages.append({"role": "user", "content": user_message})

        # Apply compression if needed (api_config already set above)
        result = compress_messages(messages, api_config, db=db)
        messages = result["messages"]

        # Update compression_points if compression occurred
        if result["compressed"] and result["summary"] and last_message_id:
            update_compression_points(
                conversation, last_message_id,
                result["summary"], result["compressed_at"], db
            )

        # Detect API format and build endpoints
        endpoint, api_format = detect_api_format(api_config["base_url"])
        if not endpoint:
            yield format_sse_event("error", {"content": "Invalid API configuration"})
            return

        # For OpenAI format, use fallback endpoints; for Anthropic, use single endpoint
        if api_format == 'anthropic':
            endpoints = [endpoint]
        else:
            endpoints = build_chat_completion_endpoints(api_config["base_url"], api_config["model"])
            if not endpoints:
                yield format_sse_event("error", {"content": "Invalid API configuration"})
                return
        # Use unified headers builder (see build_llm_headers in ai_decision_service)
        headers = build_llm_headers(api_format, api_config["api_key"], api_config["base_url"])

        # Tool calling loop
        max_rounds = 15
        tool_round = 0
        tool_calls_log = []
        final_content = ""
        reasoning_snapshot = ""
        code_suggestion = None

        # For Anthropic, we need to track tool_use blocks separately
        anthropic_tool_use_blocks = []

        # Create assistant message upfront with is_complete=False for retry support
        assistant_msg = AiProgramMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="",  # Will be updated each round
            is_complete=False  # Mark as incomplete until done
        )
        db.add(assistant_msg)
        db.flush()

        while tool_round < max_rounds:
            tool_round += 1
            is_last = tool_round == max_rounds

            yield format_sse_event("tool_round", {"round": tool_round, "max": max_rounds})

            # Use unified payload builder (see build_llm_payload in ai_decision_service)
            if api_format == 'anthropic':
                system_prompt, anthropic_messages = convert_messages_to_anthropic(messages)
                tools_for_round = PROGRAM_TOOLS_ANTHROPIC if not is_last else None
                payload = build_llm_payload(
                    model=api_config["model"],
                    messages=[{"role": "system", "content": system_prompt}] + anthropic_messages,
                    api_format=api_format,
                    tools=tools_for_round,
                )
            else:
                tools_for_round = PROGRAM_TOOLS if not is_last else None
                payload = build_llm_payload(
                    model=api_config["model"],
                    messages=messages,
                    api_format=api_format,
                    tools=tools_for_round,
                    tool_choice="auto" if not is_last else None,
                )

            # Call API
            logger.info(f"[AI Program {request_id}] Round {tool_round}: Calling API with {len(endpoints)} endpoints, format={api_format}")
            if api_format == 'anthropic' and tool_round > 1:
                # Debug: log the converted messages for troubleshooting (warning level to ensure visibility)
                logger.warning(f"[AI Program {request_id}] Anthropic round {tool_round} payload messages count: {len(payload.get('messages', []))}")
                for i, m in enumerate(payload.get('messages', [])):
                    role = m.get('role', 'unknown')
                    content = m.get('content', '')
                    if isinstance(content, list):
                        content_summary = f"[{len(content)} blocks: {[b.get('type', '?') for b in content]}]"
                    else:
                        content_summary = f"str({len(str(content))} chars)"
                    logger.warning(f"[AI Program {request_id}]   msg[{i}]: role={role}, content={content_summary}")

            # API call with retry logic
            response = None
            resp_json = None  # For Anthropic streaming, we get parsed result directly
            last_error = None
            last_status_code = None
            last_response_text = None  # Store full response text for error logging

            for retry_attempt in range(API_MAX_RETRIES):
                response = None
                resp_json = None
                # Don't reset last_error here - preserve error from previous attempts

                for endpoint in endpoints:
                    try:
                        logger.info(f"[AI Program {request_id}] Trying endpoint: {endpoint}" +
                                   (f" (retry {retry_attempt + 1}/{API_MAX_RETRIES})" if retry_attempt > 0 else ""))

                        if api_format == 'anthropic':
                            # Use streaming for Anthropic to avoid Cloudflare timeout
                            resp_json = _call_anthropic_streaming(endpoint, payload, headers, timeout=180)
                            logger.info(f"[AI Program {request_id}] Anthropic streaming response received")
                            break  # Success
                        else:
                            # OpenAI format - use regular request
                            response = requests.post(endpoint, json=payload, headers=headers, timeout=120)
                            last_status_code = response.status_code
                            last_response_text = response.text[:2000] if response.text else None
                            logger.info(f"[AI Program {request_id}] Response status: {response.status_code}")
                            if response.status_code != 200:
                                last_error = f"HTTP {response.status_code}"
                                logger.warning(f"[AI Program {request_id}] Non-200 response from {endpoint}: {response.status_code} - {response.text[:500]}")
                            if response.status_code == 200:
                                break
                    except requests.exceptions.Timeout as e:
                        last_error = f"Timeout after 120s: {str(e)}"
                        logger.warning(f"[AI Program {request_id}] Endpoint {endpoint} timeout: {e}")
                    except requests.exceptions.ConnectionError as e:
                        last_error = f"Connection error: {str(e)}"
                        logger.warning(f"[AI Program {request_id}] Endpoint {endpoint} connection error: {e}")
                    except Exception as e:
                        last_error = f"{type(e).__name__}: {str(e)}"
                        logger.warning(f"[AI Program {request_id}] Endpoint {endpoint} error: {type(e).__name__}: {e}")

                # Check if successful
                if api_format == 'anthropic' and resp_json:
                    break  # Anthropic streaming succeeded
                if api_format != 'anthropic' and response and response.status_code == 200:
                    break

                # Check if should retry
                if not _should_retry_api(last_status_code, last_error):
                    logger.info(f"[AI Program {request_id}] Error not retryable, giving up")
                    break

                # Check if more retries available
                if retry_attempt < API_MAX_RETRIES - 1:
                    delay = _get_retry_delay(retry_attempt)
                    logger.warning(f"[AI Program {request_id}] Retrying in {delay:.1f}s (attempt {retry_attempt + 2}/{API_MAX_RETRIES})")
                    yield format_sse_event("retry", {"attempt": retry_attempt + 2, "max_retries": API_MAX_RETRIES})
                    time.sleep(delay)

            # Check for failure - build comprehensive error detail
            if api_format == 'anthropic':
                if not resp_json:
                    error_parts = []
                    if last_error:
                        error_parts.append(f"error={last_error}")
                    if last_status_code:
                        error_parts.append(f"status={last_status_code}")
                    if last_response_text:
                        error_parts.append(f"response={last_response_text[:500]}")
                    error_detail = "; ".join(error_parts) if error_parts else "No response from API"
                    logger.error(f"[AI Program {request_id}] API failed at round {tool_round}: {error_detail}")

                    if tool_calls_log:
                        assistant_msg.content = f"**[Interrupted at round {tool_round}]** {error_detail}"
                        assistant_msg.tool_calls_log = json.dumps(tool_calls_log)
                        assistant_msg.reasoning_snapshot = reasoning_snapshot if reasoning_snapshot else None
                        assistant_msg.is_complete = False
                        assistant_msg.interrupt_reason = f"Round {tool_round}: {error_detail}"
                        db.commit()
                        yield format_sse_event("interrupted", {"message_id": assistant_msg.id, "round": tool_round, "error": error_detail})
                    else:
                        db.delete(assistant_msg)
                        db.commit()
                        yield format_sse_event("error", {"content": f"API request failed: {error_detail}"})
                    return
            else:
                if not response or response.status_code != 200:
                    error_parts = []
                    if last_error:
                        error_parts.append(f"error={last_error}")
                    if last_status_code:
                        error_parts.append(f"status={last_status_code}")
                    if last_response_text:
                        error_parts.append(f"response={last_response_text[:500]}")
                    elif response and response.text:
                        error_parts.append(f"response={response.text[:500]}")
                    error_detail = "; ".join(error_parts) if error_parts else "No response from API"
                    logger.error(f"[AI Program {request_id}] API failed at round {tool_round}: {error_detail}")

                    if tool_calls_log:
                        assistant_msg.content = f"**[Interrupted at round {tool_round}]** {error_detail}"
                        assistant_msg.tool_calls_log = json.dumps(tool_calls_log)
                        assistant_msg.reasoning_snapshot = reasoning_snapshot if reasoning_snapshot else None
                        assistant_msg.is_complete = False
                        assistant_msg.interrupt_reason = f"Round {tool_round}: {error_detail}"
                        db.commit()
                        yield format_sse_event("interrupted", {"message_id": assistant_msg.id, "round": tool_round, "error": error_detail})
                    else:
                        db.delete(assistant_msg)
                        db.commit()
                        yield format_sse_event("error", {"content": f"API request failed: {error_detail}"})
                    return
                resp_json = response.json()

            # Parse response based on API format
            if api_format == 'anthropic':
                # Anthropic response format
                content_blocks = resp_json.get("content", [])
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

                if reasoning_content:
                    reasoning_snapshot += f"\n[Round {tool_round}]\n{reasoning_content}"
                    yield format_sse_event("reasoning", {"content": reasoning_content[:500]})

                # Strip <thinking> text tags from content
                content, tag_thinking = strip_thinking_tags(content)
                if tag_thinking and not reasoning_content:
                    reasoning_content = tag_thinking
                    reasoning_snapshot += f"\n[Round {tool_round}]\n{tag_thinking}"

                if tool_uses:
                    # Store the raw content blocks for message history
                    assistant_msg_dict = {
                        "role": "assistant",
                        "content": content,
                        "tool_use_blocks": content_blocks  # Store for conversion
                    }
                    messages.append(assistant_msg_dict)

                    for tu in tool_uses:
                        fn_name = tu.get("name", "")
                        fn_args = tu.get("input", {})
                        tool_use_id = tu.get("id", "")

                        # Handle empty string input (some proxies return "" instead of {})
                        if fn_args == "":
                            fn_args = {}

                        yield format_sse_event("tool_call", {"name": fn_name, "args": fn_args})

                        result = _execute_tool(fn_name, fn_args, db, program_id, user_id)
                        tool_calls_log.append({"tool": fn_name, "args": fn_args, "result": result[:1000]})

                        # Check for save suggestion
                        if fn_name == "suggest_save_code":
                            try:
                                suggestion = json.loads(result)
                                if suggestion.get("type") == "save_suggestion":
                                    code_suggestion = json.dumps({
                                        "code": suggestion.get("code", ""),
                                        "name": suggestion.get("name", ""),
                                        "description": suggestion.get("description", "")
                                    })
                                    yield format_sse_event("save_suggestion", {"data": suggestion})
                            except:
                                pass

                        yield format_sse_event("tool_result", {"name": fn_name, "result": result[:500]})

                        # Add tool result in OpenAI format (will be converted for Anthropic)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": result
                        })
                else:
                    # No tool calls - final response
                    final_content = content or ""
                    yield format_sse_event("content", {"content": final_content})
                    break
            else:
                # OpenAI format response
                message = resp_json["choices"][0]["message"]
                tool_calls = message.get("tool_calls", [])
                reasoning_content = message.get("reasoning_content", "")
                content = message.get("content", "")

                # Extract reasoning (for DeepSeek Reasoner, use reasoning_content directly)
                if reasoning_content:
                    reasoning_snapshot += f"\n[Round {tool_round}]\n{reasoning_content}"
                    yield format_sse_event("reasoning", {"content": reasoning_content[:500]})
                else:
                    # Fallback: unified extraction for other models (Qwen thinking, etc.)
                    reasoning = extract_reasoning(message)
                    if reasoning:
                        reasoning_snapshot += f"\n[Round {tool_round}]\n{reasoning}"
                        yield format_sse_event("reasoning", {"content": reasoning[:500]})

                # Strip <thinking> text tags from content
                content, tag_thinking = strip_thinking_tags(content)
                if tag_thinking and not reasoning_content:
                    reasoning_content = tag_thinking
                    reasoning_snapshot += f"\n[Round {tool_round}]\n{tag_thinking}"

                if tool_calls:
                    # Process tool calls - MUST include reasoning_content for DeepSeek Reasoner
                    assistant_msg_dict = {
                        "role": "assistant",
                        "content": content or "",
                        "tool_calls": tool_calls
                    }
                    if reasoning_content:
                        assistant_msg_dict["reasoning_content"] = reasoning_content
                    messages.append(assistant_msg_dict)

                    for tc in tool_calls:
                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"])
                        except:
                            fn_args = {}

                        yield format_sse_event("tool_call", {"name": fn_name, "args": fn_args})

                        result = _execute_tool(fn_name, fn_args, db, program_id, user_id)
                        tool_calls_log.append({"tool": fn_name, "args": fn_args, "result": result[:1000]})

                        # Check for save suggestion
                        if fn_name == "suggest_save_code":
                            try:
                                suggestion = json.loads(result)
                                if suggestion.get("type") == "save_suggestion":
                                    code_suggestion = json.dumps({
                                        "code": suggestion.get("code", ""),
                                        "name": suggestion.get("name", ""),
                                        "description": suggestion.get("description", "")
                                    })
                                    yield format_sse_event("save_suggestion", {"data": suggestion})
                            except:
                                pass

                        yield format_sse_event("tool_result", {"name": fn_name, "result": result[:500]})

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result
                        })
                else:
                    # No tool calls - final response
                    final_content = content or ""
                    yield format_sse_event("content", {"content": final_content})
                    break

            # Save progress after each round (for retry support)
            if tool_calls_log:
                assistant_msg.content = "Processing..."
                assistant_msg.tool_calls_log = json.dumps(tool_calls_log)
                assistant_msg.reasoning_snapshot = reasoning_snapshot if reasoning_snapshot else None
                db.commit()

        # Handle case where final_content is empty (AI ended with tool calls)
        # Same pattern as ai_signal_generation_service
        if not final_content:
            if 'message' in dir() and message:
                last_content = message.get("content", "")
                if last_content:
                    final_content = last_content
            if not final_content:
                final_content = "Processing completed."

        # Store content without analysis markdown (frontend renders from tool_calls_log/reasoning_snapshot)
        assistant_msg.content = final_content
        assistant_msg.code_suggestion = code_suggestion
        assistant_msg.reasoning_snapshot = reasoning_snapshot if reasoning_snapshot else None
        assistant_msg.tool_calls_log = json.dumps(tool_calls_log) if tool_calls_log else None
        assistant_msg.is_complete = True
        db.commit()

        done_data = {
            "message_id": assistant_msg.id,
            "content": final_content,
            "conversation_id": conversation.id,
            "tool_calls_log": tool_calls_log if tool_calls_log else None,
            "reasoning_snapshot": reasoning_snapshot if reasoning_snapshot else None,
            "compression_points": json.loads(conversation.compression_points) if conversation.compression_points else None,
        }
        yield format_sse_event("done", done_data)

    except Exception as e:
        logger.error(f"[AI Program {request_id}] Error: {e}")
        db.rollback()
        yield format_sse_event("error", {"content": str(e)})
