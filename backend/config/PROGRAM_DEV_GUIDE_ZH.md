# 程序化交易开发指南

本指南记录了编写程序化交易策略所需的所有 API 和数据结构。

## 重要：编写阈值前先查询市场数据

**在策略代码中设置任何阈值之前**，请使用 `query_market_data` 工具查询当前市场数据。指标值差异很大：
- CVD 范围可能是 -5000万 到 +5000万
- BTC 的 OI 可能是 1亿 到 5亿
- ATR 根据波动率从 200 到 1500 不等

**错误做法**：猜测阈值，如 `if cvd > 1000`
**正确做法**：先查询当前 CVD 值，再设置合适的阈值

## 目录

1. [代码结构](#代码结构)
2. [MarketData 对象](#marketdata-对象)
3. [Decision 对象](#decision-对象)
4. [可用指标](#可用指标)
5. [资金流指标](#资金流指标)
6. [辅助函数](#辅助函数)
7. [示例策略](#示例策略)

---

## 代码结构

您的策略必须定义一个包含 `should_trade(self, data: MarketData)` 方法的类，该方法返回 `Decision` 对象。

### 三层架构

建议将 `should_trade` 方法组织为三个逻辑层：

```
┌─────────────────────────────────────────────────────────┐
│  1. 变量层 - 获取所有需要的数据                           │
│     - 获取价格、指标、资金流数据                          │
│     - 检查持仓和账户状态                                  │
│     - 将值提取到命名变量中                                │
├─────────────────────────────────────────────────────────┤
│  2. 逻辑层 - 评估条件                                    │
│     - 风险管理检查（保证金、余额）                         │
│     - 平仓条件（止盈/止损、强平风险）                      │
│     - 开仓条件（指标信号）                                │
├─────────────────────────────────────────────────────────┤
│  3. 决策层 - 返回相应的 Decision                         │
│     - 构建包含所有必填字段的 Decision 对象                │
│     - 包含 reason 和 trading_strategy                   │
│     - 无条件满足时默认返回 "hold"                         │
└─────────────────────────────────────────────────────────┘
```

这种分层使策略更易于阅读、调试和维护。

**最简示例**（始终持有）：
```python
class MyStrategy:
    def should_trade(self, data: MarketData) -> Decision:
        return Decision(
            operation="hold",
            symbol=data.trigger_symbol,
            reason="无信号"
        )
```

**真实示例**（RSI + CVD 确认）：
```python
class RSI_CVD_Strategy:
    def should_trade(self, data: MarketData) -> Decision:
        symbol = data.trigger_symbol
        market_data = data.get_market_data(symbol)
        price = market_data.get("price", 0)

        # 获取指标
        rsi = data.get_indicator(symbol, "RSI14", "1h")
        rsi_value = rsi.get("value", 50)

        # 获取资金流指标
        cvd_data = data.get_flow(symbol, "CVD", "1h")
        cvd_current = cvd_data.get("current", 0)

        # 检查是否有持仓
        position = data.positions.get(symbol)

        # 风险管理：保证金使用率过高时不交易
        if data.margin_usage_percent > 80:
            return Decision(
                operation="hold",
                symbol=symbol,
                reason="保证金使用率过高"
            )

        if position:
            # 平仓逻辑：止盈或止损
            if position.side == "long":
                # RSI 超买时止盈
                if rsi_value > 70:
                    return Decision(
                        operation="close",
                        symbol=symbol,
                        target_portion_of_balance=1.0,
                        leverage=position.leverage,
                        min_price=price * 0.995,  # 允许 0.5% 滑点
                        reason=f"止盈：RSI {rsi_value:.1f} 超买"
                    )

                # 价格接近强平价时止损
                if price < position.liquidation_price * 1.1:
                    return Decision(
                        operation="close",
                        symbol=symbol,
                        target_portion_of_balance=1.0,
                        leverage=position.leverage,
                        min_price=price * 0.99,
                        reason="紧急平仓：接近强平价"
                    )
        else:
            # 开仓逻辑：RSI 超卖 + CVD 为正
            if rsi_value < 30 and cvd_current > 0:
                # 检查余额是否足够
                if data.available_balance < 100:
                    return Decision(
                        operation="hold",
                        symbol=symbol,
                        reason="余额不足"
                    )

                return Decision(
                    operation="buy",
                    symbol=symbol,
                    target_portion_of_balance=0.2,  # 使用 20% 余额
                    leverage=5,
                    max_price=price * 1.005,  # 允许 0.5% 滑点
                    take_profit_price=price * 1.03,  # 3% 止盈目标
                    stop_loss_price=price * 0.98,    # 2% 止损
                    reason=f"开仓：RSI {rsi_value:.1f} 超卖，CVD {cvd_current:.0f} 为正",
                    trading_strategy="RSI 超卖且 CVD 确认时买入。3% 止盈或 2% 止损。"
                )

        return Decision(
            operation="hold",
            symbol=symbol,
            reason="无信号"
        )
```

---

## MarketData 对象

`MarketData` 对象提供市场数据和账户信息的访问。

### 属性

| 属性 | 类型 | 描述 |
|------|------|------|
| `trigger_symbol` | `str` | 触发本次评估的币种（定时触发时为空字符串 `""`）|
| `trigger_type` | `str` | 触发类型：`'signal'` 或 `'scheduled'` |
| `available_balance` | `float` | 可用余额（USD）|
| `total_equity` | `float` | 账户总权益（包含未实现盈亏）|
| `used_margin` | `float` | 已使用保证金 |
| `margin_usage_percent` | `float` | 保证金使用率（0-100 范围）|
| `maintenance_margin` | `float` | 维持保证金要求 |
| `positions` | `Dict[str, Position]` | 当前持仓（以币种为键）|
| `recent_trades` | `List[Trade]` | 最近平仓交易记录 |
| `open_orders` | `List[Order]` | 当前开放订单（止盈止损、限价单）|
| `signal_source_type` | `Optional[str]` | 钱包来源信号时为 `"wallet_tracking"`，否则为 `None` |
| `wallet_event` | `Optional[Dict]` | 当 `signal_source_type == "wallet_tracking"` 时提供的钱包事件 payload |

### 钱包信号触发上下文

当 `data.signal_source_type == "wallet_tracking"` 时，策略可以从 `data.wallet_event` 读取钱包事件。

钱包事件公共外层字段：
- `address`
- `event_type`
- `event_level`
- `tier`
- `summary`
- `detail`
- `event_timestamp`

统一后的 `position_change.detail` 字段：
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

仅实时聚合附加字段：
- `fills_count`
- `total_size`
- `average_price`
- `closed_pnl`
- `fills`

仅轮询快照附加字段：
- `absolute_change`
- `relative_change`
- `current_position`
- `previous_position`
- `source_event_type`

示例：
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

### 定时触发 vs 信号触发（重要）

您的策略可能由信号池或定时间隔触发。请正确处理两种情况：

| 字段 | 信号触发 | 定时触发 |
|------|---------|---------|
| `trigger_type` | `"signal"` | `"scheduled"` |
| `trigger_symbol` | `"BTC"`（触发的币种）| `""`（空字符串）|
| `triggered_signals` | `[{信号详情...}]` | `[]`（空列表）|
| `trigger_market_regime` | `RegimeInfo(...)` | `None` |
| `signal_pool_name` | `"OI 异动监控"` | `""`（空字符串）|

**示例：处理两种触发类型**：
```python
def should_trade(self, data: MarketData) -> Decision:
    if data.trigger_type == "scheduled":
        # 定时触发：只检查平仓条件，不开新仓
        # 必须显式指定币种，因为 trigger_symbol 为空
        symbol = "BTC"
        if symbol in data.positions:
            # 检查现有持仓的平仓条件...
            pass
        return Decision(operation="hold", symbol=symbol, reason="定时检查 - 无操作")

    # 信号触发：使用 trigger_symbol 和 triggered_signals
    symbol = data.trigger_symbol
    for sig in data.triggered_signals:
        if sig.get("metric") == "oi_delta" and sig.get("current_value", 0) > 1.0:
            # 检测到 OI 异动，评估入场...
            pass

    return Decision(operation="hold", symbol=symbol, reason="无入场信号")
```

**MarketData 对象示例**（无持仓）：

> **注意**：以下数值仅为格式示例。实际运行时，所有账户数据（余额、持仓、订单等）均从您的交易账户实时获取。价格数据通过 `data.get_market_data(symbol)` 方法获取。

```python
# 示例：假设账户余额 $10,000，无持仓
data.trigger_symbol = "BTC"
data.trigger_type = "signal"
data.available_balance = 10000.0   # 实际值来自您的账户
data.total_equity = 10000.0        # 实际值来自您的账户
data.used_margin = 0.0
data.margin_usage_percent = 0.0
data.maintenance_margin = 0.0
data.positions = {}                # 实际值来自您的账户
data.recent_trades = []            # 实际值来自您的账户
data.open_orders = []              # 实际值来自您的账户
```

**MarketData 对象示例**（有持仓 - 真实数据）：
```python
# 真实账户状态，持有 BTC 多头仓位
# 价格通过 data.get_market_data("BTC") 获取
data.trigger_symbol = "BTC"
data.trigger_type = "signal"
data.available_balance = 101.93    # 扣除保证金后的可用余额
data.total_equity = 259.44         # 账户总权益（USDC）
data.used_margin = 157.50          # 持仓占用保证金
data.margin_usage_percent = 60.7   # 60.7% 保证金使用率
data.maintenance_margin = 78.75    # 约为初始保证金的 50%
data.positions = {
    "BTC": Position(
        symbol="BTC",
        side="long",
        size=0.001,                # 0.001 BTC
        entry_price=95400.0,       # 平均开仓价 $95,400.00
        unrealized_pnl=0.03,       # +$0.03 (+0.03% ROE)
        leverage=1,                # 1 倍全仓杠杆
        liquidation_price=0.0      # Cross margin 模式
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
        price=76320.0,             # 限价
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

### Position 对象

`Position` 对象表示一个持仓。

**字段**：
| 字段 | 类型 | 描述 |
|------|------|------|
| `symbol` | `str` | 交易币种（如 "BTC"）|
| `side` | `str` | 持仓方向：`"long"` 或 `"short"` |
| `size` | `float` | 持仓数量（多空都为正数）|
| `entry_price` | `float` | 平均开仓价格 |
| `unrealized_pnl` | `float` | 未实现盈亏（USD）|
| `leverage` | `int` | 杠杆倍数（1-50）|
| `liquidation_price` | `float` | 强平价格 |
| `opened_at` | `int` 或 `None` | 开仓时间戳（毫秒）|
| `opened_at_str` | `str` 或 `None` | 可读的开仓时间（如 "2026-01-15 10:30:00 UTC"）|
| `holding_duration_seconds` | `float` 或 `None` | 持仓时长（秒）|
| `holding_duration_str` | `str` 或 `None` | 可读的持仓时长（如 "2h 30m"）|

**使用示例**：
```python
# 检查是否有持仓
if "BTC" in data.positions:
    pos = data.positions["BTC"]

    # 检查持仓方向
    if pos.side == "long":
        # 多头持仓，考虑止盈或加仓
        if pos.unrealized_pnl > 100:
            # 止盈
            pass

    # 时间止损：持仓超过4小时则平仓
    if pos.holding_duration_seconds and pos.holding_duration_seconds > 4 * 3600:
        # 持仓时间过长，考虑平仓
        pass

    # 检查是否接近强平
    market_data = data.get_market_data("BTC")
    current_price = market_data.get("price", 0)
    if pos.side == "long" and current_price < pos.liquidation_price * 1.1:
        # 平仓 - 太接近强平价
        pass
```

### Trade 对象

`Trade` 对象表示最近平仓的交易。

**字段**：
| 字段 | 类型 | 描述 |
|------|------|------|
| `symbol` | `str` | 交易币种（如 "BTC"）|
| `side` | `str` | 平仓方向：`"Long"` 或 `"Short"` |
| `size` | `float` | 交易数量 |
| `price` | `float` | 平仓价格 |
| `timestamp` | `int` | 平仓时间戳（毫秒）|
| `pnl` | `float` | 已实现盈亏（USD）|
| `close_time` | `str` | 平仓时间（UTC 字符串格式）|

**使用示例**：
```python
# 检查上次交易时间，避免频繁交易
if data.recent_trades:
    last_trade = data.recent_trades[0]  # 最近的在前
    time_since_last = time.time() * 1000 - last_trade.timestamp
    if time_since_last < 2 * 60 * 60 * 1000:  # 2 小时（毫秒）
        return Decision(operation="hold", symbol=symbol, reason="冷却期")
```

### Order 对象

`Order` 对象表示当前开放的订单（止盈止损、限价单）。

**字段**：
| 字段 | 类型 | 描述 |
|------|------|------|
| `order_id` | `int` | 订单唯一 ID |
| `symbol` | `str` | 交易币种 |
| `side` | `str` | 订单方向：`"Buy"` 或 `"Sell"` |
| `direction` | `str` | `"Open Long"`, `"Open Short"`, `"Close Long"`, `"Close Short"` |
| `order_type` | `str` | 订单类型：<br>- `"Market"`: 市价单<br>- `"Limit"`: 限价单<br>- `"Stop Market"`: 止损市价单<br>- `"Stop Limit"`: 止损限价单<br>- `"Take Profit Market"`: 止盈市价单<br>- `"Take Profit Limit"`: 止盈限价单 |
| `size` | `float` | 订单数量 |
| `price` | `float` | 限价 |
| `trigger_price` | `float` | 触发价格（止损/止盈订单）|
| `reduce_only` | `bool` | 是否为只减仓订单 |
| `timestamp` | `int` | 下单时间戳（毫秒）|

**使用示例**：
```python
# 检查是否已有止损订单
has_stop_loss = any(
    o.symbol == "BTC" and o.order_type == "Stop Limit"
    for o in data.open_orders
)
if not has_stop_loss and "BTC" in data.positions:
    # 需要设置止损
    pass

# 检查上次下单时间，避免重复下单
btc_orders = [o for o in data.open_orders if o.symbol == "BTC"]
if btc_orders:
    last_order_time = max(o.timestamp for o in btc_orders)
    # ...
```

### 方法

#### get_indicator(symbol, indicator, period)

获取技术指标值。

**返回类型**：`Dict[str, Any]` - 包含指标值的字典。结构因指标类型而异。

**常见返回结构**：

1. **简单指标**（RSI, MA, EMA, ATR, VWAP, OBV）：
```python
{
    "value": float,        # 最新值（最近一根K线）
    "series": [float]      # 完整历史序列
}
```

2. **MACD**：
```python
{
    "macd": float,        # MACD 线（最新值）
    "signal": float,      # 信号线（最新值）
    "histogram": float    # MACD - Signal（最新值）
}
```

3. **布林带**：
```python
{
    "upper": float,       # 上轨（最新值）
    "middle": float,      # 中轨 / SMA（最新值）
    "lower": float        # 下轨（最新值）
}
```

4. **随机指标**：
```python
{
    "k": float,           # %K 线（0-100，最新值）
    "d": float            # %D 线（0-100，最新值）
}
```

**实际返回示例**：
```python
# RSI14 - value 是最新值，series 包含历史值
data.get_indicator("BTC", "RSI14", "1h")
# 返回: {"value": 46.76, "series": [50.0, 0.0, 0.0, 5.94, ...]}

# MACD - 三条线的最新值
data.get_indicator("BTC", "MACD", "1h")
# 返回: {"macd": -73.27, "signal": -81.88, "histogram": 8.60}

# 布林带 - 上中下轨的最新值
data.get_indicator("BTC", "BOLL", "1h")
# 返回: {"upper": 97569.63, "middle": 96727.55, "lower": 95885.47}

# 随机指标 - %K 和 %D 值（0-100 范围）
data.get_indicator("BTC", "STOCH", "1h")
# 返回: {"k": 51.35, "d": 51.78}

# EMA20 - 注意：series 有预热期（前 19 个值为 0.0）
data.get_indicator("BTC", "EMA20", "1h")
# 返回: {"value": 96457.32, "series": [0.0, 0.0, ..., 96457.32]}
```

**使用示例**：
```python
# RSI - 检查超卖/超买
rsi = data.get_indicator("BTC", "RSI14", "1h")
rsi_value = rsi.get("value", 50)
if rsi_value < 30:
    # 超卖 - 潜在买入
    pass
elif rsi_value > 70:
    # 超买 - 潜在卖出
    pass

# MACD - 检查交叉
macd = data.get_indicator("BTC", "MACD", "1h")
if macd.get("histogram", 0) > 0:
    # MACD 在信号线上方 - 看涨
    pass

# 布林带 - 检查价格位置
boll = data.get_indicator("BTC", "BOLL", "1h")
market_data = data.get_market_data("BTC")
current_price = market_data.get("price", 0)
if current_price < boll.get("lower", 0):
    # 价格低于下轨 - 超卖
    pass
```

#### get_klines(symbol, period, count=50)

获取 K 线数据。

**返回类型**：`List[Kline]` - Kline 对象列表，按时间升序排列（从旧到新）。

**Kline 对象字段**：
| 字段 | 类型 | 描述 |
|------|------|------|
| `timestamp` | `int` | Unix 时间戳（秒）|
| `open` | `float` | 开盘价 |
| `high` | `float` | 最高价 |
| `low` | `float` | 最低价 |
| `close` | `float` | 收盘价 |
| `volume` | `float` | 成交量 |

**实际返回示例**：
```python
klines = data.get_klines("BTC", "1h", count=3)
# 返回（按时间升序）：
[
    Kline(timestamp=1768658400, open=95673.0, high=95673.0, low=95160.0, close=95400.0, volume=2.98375),
    Kline(timestamp=1768647600, open=95119.0, high=95336.0, low=95087.0, close=95336.0, volume=285.85),
    Kline(timestamp=1768651200, open=95336.0, high=95408.0, low=95254.0, close=95255.0, volume=113.77)
]
```

**使用示例**：
```python
# 获取最近 50 根 1 小时 K 线
klines = data.get_klines("BTC", "1h", count=50)

# 检查是否有数据
if len(klines) < 5:
    return Decision(operation="hold", symbol="BTC", reason="K线数据不足")

# 访问最新 K 线
latest = klines[-1]
current_price = latest.close

# 计算价格趋势（最近 5 根 K 线）
last_5_closes = [k.close for k in klines[-5:]]
is_uptrend = all(last_5_closes[i] < last_5_closes[i+1] for i in range(4))

# 检查高成交量
avg_volume = sum(k.volume for k in klines[-20:]) / 20
if latest.volume > avg_volume * 2:
    # 高成交量 K 线 - 潜在突破
    pass
```

#### get_market_data(symbol)

获取完整的市场数据（价格、成交量、持仓量、资金费率等）。

**复用 AI Trader 的数据层**：此方法与 AI Trader 的 `{BTC_market_data}` 变量使用相同的数据源。

**返回类型**：`Dict[str, Any]` - 包含以下字段的字典：

| 字段 | 类型 | 描述 |
|------|------|------|
| `symbol` | `str` | 交易对符号 |
| `price` | `float` | 当前标记价格 |
| `oracle_price` | `float` | 预言机价格 |
| `change24h` | `float` | 24小时价格变化（USD） |
| `percentage24h` | `float` | 24小时价格变化百分比 |
| `volume24h` | `float` | 24小时成交量（USD） |
| `open_interest` | `float` | 持仓量（USD） |
| `funding_rate` | `float` | 资金费率 |

**实际返回示例**：
```python
market_data = data.get_market_data("BTC")
# 返回：
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

**使用示例**：
```python
# 获取 BTC 完整市场数据
btc_data = data.get_market_data("BTC")

# 检查价格变化
if btc_data.get("percentage24h", 0) > 5:
    # 24小时涨幅超过 5%
    pass

# 检查资金费率
funding_rate = btc_data.get("funding_rate", 0)
if funding_rate > 0.0001:
    # 资金费率过高，多头拥挤
    pass

# 检查持仓量变化
oi = btc_data.get("open_interest", 0)
volume = btc_data.get("volume24h", 0)
if oi > 0 and volume / oi > 0.5:
    # 高换手率，市场活跃
    pass
```

**获取价格的推荐方式**：
```python
# 获取完整市场数据（推荐）
market_data = data.get_market_data("BTC")
price = market_data.get("price", 0)
funding_rate = market_data.get("funding_rate", 0)

# 如果只需要价格
btc_data = data.get_market_data("BTC")
price = btc_data.get("price", 0)
```

**重要**：`data.prices` 已移除，请始终使用 `data.get_market_data(symbol)` 获取价格。

#### get_flow(symbol, metric, period)

获取资金流指标。

**参数**：
- `symbol`: 交易对符号（如 "BTC"）
- `metric`: 指标类型（"CVD", "OI", "OI_DELTA", "TAKER", "FUNDING", "DEPTH", "IMBALANCE"）
- `period`: 时间周期（"1m", "5m", "15m", "1h", "4h", "1d"）

**实际返回示例**（CVD）：
```python
cvd = data.get_flow("BTC", "CVD", "1h")
# 返回：
{
  "current": -4284734.70,
  "last_5": [4388145.60, 13977923.30, -6439359.71, 12102468.00, -4284734.70],
  "cumulative": -7838887.07,
  "period": "1h"
}
```

**使用示例**：
```python
cvd = data.get_flow("BTC", "CVD", "1h")
oi_delta = data.get_flow("ETH", "OI_DELTA", "15m")
```

#### get_regime(symbol, period)

获取市场状态分类，包含完整的指标数据。

**返回类型**：`RegimeInfo` 对象，包含以下属性：
- `regime`: str - 市场状态类型
- `conf`: float - 置信度分数 (0.0-1.0)
- `direction`: str - 市场方向 (`bullish`, `bearish`, `neutral`)
- `reason`: str - 人类可读的解释
- `indicators`: dict - 用于分类的指标值

**实际返回示例**：
```python
regime = data.get_regime("BTC", "1h")
# 返回：
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

**市场状态类型**（7种）：
| 状态 | 描述 |
|------|------|
| `breakout` | 强势突破，成交量确认 |
| `absorption` | 大单被吸收但价格不动（潜在反转）|
| `stop_hunt` | 突破后快速反转（流动性猎杀）|
| `exhaustion` | RSI 极端且 CVD 背离（趋势衰竭）|
| `trap` | 价格突破但 CVD/OI 背离（假突破）|
| `continuation` | 趋势延续，指标一致 |
| `noise` | 无明确形态，低置信度 |

**方向类型**（3种）：
| 方向 | 描述 |
|------|------|
| `bullish` | 基于 CVD、Taker 比率和价格行为的上涨偏向 |
| `bearish` | 下跌偏向 |
| `neutral` | 无明确方向偏向 |

**指标定义**：
- `cvd_ratio`: CVD / 总成交额。正值 = 净买入压力
- `oi_delta`: 持仓量变化百分比
- `taker_ratio`: Taker 买卖比。>1 = 激进买入
- `price_atr`: 价格变化 / ATR。衡量波动强度
- `rsi`: RSI(14) 动量指标。>70 超买，<30 超卖

**使用示例**：
```python
regime = data.get_regime("BTC", "1h")
if regime.regime == "breakout" and regime.conf > 0.7 and regime.direction == "bullish":
    # 高置信度看涨突破
    pass

# 访问底层指标
if regime.indicators.get("rsi", 50) < 30:
    # RSI 超卖
    pass
```

#### get_price_change(symbol, period)

获取指定周期的价格变化。

**参数**：
- `symbol`: 交易对符号（如 "BTC"）
- `period`: 时间周期（"1m", "5m", "15m", "1h", "4h", "1d"）

**实际返回示例**：
```python
change = data.get_price_change("BTC", "5m")
# 返回：
{
  "change_percent": 0.141,   # 价格变化百分比（0.141 = +0.141%）
  "change_usd": 129.0        # 绝对美元变化
}
```

#### get_factor(symbol, factor_name, period="5m")

获取指定 K 线周期上的因子值和有效性指标。

**参数**：
- `symbol`: 交易对符号（如 "BTC"）
- `factor_name`: 因子名称（如 "RSI21"、"MOM10" 或自定义因子名）
- `period`: 时间周期（"1m", "5m", "15m", "1h", "4h", "1d"）。为了兼容旧代码，省略时默认按 `5m` 计算。

**实际返回示例**：
```python
f = data.get_factor("BTC", "RSI21", "1h")
# 返回：
{
  "factor_name": "RSI21",
  "symbol": "BTC",
  "period": "1h",
  "id": 5,
  "expression": "RSI(close, 21)",
  "description": "RSI 21周期",
  "category": "momentum",
  "value": 0.0234,
  "ic": 0.0512,
  "icir": 1.35,
  "win_rate": 58.2,
  "decay_half_life_hours": -1
}
```

**使用示例**：
```python
f = data.get_factor("BTC", "MOM10", "5m")
if f["value"] is not None and f["value"] > 0.02:
    if f.get("icir") and abs(f["icir"]) > 1.0:
        log(f"MOM10 触发: value={f['value']}, ICIR={f['icir']}, expr={f['expression']}")
```

**注意**：Prompt 因子变量、Program 实盘和 Program 回测的因子值计算口径必须保持一致。回测模式下，`value` 基于当前回测时间点之前的历史 K 线计算；有效性字段（如 `ic`、`icir`）在回测中不可用。

---

## Decision 对象

`Decision` 对象告诉系统执行什么交易操作。

### 必填字段

| 字段 | 类型 | 描述 |
|------|------|------|
| `operation` | `str` | **必填**：`'buy'`、`'sell'`、`'hold'` 或 `'close'` |
| `symbol` | `str` | **必填**：交易币种（如 `'BTC'`、`'ETH'`）|

### 订单执行字段

| 字段 | 类型 | 描述 |
|------|------|------|
| `target_portion_of_balance` | `float` | buy/sell/close 必填：0.1-1.0（余额的 10%-100%）|
| `leverage` | `int` | buy/sell/close 必填：1-50（默认：10）|
| `max_price` | `float` | buy 或 close short 必填：最高入场价格 |
| `min_price` | `float` | sell 或 close long 必填：最低入场价格 |
| `time_in_force` | `str` | 可选：`'Ioc'`、`'Gtc'`、`'Alo'`（默认：`'Ioc'`）|

### 止盈止损字段

| 字段 | 类型 | 描述 |
|------|------|------|
| `take_profit_price` | `float` | 可选：止盈触发价格 |
| `stop_loss_price` | `float` | 可选：止损触发价格 |
| `tp_execution` | `str` | 可选：`'market'` 或 `'limit'`（默认：`'limit'`）|
| `sl_execution` | `str` | 可选：`'market'` 或 `'limit'`（默认：`'limit'`）|

### 文档字段

| 字段 | 类型 | 描述 |
|------|------|------|
| `reason` | `str` | 可选：决策说明 |
| `trading_strategy` | `str` | 可选：入场论点、风险控制 |

### Time In Force 选项

- **Ioc**（立即成交或取消）：立即成交，未成交部分取消
- **Gtc**（撤销前有效）：订单在订单簿中等待直到成交或取消
- **Alo**（仅添加流动性）：仅挂单，如果会吃单则拒绝

### 价格精度控制

**建议**：计算价格时保持与市场价格一致的小数位数。

- 市场价格通过 `data.get_market_data(symbol)` 获取
- 不同币种精度不同：BTC/ETH 通常 1-2 位小数，小币种可能需要 4-8 位
- 使用 `round()` 函数控制精度，避免出现过多小数位（如 93622.54776373146）

**为什么需要控制精度？**
- 保持代码可读性
- 适应不同币种的精度要求
- 避免浮点数运算产生的过高精度

---

## 可用指标

使用 `data.get_indicator(symbol, indicator, period)` 获取以下指标。

**重要**：在策略代码中设置阈值前，请使用 `query_market_data` 工具查询当前指标值。

### RSI（相对强弱指数）

| 指标 | 返回类型 | 值范围 | 描述 |
|------|----------|--------|------|
| `RSI14` | `{'value': float}` | 0-100 | 14 周期 RSI，标准动量指标 |
| `RSI7` | `{'value': float}` | 0-100 | 7 周期 RSI，对价格变化响应更快 |

**解读**：
- `< 30`：超卖，潜在买入信号
- `> 70`：超买，潜在卖出信号
- `30-70`：中性区域

**示例**：
```python
rsi = data.get_indicator("BTC", "RSI14", "1h")
if rsi.get('value', 50) < 30:
    # 超卖条件
```

### 移动平均线

| 指标 | 返回类型 | 典型范围 | 描述 |
|------|----------|----------|------|
| `MA5` | `{'value': float}` | 与价格相同 | 5 周期简单移动平均线 |
| `MA10` | `{'value': float}` | 与价格相同 | 10 周期简单移动平均线 |
| `MA20` | `{'value': float}` | 与价格相同 | 20 周期简单移动平均线 |
| `EMA20` | `{'value': float}` | 与价格相同 | 20 周期指数移动平均线 |
| `EMA50` | `{'value': float}` | 与价格相同 | 50 周期指数移动平均线 |
| `EMA100` | `{'value': float}` | 与价格相同 | 100 周期指数移动平均线 |

**解读**：
- 价格在 MA 上方：看涨趋势
- 价格在 MA 下方：看跌趋势
- 金叉（短期 MA > 长期 MA）：买入信号
- 死叉（短期 MA < 长期 MA）：卖出信号

**示例**：
```python
ema20 = data.get_indicator("BTC", "EMA20", "1h").get('value', 0)
ema50 = data.get_indicator("BTC", "EMA50", "1h").get('value', 0)
market_data = data.get_market_data("BTC")
price = market_data.get("price", 0)
if ema20 > ema50 and price > ema20:
    # 强势上涨趋势
```

### MACD

| 指标 | 返回类型 | 描述 |
|------|----------|------|
| `MACD` | `{'macd': float, 'signal': float, 'histogram': float}` | 移动平均收敛散度 |

**典型值**（BTC）：
- `macd`：-1000 到 +1000（随价格水平变化）
- `signal`：与 macd 范围相似
- `histogram`：-500 到 +500

**解读**：
- `histogram > 0`：看涨动量
- `histogram < 0`：看跌动量
- MACD 上穿信号线：买入信号
- MACD 下穿信号线：卖出信号

**示例**：
```python
macd = data.get_indicator("BTC", "MACD", "1h")
if macd.get('histogram', 0) > 0 and macd.get('macd', 0) > macd.get('signal', 0):
    # 看涨 MACD 交叉
```

### 布林带

| 指标 | 返回类型 | 描述 |
|------|----------|------|
| `BOLL` | `{'upper': float, 'middle': float, 'lower': float}` | 布林带（20 周期，2 倍标准差）|

**典型值**：与价格相同量级（如 BTC ~95000-98000）

**解读**：
- 价格接近上轨：可能超买
- 价格接近下轨：可能超卖
- 带宽表示波动率

**示例**：
```python
boll = data.get_indicator("BTC", "BOLL", "1h")
market_data = data.get_market_data("BTC")
price = market_data.get("price", 0)
if price < boll.get('lower', 0):
    # 价格跌破下轨 - 潜在反转
```

### ATR（平均真实波幅）

| 指标 | 返回类型 | 典型范围 | 描述 |
|------|----------|----------|------|
| `ATR14` | `{'value': float}` | 200-1500（BTC）| 14 周期波动率指标 |

**解读**：
- ATR 越高 = 波动率越高
- 用于仓位管理和止损设置
- ATR * 2 是常用的止损距离

**示例**：
```python
atr = data.get_indicator("BTC", "ATR14", "1h").get('value', 500)
stop_loss = price - (atr * 2)  # 2 倍 ATR 止损
```

### 其他指标

| 指标 | 返回类型 | 描述 |
|------|----------|------|
| `VWAP` | `{'value': float}` | 成交量加权平均价格（与价格相同量级）|
| `STOCH` | `{'k': float, 'd': float}` | 随机指标（0-100 范围）|
| `OBV` | `{'value': float}` | 能量潮指标（累计值，可能是百万级）|

### 支持的周期

- `1m` - 1 分钟
- `5m` - 5 分钟
- `15m` - 15 分钟
- `1h` - 1 小时
- `4h` - 4 小时

---

## 资金流指标

使用 `data.get_flow(symbol, metric, period)` 获取资金流数据。

**重要**：所有资金流指标返回**包含完整数据结构的字典**，包括 `current` 当前值、`last_5` 历史数据，以及指标特定的字段。设置阈值前请使用 `query_market_data` 工具查询当前值。

### CVD（累积成交量差）

**返回结构**：
```python
{
    "current": float,      # 当前周期的差值（主动买入 - 主动卖出名义价值）
    "last_5": [float],     # 最近 5 个周期的差值
    "cumulative": float,   # 回溯窗口内的累积总和
    "period": str          # 时间周期（如 "1h"）
}
```

**实际测试返回示例**：
```python
{
    "current": -389433.36764,
    "last_5": [-11428730.30583, 8420546.86251, 13435392.63129, 48471120.86648, -389433.36764],
    "cumulative": 84066208.88112,
    "period": "1h"
}
```

**解读**：
- 正 CVD：更多主动买入（主动买入 > 主动卖出）
- 负 CVD：更多主动卖出
- CVD 上升 + 价格上涨：健康上涨趋势
- CVD 下降 + 价格上涨：看跌背离（潜在反转）

**使用示例**：
```python
cvd_data = data.get_flow("BTC", "CVD", "1h")
current_cvd = cvd_data.get("current", 0)
last_5 = cvd_data.get("last_5", [])

# 检查 CVD 是否呈上升趋势（最近 3 个周期递增）
if len(last_5) >= 3 and last_5[-1] > last_5[-2] > last_5[-3]:
    # CVD 上升趋势 - 看涨信号
    pass
```

### OI（持仓量变化）

**返回结构**：
```python
{
    "current": float,      # 当前周期的 OI 变化（USD）
    "last_5": [float],     # 最近 5 个周期的 OI 变化值
    "period": str          # 时间周期
}
```

**实际测试返回示例**：
```python
{
    "current": 18257834.64,
    "last_5": [32615864.12, -9186781.88, -3550037.11, -16232245.9, 18257834.64],
    "period": "1h"
}
```

**解读**：
- OI 上升 + 价格上涨：新多头入场，看涨
- OI 上升 + 价格下跌：新空头入场，看跌
- OI 下降：平仓中，趋势减弱

**使用示例**：
```python
oi_data = data.get_flow("BTC", "OI", "1h")
current_oi = oi_data.get("current", 0)
# 正值 = OI 增加，负值 = OI 减少
```

### OI_DELTA（持仓量变化百分比）

**返回结构**：
```python
{
    "current": float,      # 当前周期的 OI 变化百分比
    "last_5": [float],     # 最近 5 个周期的 OI 变化百分比
    "period": str          # 时间周期
}
```

**实际测试返回示例**：
```python
{
    "current": 0.6016847209056745,
    "last_5": [1.083886578409779, -0.30247846838551423, -0.1168357287082588, -0.5347910774531256, 0.6016847209056745],
    "period": "1h"
}
```

**解读**：
- `> 1%`：大量新仓位开立
- `< -1%`：大量仓位平仓
- 接近 0：市场稳定

**使用示例**：
```python
oi_delta = data.get_flow("BTC", "OI_DELTA", "1h")
if oi_delta.get("current", 0) > 1.0:  # 1% 增加
    # 大量新仓位涌入
    pass
```

### TAKER（主动买卖量）

**返回结构**：
```python
{
    "buy": float,              # 主动买入名义价值（USD）
    "sell": float,             # 主动卖出名义价值（USD）
    "ratio": float,            # 买卖比率（buy/sell）
    "ratio_last_5": [float],   # 最近 5 个周期的比率
    "volume_last_5": [float],  # 最近 5 个周期的总成交量
    "period": str              # 时间周期
}
```

**实际测试返回示例**：
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

**解读**：
- `ratio > 1.5`：强劲买入攻势
- `ratio < 0.7`：强劲卖出攻势
- `ratio 0.9-1.1`：市场平衡

**使用示例**：
```python
taker = data.get_flow("BTC", "TAKER", "1h")
ratio = taker.get("ratio", 1.0)
if ratio > 1.5:
    # 激进买入
    pass
```

### FUNDING（资金费率）

**返回结构**：
```python
{
    "current": float,       # 当前资金费率（K线显示单位：原始值 × 1000000）
    "current_pct": float,   # 当前费率百分比（如 0.00125 = 0.00125%）
    "change": float,        # 相对上一周期的费率变化（K线显示单位）
    "change_pct": float,    # 费率变化百分比
    "last_5": [float],      # 最近 5 个周期的费率（K线显示单位）
    "annualized": float,    # 年化费率百分比
    "period": str           # 时间周期
}
```

**实际测试返回示例**：
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

**单位说明**：
- 1 bps（基点）= 0.01% = 0.0001
- K线显示单位：原始值 × 1000000（如 12.5 表示 0.00125%）
- 信号触发基于费率**变化**，而非绝对值

**解读**：
- 正资金费率：多头付给空头，市场看涨情绪
- 负资金费率：空头付给多头，市场看跌情绪
- 极端资金费率变化：潜在反转信号

**使用示例**：
```python
funding = data.get_flow("BTC", "FUNDING", "1h")
change = funding.get("change", 0)
if change > 5:  # 资金费率增加 5 bps
    # 情绪转向看涨
    pass
```

### DEPTH（订单簿深度）

**返回结构**：
```python
{
    "bid": float,              # 买单深度（百万 USD）
    "ask": float,              # 卖单深度（百万 USD）
    "ratio": float,            # 买卖比率
    "ratio_last_5": [float],   # 最近 5 个周期的比率
    "spread": float,           # 买卖价差
    "period": str              # 时间周期
}
```

**实际测试返回示例**：
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

**解读**：
- `ratio > 1`：买单多于卖单（看涨）
- `ratio < 1`：卖单多于买单（看跌）

**使用示例**：
```python
depth = data.get_flow("BTC", "DEPTH", "1h")
ratio = depth.get("ratio", 1.0)
if ratio > 1.5:
    # 强劲买方支撑
    pass
```

### IMBALANCE（订单簿失衡）

**返回结构**：
```python
{
    "current": float,      # 当前失衡分数（-1 到 +1）
    "last_5": [float],     # 最近 5 个周期的失衡分数
    "period": str          # 时间周期
}
```

**实际测试返回示例**：
```python
{
    "current": -0.4278551379875647,
    "last_5": [0.9130708905853642, -0.997789987014244, 0.9982126029729156, 0.5769891251393954, -0.4278551379875647],
    "period": "1h"
}
```

**解读**：
- `> 0.3`：强买方失衡（看涨）
- `< -0.3`：强卖方失衡（看跌）
- 接近 0：订单簿平衡

**使用示例**：
```python
imbalance = data.get_flow("BTC", "IMBALANCE", "1h")
current = imbalance.get("current", 0)
if current > 0.5:
    # 强劲看涨失衡
    pass
```

---

## 辅助函数

### 可用沙箱对象

```python
time   # 预注入的沙箱对象，用于时间戳操作
math   # 预注入的沙箱对象，用于数学函数
```

不要在 Program Trader 代码里写 `import time` 或 `import math`。
请直接使用 `time.time()` 和 `math.sqrt()`。

### time 模块函数

```python
time.time()           # 当前 Unix 时间戳（秒，浮点数）
time.time() * 1000    # 当前时间戳（毫秒，用于与 trade.timestamp 比较）
```

**使用示例**：
```python
# 检查距离上次交易的时间，避免频繁交易
if data.recent_trades:
    last_trade = data.recent_trades[0]
    time_since_last_ms = time.time() * 1000 - last_trade.timestamp
    if time_since_last_ms < 2 * 60 * 60 * 1000:  # 2小时（毫秒）
        return Decision(operation="hold", symbol=symbol, reason="冷却期")
```

### 可用数学函数

```python
math.sqrt(x)    # 平方根
math.log(x)     # 自然对数
math.log10(x)   # 以 10 为底的对数
math.exp(x)     # 指数函数 (e^x)
math.pow(x, y)  # 幂运算 (x^y)
math.floor(x)   # 向下取整
math.ceil(x)    # 向上取整
math.fabs(x)    # 绝对值（浮点数）
```

### 可用内置函数

```python
abs, min, max, sum, len, round
int, float, str, bool, list, dict
range, enumerate, zip, sorted, any, all
```

### 调试函数

```python
log("调试信息")  # 输出调试信息到执行日志
```

---

## 示例策略

### 简单 RSI 策略

```python
class RSIStrategy:
    def should_trade(self, data: MarketData) -> Decision:
        symbol = data.trigger_symbol
        market_data = data.get_market_data(symbol)
        price = market_data.get("price", 0)
        rsi = data.get_indicator(symbol, "RSI14", "1h")
        rsi_value = rsi.get("value", 50) if rsi else 50

        # 检查是否有持仓
        position = data.positions.get(symbol)

        if position:
            # RSI 超买时平多仓
            if position.side == "long" and rsi_value > 70:
                return Decision(
                    operation="close",
                    symbol=symbol,
                    target_portion_of_balance=1.0,
                    leverage=10,
                    min_price=price * 0.995,
                    reason=f"RSI 超买: {rsi_value:.1f}"
                )
        else:
            # RSI 超卖时开多仓
            if rsi_value < 30:
                return Decision(
                    operation="buy",
                    symbol=symbol,
                    target_portion_of_balance=0.2,
                    leverage=10,
                    max_price=price * 1.005,
                    take_profit_price=price * 1.03,
                    stop_loss_price=price * 0.98,
                    reason=f"RSI 超卖: {rsi_value:.1f}"
                )

        return Decision(operation="hold", symbol=symbol, reason="无信号")
```

### EMA 趋势跟踪策略

```python
class TrendStrategy:
    def should_trade(self, data: MarketData) -> Decision:
        symbol = data.trigger_symbol
        market_data = data.get_market_data(symbol)
        price = market_data.get("price", 0)

        ema20 = data.get_indicator(symbol, "EMA20", "1h")
        ema50 = data.get_indicator(symbol, "EMA50", "1h")

        position = data.positions.get(symbol)

        # 金叉做多
        if ema20 > ema50 and not position:
            return Decision(
                operation="buy",
                symbol=symbol,
                target_portion_of_balance=0.3,
                leverage=5,
                max_price=price * 1.002,
                time_in_force="Gtc",
                reason="EMA 金叉"
            )

        # 死叉平仓
        if ema20 < ema50 and position and position.side == "long":
            return Decision(
                operation="close",
                symbol=symbol,
                target_portion_of_balance=1.0,
                leverage=10,
                min_price=price * 0.998,
                reason="EMA 死叉"
            )

        return Decision(operation="hold", symbol=symbol, reason="等待信号")
```
