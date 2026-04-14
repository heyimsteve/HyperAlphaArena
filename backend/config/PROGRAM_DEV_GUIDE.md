# Program Trader Development Guide

This guide documents all available APIs and data structures for writing Program Trader strategies.

## Important: Query Market Data Before Writing Thresholds

**Before setting any threshold values in your strategy code**, use the `query_market_data` tool to check current market data. Indicator values vary significantly:
- CVD can range from -50M to +50M
- OI can be 100M to 500M for BTC
- ATR varies from 200 to 1500 depending on volatility

**Wrong approach**: Guessing thresholds like `if cvd > 1000`
**Correct approach**: Query current CVD value first, then set appropriate threshold

## Table of Contents

1. [Code Structure](#code-structure)
2. [MarketData Object](#marketdata-object)
3. [Decision Object](#decision-object)
4. [Available Indicators](#available-indicators)
5. [Flow Metrics](#flow-metrics)
6. [Helper Functions](#helper-functions)
7. [Example Strategies](#example-strategies)

---

## Code Structure

Your strategy must define a class with a `should_trade(self, data: MarketData)` method that returns a `Decision` object.

### Three-Layer Architecture

Organize your `should_trade` method into three logical layers:

```
┌─────────────────────────────────────────────────────────┐
│  1. VARIABLE LAYER - Fetch all required data            │
│     - Get prices, indicators, flow metrics              │
│     - Check positions and account state                 │
│     - Extract values into named variables               │
├─────────────────────────────────────────────────────────┤
│  2. LOGIC LAYER - Evaluate conditions                   │
│     - Risk management checks (margin, balance)          │
│     - Exit conditions (TP/SL, liquidation risk)         │
│     - Entry conditions (indicator signals)              │
├─────────────────────────────────────────────────────────┤
│  3. DECISION LAYER - Return appropriate Decision        │
│     - Build Decision object with all required fields    │
│     - Include reason and trading_strategy               │
│     - Default to "hold" if no conditions met            │
└─────────────────────────────────────────────────────────┘
```

This separation makes strategies easier to read, debug, and maintain.

**Minimal Example** (always hold):
```python
class MyStrategy:
    def should_trade(self, data: MarketData) -> Decision:
        return Decision(
            operation="hold",
            symbol=data.trigger_symbol,
            reason="No signal"
        )
```

**Realistic Example** (RSI + CVD confirmation):
```python
class RSI_CVD_Strategy:
    def should_trade(self, data: MarketData) -> Decision:
        symbol = data.trigger_symbol
        market_data = data.get_market_data(symbol)
        price = market_data.get("price", 0)

        # Get indicators
        rsi = data.get_indicator(symbol, "RSI14", "1h")
        rsi_value = rsi.get("value", 50)

        # Get flow metrics
        cvd_data = data.get_flow(symbol, "CVD", "1h")
        cvd_current = cvd_data.get("current", 0)

        # Check if we have a position
        position = data.positions.get(symbol)

        # Risk management: don't trade if margin usage too high
        if data.margin_usage_percent > 80:
            return Decision(
                operation="hold",
                symbol=symbol,
                reason="Margin usage too high"
            )

        if position:
            # Exit logic: take profit or stop loss
            if position.side == "long":
                # Take profit if RSI overbought
                if rsi_value > 70:
                    return Decision(
                        operation="close",
                        symbol=symbol,
                        target_portion_of_balance=1.0,
                        leverage=position.leverage,
                        min_price=price * 0.995,  # Allow 0.5% slippage
                        reason=f"Take profit: RSI {rsi_value:.1f} overbought"
                    )

                # Stop loss if price near liquidation
                if price < position.liquidation_price * 1.1:
                    return Decision(
                        operation="close",
                        symbol=symbol,
                        target_portion_of_balance=1.0,
                        leverage=position.leverage,
                        min_price=price * 0.99,
                        reason="Emergency exit: near liquidation"
                    )
        else:
            # Entry logic: RSI oversold + positive CVD
            if rsi_value < 30 and cvd_current > 0:
                # Check we have enough balance
                if data.available_balance < 100:
                    return Decision(
                        operation="hold",
                        symbol=symbol,
                        reason="Insufficient balance"
                    )

                return Decision(
                    operation="buy",
                    symbol=symbol,
                    target_portion_of_balance=0.2,  # Use 20% of balance
                    leverage=5,
                    max_price=price * 1.005,  # Allow 0.5% slippage
                    take_profit_price=price * 1.03,  # 3% profit target
                    stop_loss_price=price * 0.98,    # 2% stop loss
                    reason=f"Entry: RSI {rsi_value:.1f} oversold, CVD {cvd_current:.0f} positive",
                    trading_strategy="Buy on RSI oversold with CVD confirmation. Exit at 3% profit or 2% loss."
                )

        return Decision(
            operation="hold",
            symbol=symbol,
            reason="No signal"
        )
```

---

## MarketData Object

The `MarketData` object provides access to market data and account information.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `trigger_symbol` | `str` | Symbol that triggered this evaluation (empty string `""` for scheduled triggers) |
| `trigger_type` | `str` | Trigger type: `'signal'` or `'scheduled'` |
| `signal_pool_name` | `str` | Name of the signal pool that triggered (empty for scheduled) |
| `pool_logic` | `str` | `"OR"` or `"AND"` - how signals in the pool are combined |
| `triggered_signals` | `List[Dict]` | Full details of each triggered signal (see Signal section below) |
| `trigger_market_regime` | `RegimeInfo` | Market regime snapshot at trigger time (None for scheduled) |
| `environment` | `str` | Trading environment: `"mainnet"` or `"testnet"` |
| `max_leverage` | `int` | Maximum allowed leverage for this account |
| `default_leverage` | `int` | Default leverage setting |
| `available_balance` | `float` | Available balance in USD |
| `total_equity` | `float` | Total account equity (includes unrealized PnL) |
| `used_margin` | `float` | Currently used margin |
| `margin_usage_percent` | `float` | Margin usage percentage (0-100 scale) |
| `maintenance_margin` | `float` | Maintenance margin requirement |
| `positions` | `Dict[str, Position]` | Current open positions (keyed by symbol) |
| `recent_trades` | `List[Trade]` | Recent closed trades history |
| `open_orders` | `List[Order]` | Current open orders (TP/SL, limit orders) |
| `signal_source_type` | `Optional[str]` | `"wallet_tracking"` for wallet-origin signals, otherwise `None` |
| `wallet_event` | `Optional[Dict]` | Wallet event payload when `signal_source_type == "wallet_tracking"` |

### Wallet Signal Trigger Context

When `data.signal_source_type == "wallet_tracking"`, the strategy receives a wallet event in `data.wallet_event`.

Common wallet event envelope:
- `address`
- `event_type`
- `event_level`
- `tier`
- `summary`
- `detail`
- `event_timestamp`

Unified `position_change` detail fields:
- `action`
- `direction`
- `start_position`
- `end_position`
- `old_value`
- `new_value`
- `notional_value`
- `entry_price`
- `leverage`
- `unrealized_pnl`
- `liquidation_price`

Realtime-only extra fields:
- `fills_count`
- `total_size`
- `average_price`
- `closed_pnl`
- `fills`

Polling-only extra fields:
- `absolute_change`
- `relative_change`
- `current_position`
- `previous_position`
- `source_event_type`

Example:
```python
if data.signal_source_type == "wallet_tracking" and data.wallet_event:
    detail = data.wallet_event.get("detail", {})
    action = detail.get("action")
    direction = detail.get("direction")
    notional = detail.get("notional_value", 0)
    entry_price = detail.get("entry_price")

    if data.wallet_event.get("event_type") == "position_change" and action == "open" and direction == "long":
        ...
```

### Scheduled vs Signal Trigger (IMPORTANT)

Your strategy may be triggered by signal pool or scheduled interval. Handle both cases appropriately:

| Field | Signal Trigger | Scheduled Trigger |
|-------|---------------|-------------------|
| `trigger_type` | `"signal"` | `"scheduled"` |
| `trigger_symbol` | `"BTC"` (triggered symbol) | `""` (empty string) |
| `triggered_signals` | `[{signal details...}]` | `[]` (empty list) |
| `trigger_market_regime` | `RegimeInfo(...)` | `None` |
| `signal_pool_name` | `"OI Surge Monitor"` | `""` (empty string) |

**Example: Handle Both Trigger Types**:
```python
def should_trade(self, data: MarketData) -> Decision:
    if data.trigger_type == "scheduled":
        # Scheduled trigger: only check exit conditions, no new entries
        # Must specify symbol explicitly since trigger_symbol is empty
        symbol = "BTC"
        if symbol in data.positions:
            # Check exit conditions for existing position...
            pass
        return Decision(operation="hold", symbol=symbol, reason="Scheduled check - no action")

    # Signal trigger: use trigger_symbol and triggered_signals
    symbol = data.trigger_symbol
    for sig in data.triggered_signals:
        if sig.get("metric") == "oi_delta" and sig.get("current_value", 0) > 1.0:
            # OI spike detected, evaluate entry...
            pass

    return Decision(operation="hold", symbol=symbol, reason="No entry signal")
```

**Example MarketData Object** (with no positions):

> **Note**: Values below are format examples only. At runtime, all account data (balance, positions, orders, etc.) is fetched in real-time from your trading account. Price data is obtained through `data.get_market_data(symbol)` method.

```python
# Example: Assuming account with $10,000 balance, no open positions
# Price data obtained through data.get_market_data(symbol)
data.trigger_symbol = "BTC"
data.trigger_type = "signal"
data.available_balance = 10000.0   # Actual value from your account
data.total_equity = 10000.0        # Actual value from your account
data.used_margin = 0.0
data.margin_usage_percent = 0.0
data.maintenance_margin = 0.0
data.positions = {}                # Actual value from your account
data.recent_trades = []            # Actual value from your account
data.open_orders = []              # Actual value from your account
```

**Example MarketData Object** (with open position - actual data):
```python
# Real account state with BTC long position
# Price data obtained through data.get_market_data(symbol)
data.trigger_symbol = "BTC"
data.trigger_type = "signal"
data.available_balance = 101.93    # Available after margin
data.total_equity = 259.44         # Total equity (USDC)
data.used_margin = 157.50          # Margin for open position
data.margin_usage_percent = 60.7   # 60.7% margin usage
data.maintenance_margin = 78.75    # ~50% of initial margin
data.positions = {
    "BTC": Position(
        symbol="BTC",
        side="long",
        size=0.001,                # 0.001 BTC
        entry_price=95400.0,       # Avg entry $95,400.00
        unrealized_pnl=0.03,       # +$0.03 (+0.03% ROE)
        leverage=1,                # 1x Cross leverage
        liquidation_price=0.0      # Cross margin mode
    )
}
data.open_orders = [
    Order(
        order_id=46731293990,
        symbol="BTC",
        side="Sell",
        direction="Close Long",
        order_type="Limit",
        size=0.001,
        price=76320.0,             # Limit price
        trigger_price=None,
        reduce_only=True,
        timestamp=1768665293187
    )
]
data.recent_trades = [
    Trade(
        symbol="BTC",
        side="Sell",
        size=0.001,
        price=95367.0,
        timestamp=1768665292968,
        pnl=-0.033,
        close_time="2026-01-17 15:54:52 UTC"
    )
]
```

### Position Object

The `Position` object represents an open trading position.

**Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `symbol` | `str` | Trading symbol (e.g., "BTC") |
| `side` | `str` | Position side: `"long"` or `"short"` |
| `size` | `float` | Position size (positive for both long/short) |
| `entry_price` | `float` | Average entry price |
| `unrealized_pnl` | `float` | Unrealized profit/loss in USD |
| `leverage` | `int` | Leverage multiplier (1-50) |
| `liquidation_price` | `float` | Price at which position will be liquidated |
| `opened_at` | `int` or `None` | Timestamp in milliseconds when position was opened |
| `opened_at_str` | `str` or `None` | Human-readable opened time (e.g., "2026-01-15 10:30:00 UTC") |
| `holding_duration_seconds` | `float` or `None` | How long position has been held in seconds |
| `holding_duration_str` | `str` or `None` | Human-readable duration (e.g., "2h 30m") |

**Example Usage**:
```python
# Check if we have an open position
if "BTC" in data.positions:
    pos = data.positions["BTC"]

    # Check position side
    if pos.side == "long":
        # We're long, consider taking profit or adding
        if pos.unrealized_pnl > 100:
            # Take profit
            pass

    # Time-based exit: close if held for more than 4 hours
    if pos.holding_duration_seconds and pos.holding_duration_seconds > 4 * 3600:
        # Position held too long, consider closing
        pass

    # Check if position is at risk
    market_data = data.get_market_data("BTC")
    current_price = market_data.get("price", 0)
    if pos.side == "long" and current_price < pos.liquidation_price * 1.1:
        # Close position - too close to liquidation
        pass
```

### Trade Object

The `Trade` object represents a recently closed trade.

**Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `symbol` | `str` | Trading symbol (e.g., "BTC") |
| `side` | `str` | Position side that was closed: `"Long"` or `"Short"` |
| `size` | `float` | Trade size |
| `price` | `float` | Close price |
| `timestamp` | `int` | Close timestamp in milliseconds |
| `pnl` | `float` | Realized profit/loss in USD |
| `close_time` | `str` | Close time in UTC string format |

**Example Usage**:
```python
# Check last trade time to avoid rapid trading
if data.recent_trades:
    last_trade = data.recent_trades[0]  # Most recent first
    time_since_last = time.time() * 1000 - last_trade.timestamp
    if time_since_last < 2 * 60 * 60 * 1000:  # 2 hours in ms
        return Decision(operation="hold", symbol=symbol, reason="Cooldown period")
```

### Order Object

The `Order` object represents an open order (TP/SL, limit orders).

**Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `order_id` | `int` | Unique order ID |
| `symbol` | `str` | Trading symbol |
| `side` | `str` | Order side: `"Buy"` or `"Sell"` |
| `direction` | `str` | `"Open Long"`, `"Open Short"`, `"Close Long"`, `"Close Short"` |
| `order_type` | `str` | Order type:<br>- `"Market"`: Market order<br>- `"Limit"`: Limit order<br>- `"Stop Market"`: Stop loss market<br>- `"Stop Limit"`: Stop loss limit<br>- `"Take Profit Market"`: Take profit market<br>- `"Take Profit Limit"`: Take profit limit |
| `size` | `float` | Order size |
| `price` | `float` | Limit price |
| `trigger_price` | `float` | Trigger price (for stop/TP orders) |
| `reduce_only` | `bool` | Whether this is a reduce-only order |
| `timestamp` | `int` | Order placement timestamp in milliseconds |

**Example Usage**:
```python
# Check if we already have TP/SL orders
has_stop_loss = any(
    o.symbol == "BTC" and o.order_type == "Stop Limit"
    for o in data.open_orders
)
if not has_stop_loss and "BTC" in data.positions:
    # Need to set stop loss
    pass

# Check last order time to avoid duplicate orders
btc_orders = [o for o in data.open_orders if o.symbol == "BTC"]
if btc_orders:
    last_order_time = max(o.timestamp for o in btc_orders)
    # ...
```

### RegimeInfo Object

The `RegimeInfo` object represents market regime classification (from `get_regime()` or `trigger_market_regime`).

**Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `regime` | `str` | Market regime type (see table below) |
| `conf` | `float` | Confidence score 0.0-1.0 |
| `direction` | `str` | `"bullish"`, `"bearish"`, or `"neutral"` |
| `reason` | `str` | Human-readable explanation |
| `indicators` | `dict` | Indicator values used for classification |

**Regime Types**:
| Regime | Description |
|--------|-------------|
| `breakout` | Strong directional move with volume confirmation |
| `absorption` | Large orders absorbed without price impact (potential reversal) |
| `stop_hunt` | Wick beyond range then reversal (liquidity grab) |
| `exhaustion` | Extreme RSI with diverging CVD (trend weakening) |
| `trap` | Price breaks level but CVD/OI diverge (false breakout) |
| `continuation` | Trend continuation with aligned indicators |
| `noise` | No clear pattern, low conviction |

**Example Return** (actual test):
```python
regime = data.get_regime("BTC", "1h")
# Or: regime = data.trigger_market_regime (snapshot at trigger time)
# Returns:
RegimeInfo(
    regime="absorption",
    conf=0.455,
    direction="bearish",
    reason="Strong flow absorbed without price movement",
    indicators={
        "cvd_ratio": -0.3939,
        "oi_delta": 0.157,
        "taker_ratio": 0.435,
        "price_atr": -0.293,
        "rsi": 50.8
    }
)
```

### Signal Object

The `triggered_signals` list contains signal details when trigger_type is "signal".

**Supported Metric Types**:

| Metric | Description | Return Fields |
|--------|-------------|---------------|
| `oi_delta` | Open Interest change percentage | `operator`, `threshold`, `current_value` |
| `cvd` | Cumulative Volume Delta | `operator`, `threshold`, `current_value` |
| `depth_ratio` | Order book depth ratio (bid/ask) | `operator`, `threshold`, `current_value` |
| `order_imbalance` | Order book imbalance (-1 to +1) | `operator`, `threshold`, `current_value` |
| `taker_ratio` | Taker buy/sell ratio | `operator`, `threshold`, `current_value` |
| `funding` | Funding rate change (bps) | `operator`, `threshold`, `current_value` |
| `oi` | Open Interest change (USD) | `operator`, `threshold`, `current_value` |
| `price_change` | Price change percentage | `operator`, `threshold`, `current_value` |
| `volatility` | Price volatility | `operator`, `threshold`, `current_value` |
| `taker_volume` | Taker volume (special composite) | `direction`, `buy`, `sell`, `ratio`, `ratio_threshold`, `volume_threshold` |

**Standard Signal Fields** (all metrics except taker_volume):
| Field | Type | Description |
|-------|------|-------------|
| `signal_id` | `int` | Signal ID |
| `signal_name` | `str` | Name of the signal |
| `description` | `str` | Signal description |
| `metric` | `str` | Metric type (see table above) |
| `time_window` | `str` | Time window (1m, 5m, 15m, 1h, 4h) |
| `operator` | `str` | Comparison operator: `<`, `>`, `<=`, `>=`, `abs_greater_than` |
| `threshold` | `float` | Threshold value |
| `current_value` | `float` | Current metric value that triggered |
| `condition_met` | `bool` | Whether condition was met |

**Example Return** (standard signal):
```python
# Access: signals = data.triggered_signals
[
    {
        "signal_id": 31,
        "signal_name": "OI Delta Spike",
        "description": "Open interest increased significantly",
        "metric": "oi_delta",
        "time_window": "5m",
        "operator": ">",
        "threshold": 1.0,
        "current_value": 1.52,
        "condition_met": True
    }
]
```

**Taker Volume Signal Fields** (special composite signal):
| Field | Type | Description |
|-------|------|-------------|
| `signal_id` | `int` | Signal ID |
| `signal_name` | `str` | Name of the signal |
| `metric` | `str` | Always `"taker_volume"` |
| `time_window` | `str` | Time window |
| `direction` | `str` | Detected direction: `"buy"` or `"sell"` |
| `buy` | `float` | Taker buy volume (USD) |
| `sell` | `float` | Taker sell volume (USD) |
| `total` | `float` | Total volume (buy + sell) |
| `ratio` | `float` | Buy/sell ratio |
| `ratio_threshold` | `float` | Configured ratio threshold |
| `volume_threshold` | `float` | Configured minimum volume |
| `condition_met` | `bool` | Whether condition was met |

**Example Return** (taker_volume signal):
```python
{
    "signal_id": 42,
    "signal_name": "Taker Buy Surge",
    "metric": "taker_volume",
    "time_window": "5m",
    "direction": "buy",
    "buy": 5234567.89,
    "sell": 2345678.90,
    "total": 7580246.79,
    "ratio": 2.23,
    "ratio_threshold": 1.5,
    "volume_threshold": 1000000,
    "condition_met": True
}
```

**Example Usage**:
```python
# Check what triggered this execution
if data.triggered_signals:
    for sig in data.triggered_signals:
        metric = sig.get("metric")

        # Handle standard signals
        if metric in ["oi_delta", "cvd", "price_change", "volatility"]:
            value = sig.get("current_value", 0)
            threshold = sig.get("threshold", 0)
            # Use value and threshold for strategy logic

        # Handle taker_volume special signal
        elif metric == "taker_volume":
            direction = sig.get("direction")
            ratio = sig.get("ratio", 1.0)
            if direction == "buy" and ratio > 2.0:
                # Strong buying pressure
                pass
```

### Methods

#### get_indicator(symbol, indicator, period)

Get technical indicator value.

**Return Type**: `Dict[str, Any]` - Dictionary containing indicator values. Structure varies by indicator type.

**Common Return Structures**:

1. **Simple indicators** (RSI, MA, EMA, ATR, VWAP, OBV):
```python
{
    "value": float,        # Latest value (most recent candle)
    "series": [float]      # Full series for historical analysis
}
```

2. **MACD**:
```python
{
    "macd": float,        # MACD line (latest value)
    "signal": float,      # Signal line (latest value)
    "histogram": float    # MACD - Signal (latest value)
}
```

3. **Bollinger Bands**:
```python
{
    "upper": float,       # Upper band (latest value)
    "middle": float,      # Middle band / SMA (latest value)
    "lower": float        # Lower band (latest value)
}
```

4. **Stochastic**:
```python
{
    "k": float,           # %K line (0-100, latest value)
    "d": float            # %D line (0-100, latest value)
}
```

**Example Returns** (actual API results):
```python
# RSI14 - value is latest, series contains historical values
data.get_indicator("BTC", "RSI14", "1h")
# Returns: {"value": 46.76, "series": [50.0, 0.0, 0.0, 5.94, ...]}

# MACD - all three lines as floats
data.get_indicator("BTC", "MACD", "1h")
# Returns: {"macd": -73.27, "signal": -81.88, "histogram": 8.60}

# Bollinger Bands - upper/middle/lower band values
data.get_indicator("BTC", "BOLL", "1h")
# Returns: {"upper": 97569.63, "middle": 96727.55, "lower": 95885.47}

# Stochastic - %K and %D values (0-100 range)
data.get_indicator("BTC", "STOCH", "1h")
# Returns: {"k": 51.35, "d": 51.78}

# EMA20 - note: series has warmup period (first 19 values are 0.0)
data.get_indicator("BTC", "EMA20", "1h")
# Returns: {"value": 96457.32, "series": [0.0, 0.0, ..., 96457.32]}
```

**Example Usage**:
```python
# RSI - check oversold/overbought
rsi = data.get_indicator("BTC", "RSI14", "1h")
rsi_value = rsi.get("value", 50)
if rsi_value < 30:
    # Oversold - potential buy
    pass
elif rsi_value > 70:
    # Overbought - potential sell
    pass

# MACD - check crossover
macd = data.get_indicator("BTC", "MACD", "1h")
if macd.get("histogram", 0) > 0:
    # MACD above signal - bullish
    pass

# Bollinger Bands - check price position
boll = data.get_indicator("BTC", "BOLL", "1h")
market_data = data.get_market_data("BTC")
current_price = market_data.get("price", 0)
if current_price < boll.get("lower", 0):
    # Price below lower band - oversold
    pass
```

#### get_klines(symbol, period, count=50)

Get K-line (candlestick) data.

**Return Type**: `List[Kline]` - List of Kline objects, sorted from oldest to newest (time ascending).

**Kline Object Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | `int` | Unix timestamp in seconds |
| `open` | `float` | Opening price |
| `high` | `float` | Highest price |
| `low` | `float` | Lowest price |
| `close` | `float` | Closing price |
| `volume` | `float` | Trading volume |

**Example Return** (actual API results):
```python
klines = data.get_klines("BTC", "1h", count=3)
# Returns (sorted oldest to newest):
[
    Kline(timestamp=1768658400, open=95673.0, high=95673.0, low=95160.0, close=95400.0, volume=2.98375),
    Kline(timestamp=1768647600, open=95119.0, high=95336.0, low=95087.0, close=95336.0, volume=285.85),
    Kline(timestamp=1768651200, open=95336.0, high=95408.0, low=95254.0, close=95255.0, volume=113.77)
]
```

**Example Usage**:
```python
# Get last 50 1-hour candles
klines = data.get_klines("BTC", "1h", count=50)

# Check if we have data
if len(klines) < 5:
    return Decision(operation="hold", symbol="BTC", reason="Not enough kline data")

# Access most recent candle
latest = klines[-1]
current_price = latest.close

# Calculate price trend (last 5 candles)
last_5_closes = [k.close for k in klines[-5:]]
is_uptrend = all(last_5_closes[i] < last_5_closes[i+1] for i in range(4))

# Check for high volume
avg_volume = sum(k.volume for k in klines[-20:]) / 20
if latest.volume > avg_volume * 2:
    # High volume candle - potential breakout
    pass
```

#### get_market_data(symbol)

Get complete market data (price, volume, open interest, funding rate, etc.).

**Reuses AI Trader's data layer**: This method uses the same data source as AI Trader's `{BTC_market_data}` variable.

**Return Type**: `Dict[str, Any]` - Dictionary with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | `str` | Trading pair symbol |
| `price` | `float` | Current mark price |
| `oracle_price` | `float` | Oracle price |
| `change24h` | `float` | 24h price change (USD) |
| `percentage24h` | `float` | 24h price change percentage |
| `volume24h` | `float` | 24h trading volume (USD) |
| `open_interest` | `float` | Open interest (USD) |
| `funding_rate` | `float` | Funding rate |

**Actual Return Example**:
```python
market_data = data.get_market_data("BTC")
# Returns:
{
  "symbol": "BTC",
  "price": 95460.0,
  "oracle_price": 95251.0,
  "change24h": 360.0,
  "volume24h": 1778510.45,
  "percentage24h": 0.378,
  "open_interest": 10898599.47,
  "funding_rate": 0.0000425
}
```

**Usage Examples**:
```python
# Get complete BTC market data
btc_data = data.get_market_data("BTC")

# Check price change
if btc_data.get("percentage24h", 0) > 5:
    # 24h gain exceeds 5%
    pass

# Check funding rate
funding_rate = btc_data.get("funding_rate", 0)
if funding_rate > 0.0001:
    # High funding rate, longs crowded
    pass

# Check open interest turnover
oi = btc_data.get("open_interest", 0)
volume = btc_data.get("volume24h", 0)
if oi > 0 and volume / oi > 0.5:
    # High turnover, active market
    pass
```

**Recommended way to get price**:
```python
# Get complete market data (recommended)
market_data = data.get_market_data("BTC")
price = market_data.get("price", 0)
funding_rate = market_data.get("funding_rate", 0)

# If you only need price
btc_data = data.get_market_data("BTC")
price = btc_data.get("price", 0)
```

**Important**: `data.prices` has been removed. Always use `data.get_market_data(symbol)` to get price.

#### get_flow(symbol, metric, period)

Get order flow metrics.

**Parameters**:
- `symbol`: Trading symbol (e.g., "BTC")
- `metric`: Metric type ("CVD", "OI", "OI_DELTA", "TAKER", "FUNDING", "DEPTH", "IMBALANCE")
- `period`: Time period ("1m", "5m", "15m", "1h", "4h", "1d")

**Example Return** (CVD):
```python
cvd = data.get_flow("BTC", "CVD", "1h")
# Returns:
{
  "current": -4284734.70,
  "last_5": [4388145.60, 13977923.30, -6439359.71, 12102468.00, -4284734.70],
  "cumulative": -7838887.07,
  "period": "1h"
}
```

**Usage Examples**:
```python
cvd = data.get_flow("BTC", "CVD", "1h")
oi_delta = data.get_flow("ETH", "OI_DELTA", "15m")
```

#### get_regime(symbol, period)

Get market regime classification with full indicator data.

**Return Type**: `RegimeInfo` object with attributes:
- `regime`: str - Market regime type
- `conf`: float - Confidence score (0.0-1.0)
- `direction`: str - Market direction (`bullish`, `bearish`, `neutral`)
- `reason`: str - Human-readable explanation
- `indicators`: dict - Indicator values used for classification

**Example Return** (actual test):
```python
regime = data.get_regime("BTC", "1h")
# Returns:
RegimeInfo(
    regime="absorption",
    conf=0.455,
    direction="bearish",
    reason="Strong flow absorbed without price movement",
    indicators={
        "cvd_ratio": -0.3939,
        "oi_delta": 0.157,
        "taker_ratio": 0.435,
        "price_atr": -0.293,
        "rsi": 50.8
    }
)
```

**Regime Types** (7 types):
| Regime | Description |
|--------|-------------|
| `breakout` | Strong directional move with volume confirmation |
| `absorption` | Large orders absorbed without price impact (potential reversal) |
| `stop_hunt` | Wick beyond range then reversal (liquidity grab) |
| `exhaustion` | Extreme RSI with diverging CVD (trend weakening) |
| `trap` | Price breaks level but CVD/OI diverge (false breakout) |
| `continuation` | Trend continuation with aligned indicators |
| `noise` | No clear pattern, low conviction |

**Direction Types** (3 types):
| Direction | Description |
|-----------|-------------|
| `bullish` | Upward bias based on CVD, taker ratio, and price action |
| `bearish` | Downward bias |
| `neutral` | No clear directional bias |

**Indicator Definitions**:
- `cvd_ratio`: CVD / total notional. Positive = net buying pressure
- `oi_delta`: Open interest change percentage
- `taker_ratio`: Taker buy/sell ratio. >1 = aggressive buying
- `price_atr`: Price change / ATR. Measures move strength
- `rsi`: RSI(14) momentum. >70 overbought, <30 oversold

**Example Usage**:
```python
regime = data.get_regime("BTC", "1h")
if regime.regime == "breakout" and regime.conf > 0.7 and regime.direction == "bullish":
    # High confidence bullish breakout
    pass

# Access underlying indicators
if regime.indicators.get("rsi", 50) < 30:
    # RSI oversold
    pass
```

#### get_price_change(symbol, period)

Get price change over period.

**Parameters**:
- `symbol`: Trading symbol (e.g., "BTC")
- `period`: Time period ("1m", "5m", "15m", "1h", "4h", "1d")

**Example Return**:
```python
change = data.get_price_change("BTC", "5m")
# Returns:
{
  "change_percent": 0.141,   # Price change percentage (0.141 = +0.141%)
  "change_usd": 129.0        # Absolute USD change
}
```

#### get_factor(symbol, factor_name, period="5m")

Get factor value and effectiveness metrics for a specific K-line period.

**Parameters**:
- `symbol`: Trading symbol (e.g., "BTC")
- `factor_name`: Factor name (e.g., "RSI21", "MOM10", or custom factor name)
- `period`: Time period ("1m", "5m", "15m", "1h", "4h", "1d"). Omitted defaults to `5m` for backward compatibility.

**Example Return**:
```python
f = data.get_factor("BTC", "RSI21", "1h")
# Returns:
{
  "factor_name": "RSI21",
  "symbol": "BTC",
  "period": "1h",
  "id": 5,                          # Factor ID in database
  "expression": "RSI(close, 21)",    # Factor formula
  "description": "Relative Strength Index with 21-period lookback",
  "category": "momentum",           # Factor category
  "value": 0.0234,                   # Value from latest 500 1h K-lines
  "ic": 0.0512,                     # Information Coefficient (predictive power)
  "icir": 1.35,                     # IC Information Ratio (stability)
  "win_rate": 58.2,                 # Win rate percentage
  "decay_half_life_hours": -1       # -1=Persistent, positive=half-life hours, None=insufficient data
}
```

**Example Usage**:
```python
f = data.get_factor("BTC", "MOM10", "5m")
if f["value"] is not None and f["value"] > 0.02:
    # Strong positive momentum
    if f.get("icir") and abs(f["icir"]) > 1.0:
        # Factor has reliable predictive power
        log(f"MOM10 triggered: value={f['value']}, ICIR={f['icir']}, expr={f['expression']}")
        pass
```

**Note**: Prompt factor variables, Program live execution, and Program backtest must keep the same factor-value calculation semantics. In backtest mode, `value` is computed from historical K-lines at the current bar. Effectiveness fields (`ic`, `icir`, etc.) are not available in backtest. Metadata (`id`, `expression`, `description`) is always available.

#### get_factor_ranking(symbol, top_n=10)

Get top factors ranked by |ICIR| (most reliable predictors first).

**Parameters**:
- `symbol`: Trading symbol
- `top_n`: Number of top factors to return (default: 10)

**Example Return**:
```python
ranking = data.get_factor_ranking("BTC", top_n=5)
# Returns list sorted by |ICIR| descending:
[
  {
    "factor_name": "SKEW20", "id": 12,
    "expression": "SKEW(RET(close, 1), 20)",
    "description": "20-period return skewness",
    "ic": -0.08, "icir": -2.1, "win_rate": 62.0, "decay_half_life_hours": -1,
    "ic_7d": -0.095, "ic_trend": 1.19
  },
  {
    "factor_name": "MOM10", "id": 8,
    "expression": "RET(close, 10)",
    "description": "10-period momentum (rate of change)",
    "ic": 0.05, "icir": 1.5, "win_rate": 55.0, "decay_half_life_hours": 8,
    "ic_7d": 0.038, "ic_trend": 0.76
  },
  ...
]
```

**Note**: Not available in backtest mode (returns empty list).

---

## Decision Object

The `Decision` object tells the system what trading action to take.

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `operation` | `str` | **Required**: `'buy'`, `'sell'`, `'hold'`, or `'close'` |
| `symbol` | `str` | **Required**: Trading symbol (e.g., `'BTC'`, `'ETH'`) |

### Order Execution Fields

| Field | Type | Description |
|-------|------|-------------|
| `target_portion_of_balance` | `float` | Required for buy/sell/close: 0.1-1.0 (10%-100% of balance) |
| `leverage` | `int` | Required for buy/sell/close: 1-50 (default: 10) |
| `max_price` | `float` | Required for buy or close short: maximum entry price |
| `min_price` | `float` | Required for sell or close long: minimum entry price |
| `time_in_force` | `str` | Optional: `'Ioc'`, `'Gtc'`, `'Alo'` (default: `'Ioc'`) |

### Take Profit / Stop Loss Fields

| Field | Type | Description |
|-------|------|-------------|
| `take_profit_price` | `float` | Optional: TP trigger price |
| `stop_loss_price` | `float` | Optional: SL trigger price |
| `tp_execution` | `str` | Optional: `'market'` or `'limit'` (default: `'limit'`) |
| `sl_execution` | `str` | Optional: `'market'` or `'limit'` (default: `'limit'`) |

### Documentation Fields

| Field | Type | Description |
|-------|------|-------------|
| `reason` | `str` | Optional: Explanation of the decision |
| `trading_strategy` | `str` | Optional: Entry thesis, risk controls |

### Time In Force Options

- **Ioc** (Immediate or Cancel): Fill immediately or cancel unfilled portion
- **Gtc** (Good Till Cancel): Order stays in orderbook until filled or cancelled
- **Alo** (Add Liquidity Only): Maker-only order, rejected if would take liquidity

### Price Precision Control

**Recommendation**: Maintain consistent decimal places with market prices when calculating prices.

- Market prices are available through `data.get_market_data(symbol)`
- Different assets have different precision: BTC/ETH typically use 1-2 decimals, small-cap coins may need 4-8 decimals
- Use `round()` function to control precision and avoid excessive decimal places (e.g., 93622.54776373146)

**Why control precision?**
- Maintain code readability
- Adapt to different asset precision requirements
- Avoid excessive precision from floating-point arithmetic

---

## Available Indicators

Use `data.get_indicator(symbol, indicator, period)` to access these indicators.

**Important**: Use `query_market_data` tool to check current indicator values before setting thresholds in your strategy code.

### RSI (Relative Strength Index)

| Indicator | Return Type | Value Range | Description |
|-----------|-------------|-------------|-------------|
| `RSI14` | `{'value': float}` | 0-100 | 14-period RSI, standard momentum indicator |
| `RSI7` | `{'value': float}` | 0-100 | 7-period RSI, faster response to price changes |

**Interpretation**:
- `< 30`: Oversold, potential buy signal
- `> 70`: Overbought, potential sell signal
- `30-70`: Neutral zone

**Example**:
```python
rsi = data.get_indicator("BTC", "RSI14", "1h")
if rsi.get('value', 50) < 30:
    # Oversold condition
```

### Moving Averages

| Indicator | Return Type | Typical Range | Description |
|-----------|-------------|---------------|-------------|
| `MA5` | `{'value': float}` | Same as price | 5-period Simple Moving Average |
| `MA10` | `{'value': float}` | Same as price | 10-period Simple Moving Average |
| `MA20` | `{'value': float}` | Same as price | 20-period Simple Moving Average |
| `EMA20` | `{'value': float}` | Same as price | 20-period Exponential Moving Average |
| `EMA50` | `{'value': float}` | Same as price | 50-period Exponential Moving Average |
| `EMA100` | `{'value': float}` | Same as price | 100-period Exponential Moving Average |

**Interpretation**:
- Price above MA: Bullish trend
- Price below MA: Bearish trend
- Golden cross (short MA > long MA): Buy signal
- Death cross (short MA < long MA): Sell signal

**Example**:
```python
ema20 = data.get_indicator("BTC", "EMA20", "1h").get('value', 0)
ema50 = data.get_indicator("BTC", "EMA50", "1h").get('value', 0)
market_data = data.get_market_data("BTC")
price = market_data.get("price", 0)
if ema20 > ema50 and price > ema20:
    # Strong uptrend
```

### MACD

| Indicator | Return Type | Description |
|-----------|-------------|-------------|
| `MACD` | `{'macd': float, 'signal': float, 'histogram': float}` | Moving Average Convergence Divergence |

**Typical Values** (for BTC):
- `macd`: -1000 to +1000 (varies with price level)
- `signal`: Similar range to macd
- `histogram`: -500 to +500

**Interpretation**:
- `histogram > 0`: Bullish momentum
- `histogram < 0`: Bearish momentum
- MACD crosses above signal: Buy signal
- MACD crosses below signal: Sell signal

**Example**:
```python
macd = data.get_indicator("BTC", "MACD", "1h")
if macd.get('histogram', 0) > 0 and macd.get('macd', 0) > macd.get('signal', 0):
    # Bullish MACD crossover
```

### Bollinger Bands

| Indicator | Return Type | Description |
|-----------|-------------|-------------|
| `BOLL` | `{'upper': float, 'middle': float, 'lower': float}` | Bollinger Bands (20-period, 2 std dev) |

**Typical Values**: Same scale as price (e.g., BTC ~95000-98000)

**Interpretation**:
- Price near upper band: Potentially overbought
- Price near lower band: Potentially oversold
- Band width indicates volatility

**Example**:
```python
boll = data.get_indicator("BTC", "BOLL", "1h")
market_data = data.get_market_data("BTC")
price = market_data.get("price", 0)
if price < boll.get('lower', 0):
    # Price below lower band - potential reversal
```

### ATR (Average True Range)

| Indicator | Return Type | Typical Range | Description |
|-----------|-------------|---------------|-------------|
| `ATR14` | `{'value': float}` | 200-1500 (BTC) | 14-period volatility measure |

**Interpretation**:
- Higher ATR = Higher volatility
- Use for position sizing and stop-loss placement
- ATR * 2 is common stop-loss distance

**Example**:
```python
atr = data.get_indicator("BTC", "ATR14", "1h").get('value', 500)
stop_loss = price - (atr * 2)  # 2x ATR stop
```

### Other Indicators

| Indicator | Return Type | Description |
|-----------|-------------|-------------|
| `VWAP` | `{'value': float}` | Volume Weighted Average Price (same scale as price) |
| `STOCH` | `{'k': float, 'd': float}` | Stochastic Oscillator (0-100 range) |
| `OBV` | `{'value': float}` | On-Balance Volume (cumulative, can be millions) |

### Supported Periods

- `1m` - 1 minute
- `5m` - 5 minutes
- `15m` - 15 minutes
- `1h` - 1 hour
- `4h` - 4 hours

---

## Flow Metrics

Use `data.get_flow(symbol, metric, period)` to access order flow data.

**Important**: All flow metrics return a **dict with full data structure**, including `current` value, `last_5` history, and metric-specific fields. Use `query_market_data` tool to check current values before setting thresholds.

### CVD (Cumulative Volume Delta)

**Return Structure**:
```python
{
    "current": float,      # Current period's delta (taker buy - taker sell notional)
    "last_5": [float],     # Last 5 periods' delta values
    "cumulative": float,   # Cumulative sum over lookback window
    "period": str          # Time period (e.g., "1h")
}
```

**Example Return** (actual test):
```python
{
    "current": -389433.36764,
    "last_5": [-11428730.30583, 8420546.86251, 13435392.63129, 48471120.86648, -389433.36764],
    "cumulative": 84066208.88112,
    "period": "1h"
}
```

**Interpretation**:
- Positive CVD: More aggressive buying (taker buys > taker sells)
- Negative CVD: More aggressive selling
- Rising CVD with rising price: Healthy uptrend
- Falling CVD with rising price: Bearish divergence (potential reversal)

**Example Usage**:
```python
cvd_data = data.get_flow("BTC", "CVD", "1h")
current_cvd = cvd_data.get("current", 0)
last_5 = cvd_data.get("last_5", [])

# Check if CVD is trending up (last 3 periods increasing)
if len(last_5) >= 3 and last_5[-1] > last_5[-2] > last_5[-3]:
    # CVD trending up - bullish signal
    pass
```

### OI (Open Interest)

**Return Structure**:
```python
{
    "current": float,      # Current period's OI change in USD
    "last_5": [float],     # Last 5 periods' OI change values
    "period": str          # Time period
}
```

**Example Return** (actual test):
```python
{
    "current": 18257834.64,
    "last_5": [32615864.12, -9186781.88, -3550037.11, -16232245.9, 18257834.64],
    "period": "1h"
}
```

**Interpretation**:
- Rising OI + Rising price: New longs entering, bullish
- Rising OI + Falling price: New shorts entering, bearish
- Falling OI: Positions closing, trend weakening

**Example Usage**:
```python
oi_data = data.get_flow("BTC", "OI", "1h")
current_oi = oi_data.get("current", 0)
# Positive = OI increasing, Negative = OI decreasing
```

### OI_DELTA (Open Interest Change %)

**Return Structure**:
```python
{
    "current": float,      # Current period's OI change percentage
    "last_5": [float],     # Last 5 periods' OI change percentages
    "period": str          # Time period
}
```

**Example Return** (actual test):
```python
{
    "current": 0.6016847209056745,
    "last_5": [1.083886578409779, -0.30247846838551423, -0.1168357287082588, -0.5347910774531256, 0.6016847209056745],
    "period": "1h"
}
```

**Interpretation**:
- `> 1%`: Significant new positions opening
- `< -1%`: Significant positions closing
- Near 0: Stable market

**Example Usage**:
```python
oi_delta = data.get_flow("BTC", "OI_DELTA", "1h")
if oi_delta.get("current", 0) > 1.0:  # 1% increase
    # Large influx of new positions
    pass
```

### TAKER (Taker Buy/Sell Volume)

**Return Structure**:
```python
{
    "buy": float,              # Taker buy notional (USD)
    "sell": float,             # Taker sell notional (USD)
    "ratio": float,            # Buy/Sell ratio (buy/sell)
    "ratio_last_5": [float],   # Last 5 periods' ratios
    "volume_last_5": [float],  # Last 5 periods' total volumes
    "period": str              # Time period
}
```

**Example Return** (actual test):
```python
{
    "buy": 2780959.67706,
    "sell": 3170393.0447,
    "ratio": 0.8771655873106894,
    "ratio_last_5": [0.6164917154662386, 1.4234337814976776, 1.3693340979925976, 2.128215775227713, 0.8771655873106894],
    "volume_last_5": [48172226.26659, 48193220.79857, 86190075.74511, 134396387.87792, 5951352.72176],
    "period": "1h"
}
```

**Interpretation**:
- `ratio > 1.5`: Strong buying aggression
- `ratio < 0.7`: Strong selling aggression
- `ratio 0.9-1.1`: Balanced market

**Example Usage**:
```python
taker = data.get_flow("BTC", "TAKER", "1h")
ratio = taker.get("ratio", 1.0)
if ratio > 1.5:
    # Aggressive buying
    pass
```

### FUNDING (Funding Rate)

**Return Structure**:
```python
{
    "current": float,       # Current funding rate (K-line display unit: raw × 1000000)
    "current_pct": float,   # Current rate as percentage (e.g., 0.00125 = 0.00125%)
    "change": float,        # Rate change from previous period (K-line display unit)
    "change_pct": float,    # Rate change as percentage
    "last_5": [float],      # Last 5 periods' rates (K-line display unit)
    "annualized": float,    # Annualized rate percentage
    "period": str           # Time period
}
```

**Example Return** (actual test):
```python
{
    "current": 12.5,
    "current_pct": 0.00125,
    "change": 0.0,
    "change_pct": 0.0,
    "last_5": [12.5, 12.5, 12.5, 12.5, 12.5],
    "annualized": 1.36875,
    "period": "1h"
}
```

**Unit Explanation**:
- 1 bps (basis point) = 0.01% = 0.0001
- K-line display unit: raw value × 1000000 (e.g., 12.5 means 0.00125%)
- Signal triggers on rate **CHANGE**, not absolute value

**Interpretation**:
- Positive funding: Longs pay shorts, market bullish sentiment
- Negative funding: Shorts pay longs, market bearish sentiment
- Extreme funding change: Potential reversal signal

**Example Usage**:
```python
funding = data.get_flow("BTC", "FUNDING", "1h")
change = funding.get("change", 0)
if change > 5:  # Funding rate increased by 5 bps
    # Sentiment shifting bullish
    pass
```

### DEPTH (Order Book Depth)

**Return Structure**:
```python
{
    "bid": float,              # Bid depth (USD millions)
    "ask": float,              # Ask depth (USD millions)
    "ratio": float,            # Bid/Ask ratio
    "ratio_last_5": [float],   # Last 5 periods' ratios
    "spread": float,           # Bid-ask spread
    "period": str              # Time period
}
```

**Example Return** (actual test):
```python
{
    "bid": 4.50387,
    "ask": 11.23994,
    "ratio": 0.40070231691628244,
    "ratio_last_5": [22.007252846228603, 0.0011062288829763204, 1117.9455782312923, 3.7280108357948545, 0.40070231691628244],
    "spread": 1.0,
    "period": "1h"
}
```

**Interpretation**:
- `ratio > 1`: More buy orders than sell orders (bullish)
- `ratio < 1`: More sell orders than buy orders (bearish)

**Example Usage**:
```python
depth = data.get_flow("BTC", "DEPTH", "1h")
ratio = depth.get("ratio", 1.0)
if ratio > 1.5:
    # Strong bid-side support
    pass
```

### IMBALANCE (Order Book Imbalance)

**Return Structure**:
```python
{
    "current": float,      # Current imbalance score (-1 to +1)
    "last_5": [float],     # Last 5 periods' imbalance scores
    "period": str          # Time period
}
```

**Example Return** (actual test):
```python
{
    "current": -0.4278551379875647,
    "last_5": [0.9130708905853642, -0.997789987014244, 0.9982126029729156, 0.5769891251393954, -0.4278551379875647],
    "period": "1h"
}
```

**Interpretation**:
- `> 0.3`: Strong bid-side imbalance (bullish)
- `< -0.3`: Strong ask-side imbalance (bearish)
- Near 0: Balanced order book

**Example Usage**:
```python
imbalance = data.get_flow("BTC", "IMBALANCE", "1h")
current = imbalance.get("current", 0)
if current > 0.5:
    # Strong bullish imbalance
    pass
```

---

## Helper Functions

### Available Sandbox Objects

```python
time   # Pre-injected sandbox object for timestamp operations
math   # Pre-injected sandbox object for mathematical functions
```

Do not write `import time` or `import math` in Program Trader code.
Use `time.time()` and `math.sqrt()` directly.

### Time Module Functions

```python
time.time()           # Current Unix timestamp in seconds (float)
time.time() * 1000    # Current timestamp in milliseconds (for comparison with trade.timestamp)
```

**Example Usage**:
```python
# Check time since last trade to avoid rapid trading
if data.recent_trades:
    last_trade = data.recent_trades[0]
    time_since_last_ms = time.time() * 1000 - last_trade.timestamp
    if time_since_last_ms < 2 * 60 * 60 * 1000:  # 2 hours in ms
        return Decision(operation="hold", symbol=symbol, reason="Cooldown period")
```

### Available Math Functions

```python
math.sqrt(x)    # Square root
math.log(x)     # Natural logarithm
math.log10(x)   # Base-10 logarithm
math.exp(x)     # Exponential (e^x)
math.pow(x, y)  # Power (x^y)
math.floor(x)   # Floor
math.ceil(x)    # Ceiling
math.fabs(x)    # Absolute value (float)
```

### Available Built-in Functions

```python
abs, min, max, sum, len, round
int, float, str, bool, list, dict
range, enumerate, zip, sorted, any, all
```

### Debug Function

```python
log("Debug message")  # Print debug output to execution log
```

---

## Example Strategies

### Simple RSI Strategy

```python
class RSIStrategy:
    def should_trade(self, data: MarketData) -> Decision:
        symbol = data.trigger_symbol
        market_data = data.get_market_data(symbol)
        price = market_data.get("price", 0)
        rsi = data.get_indicator(symbol, "RSI14", "1h")
        rsi_value = rsi.get("value", 50) if rsi else 50

        # Check if we have a position
        position = data.positions.get(symbol)

        if position:
            # Close long if RSI overbought
            if position.side == "long" and rsi_value > 70:
                return Decision(
                    operation="close",
                    symbol=symbol,
                    target_portion_of_balance=1.0,
                    leverage=10,
                    min_price=price * 0.995,
                    reason=f"RSI overbought: {rsi_value:.1f}"
                )
        else:
            # Open long if RSI oversold
            if rsi_value < 30:
                return Decision(
                    operation="buy",
                    symbol=symbol,
                    target_portion_of_balance=0.2,
                    leverage=10,
                    max_price=price * 1.005,
                    take_profit_price=price * 1.03,
                    stop_loss_price=price * 0.98,
                    reason=f"RSI oversold: {rsi_value:.1f}"
                )

        return Decision(operation="hold", symbol=symbol, reason="No signal")
```

### Trend Following with EMA

```python
class TrendStrategy:
    def should_trade(self, data: MarketData) -> Decision:
        symbol = data.trigger_symbol
        market_data = data.get_market_data(symbol)
        price = market_data.get("price", 0)

        ema20 = data.get_indicator(symbol, "EMA20", "1h")
        ema50 = data.get_indicator(symbol, "EMA50", "1h")

        position = data.positions.get(symbol)

        # Bullish crossover
        if ema20 > ema50 and not position:
            return Decision(
                operation="buy",
                symbol=symbol,
                target_portion_of_balance=0.3,
                leverage=5,
                max_price=price * 1.002,
                time_in_force="Gtc",
                reason="EMA bullish crossover"
            )

        # Bearish crossover - close position
        if ema20 < ema50 and position and position.side == "long":
            return Decision(
                operation="close",
                symbol=symbol,
                target_portion_of_balance=1.0,
                leverage=10,
                min_price=price * 0.998,
                reason="EMA bearish crossover"
            )

        return Decision(operation="hold", symbol=symbol, reason="Waiting for signal")
```
