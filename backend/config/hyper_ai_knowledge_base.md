# Hyper Alpha Arena Knowledge Base (Supplementary Reference)

This document provides detailed workflows and examples. Core tool usage is defined in the system prompt.

---

## 1. Complete Task Workflows

### 1.1 "Help me set up a complete trading strategy"

```
Step 1: Understand current state
→ Call get_system_overview
→ Check: Does user have wallet? Which exchange? Any existing strategies?

Step 2: Clarify requirements with user
→ Ask: Trading frequency? Risk tolerance? Target symbols? Prompt or Program?

Step 3: Create signal pool (if using signal trigger)
→ Call call_signal_ai with user requirements
→ Signal AI will: query market data → analyze thresholds → create pool

Step 4: Create strategy
→ Call call_prompt_ai OR call_program_ai with user requirements
→ Sub-agent will: design logic → create strategy

Step 5: Create AI Trader
→ Call create_ai_trader to connect: wallet + signal pool + strategy
→ Confirm with user before enabling

Step 6: Verify setup
→ Call get_trader_details to confirm configuration
→ Explain to user how to monitor
```

### 1.2 "Help me create a signal pool"

```
Step 1: Gather requirements
→ Ask user: Which symbol? Which exchange? How often should it trigger? Long/short/both?

Step 2: Delegate to Signal AI
→ Call call_signal_ai with complete requirements
→ DO NOT try to create signal pool yourself

Step 3: Report result
→ Tell user what Signal AI created
→ Explain the trigger conditions in simple terms
```

### 1.3 "Why is my AI Trader not trading?"

```
Step 1: Diagnose
→ Call diagnose_trader_issues with trader ID
→ This checks: enabled status, signal pool, wallet balance, recent triggers

Step 2: Check decisions
→ Call get_decision_list to see recent decisions
→ Look for HOLD decisions and their reasons

Step 3: Explain to user
→ Common reasons: signal not triggering, strategy deciding HOLD, insufficient balance
→ Suggest specific fixes based on diagnosis
```

### 1.4 "Analyze my recent trades"

```
Step 1: Delegate to Attribution AI
→ Call call_attribution_ai with analysis request
→ Include: time period, specific questions

Step 2: Report findings
→ Summarize Attribution AI's analysis
→ Highlight actionable insights
```

---

## 2. Common Mistakes to Avoid

### Mistake 1: Guessing Signal IDs or Thresholds
```
❌ save_signal_pool(signal_ids=[1, 2, 3, 4, 5])  # These IDs don't exist!
❌ save_signal_pool(signals=[{threshold: 0.5}])  # How do you know 0.5 is right?
✅ call_signal_ai(task="创建信号池...")  # Let Signal AI design it with market data
```

### Mistake 2: Writing Code/Prompts Yourself
```
❌ save_prompt(template_text="你自己写的提示词...")  # May miss important variables
❌ save_program(code="你自己写的代码...")  # May have bugs or miss API patterns
✅ call_prompt_ai(task="创建提示词...")  # Prompt AI knows all available variables
✅ call_program_ai(task="创建程序...")  # Program AI knows the execution environment
```

### Mistake 3: Using Sub-Agent for Queries
```
❌ call_signal_ai(task="获取所有信号列表")  # Sub-agent is for CREATING, not querying
✅ get_signal_pools()  # Use query tool for queries
```

---

## 3. Restricted Operations

These operations require user to do manually:
- Wallet setup — Hyperliquid: create an API Wallet on the Hyperliquid website, then paste the agent private key and master wallet address in [AI Trader](/#trader-management) → click trader → bind wallet. Binance: paste API key + secret key.
- Wallet deletion
- API key management

When user asks for these, guide them to the AI Trader page and explain the security requirement.

---

## 4. FAQ

### Q: Signal triggered but AI decided HOLD, why?
Signal triggering means "time to analyze", not "must trade". The strategy evaluates all factors and may decide HOLD because:
- Market regime unfavorable
- Already have a position
- Risk parameters not met
- Price moved too fast

### Q: How to test without real money?
1. Use Hyperliquid testnet — create an API Wallet on [Hyperliquid Testnet API page](https://app.hyperliquid-testnet.xyz/API), then bind it in the system
2. Use Binance testnet (separate API keys needed)
3. Run backtests on historical data

### Q: Can I have multiple AI Traders?
Yes. Common setups:
- Different traders for different symbols
- Different traders for different strategies
- Same signal pool, different strategies (conservative vs aggressive)

### Q: How do I connect Hyper Insight wallet signals?
Hyper Insight wallet signals are configured in two places:

1. Track wallets on `https://hyper.akooi.com/`
2. Open Hyper Alpha Arena → `Signals` → `Wallet Tracking`
3. Enable the Hyper Insight connection
4. Create a wallet-tracking signal pool from the synced wallet list

Suggested user-facing guidance:
- Explain Hyper Insight as the wallet intelligence site where users track wallets and review wallet behavior
- Explain Hyper Alpha Arena as the place where those tracked wallet signals are connected and used
- Prefer product paths and links over internal implementation details
- Do not mention internal tool names, API names, or tokens in user replies
- Do not quote raw status enums or raw field names in normal replies; translate them into product language

Important rules:
- Hyper Insight is the source of truth for tracked wallets
- Hyper Alpha Arena consumes synced wallets but does not edit them locally
- New synced wallets do not automatically enter an existing pool
- Wallet-tracking pools do not support backtest in Phase 5
- Wallet Tracking and Strategy Radar are different Hyper Insight capabilities and should not be described as the same flow

### Q: Can Hyper AI tell me which wallets are currently tracked?
Yes.

Recommended usage:
- First confirm `Hyper Alpha Arena -> Signals -> Wallet Tracking` is connected
- Then read the exact wallet list currently synced into this Hyper Alpha Arena session
- Then choose one of those wallets for deeper analysis if needed

Status wording:
- If login is required, say the user needs to log in to Hyper Alpha Arena
- If sync is not enabled yet, say Wallet Tracking is not connected yet
- If sync is enabled but there are no wallets, say there are no synced wallets yet
- Avoid raw backend wording such as `waiting_for_token`

### Q: Can Hyper AI analyze a tracked wallet for me?
Yes, if the wallet is already tracked in Hyper Insight and Wallet Tracking sync is connected in Hyper Alpha Arena.

Important limitation:
- Hyper AI can read detailed address data and recent fills
- Recent fills are only a recent activity window, not the wallet's complete all-time trade history
- Hyper AI should treat style conclusions as analysis based on available data, not as guaranteed full-history facts

If analysis is unavailable:
- First check whether the user is logged in to Hyper Alpha Arena
- First check `Hyper Alpha Arena -> Signals -> Wallet Tracking` and confirm sync is connected
- Then confirm the wallet already appears in the synced wallet list
- If sync is connected and the wallet is already visible in the synced list but analysis still fails, explain that the problem is system-side rather than a tracking issue

### Q: How do I find strategy ideas?
Strategy Radar is the Hyper Insight area for browsing current strategy ideas before turning one into a Prompt or Program.

Product boundaries:
- Hyper Insight entry: `https://hyper.akooi.com/strategy-radar`
- Hyper Alpha Arena linkage: Hyper AI can query it after login, and Prompt / Program pages can link users to open it
- Strategy Radar is not part of `Signals -> Wallet Tracking`
- Wallet Tracking is for synced wallets and wallet signals; Strategy Radar is for strategy ideas

Recommended usage:
- If the user does not know what strategy to build, guide them to open Strategy Radar first
- If the user asks Hyper AI for current candidates, first check Strategy Radar's supported symbol/period combinations
- If the requested symbol is unsupported, explain that Strategy Radar does not currently cover it and suggest supported symbols
- Treat candidates as reference logic, not as performance-ranked or guaranteed-profitable strategies
- Do not mention internal query tool names when explaining this flow to users

Important rules:
- Strategy Radar candidates are quality-filtered by validation and recency, not by backtested profit
- Do not describe Strategy Radar as a realtime scanning engine, a performance leaderboard, or a source of guaranteed profitable strategies
- Prebuilt Prompt and Program templates are test templates for learning the workflow
- Users should adapt and test any candidate before live trading
- If the user is not logged in, tell them to use the top-right `Login` button inside Hyper Alpha Arena first

### Q: What data does a wallet signal event contain?
Wallet signals arrive as `wallet_event` inside trigger_context (AI Trader) or input_data (Program Trader).

Common envelope fields (all event types):
- `address`: wallet address (lowercase)
- `event_type`: position_change, equity_change, funding, transfer, liquidation
- `event_level`: normal, significant, critical
- `tier`: "realtime" (paid WS fills) or "polling" (snapshot diff)
- `summary`: human-readable description (e.g. "Real-time: opened ETH $50,000")
- `event_timestamp`: UTC milliseconds

Unified position_change detail fields:
- `action`: open, close, add, reduce, flip, update
- `direction`: long, short, flat
- `start_position`: position size before the action
- `end_position`: position size after the action
- `old_value`: previous position notional value in USD
- `new_value`: current position notional value in USD
- `notional_value`: normalized position notional reference in USD
- `entry_price`: entry price when available
- `leverage`: leverage when available
- `unrealized_pnl`: unrealized PnL when available
- `liquidation_price`: liquidation price when available

Realtime position_change extra detail (from aggregated fills):
- `start_position`: position size before fills
- `end_position`: position size after fills
- `total_size`: sum of absolute fill sizes
- `notional_value`: total notional in USD
- `average_price`: volume-weighted average fill price
- `closed_pnl`: realized PnL from this batch (null if none)
- `fills_count`: number of fills aggregated
- `fills`: array of raw fill objects with coin, px, sz, dir, side, time, closedPnl

Polling position_change extra detail (from snapshot comparison):
- `absolute_change`: |new_value - old_value|
- `relative_change`: absolute_change / |old_value|
- `current_position`: normalized current position snapshot when available
- `previous_position`: normalized previous position snapshot when available
- `source_event_type`: open_position, increase_position, reduce_position, close_position, direction_reversal
- Legacy compatibility fields may still appear in runtime payloads, but new logic should prefer the unified fields above

Polling equity_change detail:
- `old_equity`: previous total equity
- `new_equity`: current total equity
- `absolute_change`: |new_equity - old_equity|
- `relative_change`: absolute_change / |old_equity|

Key differences between realtime and polling:
- Realtime has full fill details (prices, sizes, PnL per fill)
- Polling uses before/after position snapshots (no individual fill data)
- Realtime events have tier="realtime", polling events have tier="polling"
- The summary prefix indicates the source: "Real-time:" vs "Polling:"

### Q: How should a copy-trading strategy use wallet signals?
When writing a strategy that follows wallet signals:
- Check `event_type == "position_change"` to filter for trading actions
- Use `detail.action` to determine what the tracked wallet did (open/close/add/reduce/flip)
- Use `detail.direction` to know the wallet's position direction after the action
- Use `detail.notional_value` to gauge the normalized trade size
- Use `detail.entry_price`, `detail.leverage`, `detail.unrealized_pnl`, and `detail.liquidation_price` when available
- Use `detail.average_price` and `detail.fills` only when the event includes realtime fill aggregation
- Always check `trigger_market_regime` is null for wallet signals (no local market context)
- The strategy must fetch its own market data if price validation is needed
