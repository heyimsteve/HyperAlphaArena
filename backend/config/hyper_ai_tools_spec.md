# Hyper AI Tools Specification

This document defines the detailed specifications for all Hyper AI tools.

---

## 1. Query Tools (Read-Only)

### 1.1 get_system_overview

**Purpose**: Get a high-level summary of system status including wallets, strategies, traders, and positions.

**Parameters**: None

**Implementation**:
```python
# Query from multiple tables:
# - HyperliquidWallet, BinanceWallet: count by environment, check is_active
# - Account: count active AI Traders
# - PromptTemplate: count user prompts (is_system=false)
# - TradingProgram: count programs
# - SignalPool: count pools by exchange
# - HyperliquidPosition, BinancePosition: count open positions
```

**Return Value**:
```json
{
  "wallets": {
    "hyperliquid": {"testnet": 1, "mainnet": 0},
    "binance": {"testnet": 0, "mainnet": 1}
  },
  "ai_traders": {
    "total": 3,
    "active": 2,
    "using_prompt": 1,
    "using_program": 1
  },
  "strategies": {
    "prompts": 5,
    "programs": 3
  },
  "signal_pools": {
    "hyperliquid": 2,
    "binance": 1
  },
  "open_positions": {
    "hyperliquid_testnet": 1,
    "hyperliquid_mainnet": 0,
    "binance_testnet": 0,
    "binance_mainnet": 2
  }
}
```

**Efficiency**: Simple COUNT queries, no complex joins. O(1) per table.

---

### 1.2 get_wallet_status

**Purpose**: Get wallet balance and position summary (read-only, no credentials exposed).

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| exchange | string | No | "hyperliquid" or "binance" (default: all) |
| environment | string | No | "testnet" or "mainnet" (default: all) |

**Implementation**:
```python
# For Hyperliquid: query HyperliquidAccountSnapshot (latest per wallet)
# For Binance: query BinanceAccountSnapshot (latest per wallet)
# Join with wallet tables to get account_id mapping
# DO NOT expose private_key_encrypted or api_key_encrypted
```

**Return Value**:
```json
{
  "wallets": [
    {
      "exchange": "hyperliquid",
      "environment": "testnet",
      "wallet_address": "0x1234...abcd",
      "trader_id": 1,
      "trader_name": "BTC Trend Trader",
      "balance": {
        "total_equity": 10500.50,
        "available_balance": 8000.00,
        "used_margin": 2500.50
      },
      "positions": [
        {"symbol": "BTC", "size": 0.1, "side": "long", "unrealized_pnl": 150.00}
      ],
      "last_updated": "2026-02-20 10:30 UTC"
    }
  ]
}
```

**Security**: Never expose encrypted keys. Only show wallet_address (public).

---

### 1.3 get_api_reference

**Purpose**: Get API reference documentation for Prompt variables OR Program MarketData/Decision APIs.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| doc_type | string | Yes | "prompt" (Prompt variables) or "program" (MarketData/Decision API) |
| api_type | string | No | For program only: "market", "decision", or "all" (default: all) |
| lang | string | No | Language: "en" or "zh" (default: en) |

**Implementation**:
```python
# For doc_type == "prompt":
#   Read from config/PROMPT_VARIABLES_REFERENCE.md (en) or PROMPT_VARIABLES_REFERENCE_ZH.md (zh)
#   Return full markdown content (same as /api/prompts/variables-reference endpoint)
#
# For doc_type == "program":
#   Return MARKET_API_DOCS and/or DECISION_API_DOCS constants from ai_program_service.py
#   (same as get_api_docs tool in Program AI)
```

**Return Value**:
```json
{
  "doc_type": "prompt",
  "lang": "en",
  "content": "# Prompt Variables Reference\n\nThis document lists all available variables...\n\n## Required Variables\n| Variable | Description |\n|----------|-------------|\n| `{output_format}` | **MUST INCLUDE** - JSON output schema...\n..."
}
```

Or for program:
```json
{
  "doc_type": "program",
  "api_type": "all",
  "content": "## MarketData Object (passed to should_trade as 'data')\n\n### Properties (Direct Access)\n- data.available_balance: float...\n\n## Decision Object\n..."
}
```

**Note**: This tool returns the SAME content as existing endpoints/tools to ensure consistency:
- Prompt: Same as `/api/prompts/variables-reference?lang=xx`
- Program: Same as `get_api_docs` tool in AI Program service

---

### 1.4 get_klines

**Purpose**: Get K-line/candlestick data for a symbol.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| symbol | string | Yes | Trading symbol (e.g., BTC, ETH) |
| period | string | No | 1m, 5m, 15m, 1h, 4h, 1d (default: 1h) |
| limit | int | No | Number of candles (default: 50, max: 200) |
| exchange | string | No | hyperliquid or binance (default: hyperliquid) |

**Implementation**:
```python
# Query CryptoKline table
# Filter by exchange, symbol, period
# Order by timestamp DESC, limit
# Return OHLCV data
```

**Return Value**:
```json
{
  "symbol": "BTC",
  "period": "1h",
  "exchange": "hyperliquid",
  "candles": [
    {
      "time": "2026-02-20 10:00 UTC",
      "open": 97000.00,
      "high": 97500.00,
      "low": 96800.00,
      "close": 97300.00,
      "volume": 1250000.00
    }
  ],
  "count": 50
}
```

**Efficiency**: Index on (exchange, symbol, period, timestamp). Limit to 200 rows.

---

### 1.5 get_market_regime

**Purpose**: Get current market regime classification for a symbol.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| symbol | string | Yes | Trading symbol |
| period | string | No | Time period: 1m, 5m, 15m, 1h, 4h, 1d (default: 1h) |

**Implementation**:
```python
# Use DataProvider.get_regime(symbol, period)
# Returns: regime name, direction, confidence, reason
```

**Return Value**:
```json
{
  "symbol": "BTC",
  "period": "1h",
  "regime": "breakout",
  "direction": "bullish",
  "confidence": 0.85,
  "reason": "Strong CVD with price breaking resistance",
  "components": {
    "cvd_z": 2.1,
    "oi_z": 1.5,
    "price_atr": 0.8,
    "taker_ratio": 2.3
  }
}
```

---

### 1.6 get_market_flow

**Purpose**: Get market flow data (CVD, OI, Funding, etc.) for a symbol.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| symbol | string | Yes | Trading symbol |
| period | string | No | Time period: 1m, 5m, 15m, 1h, 4h, 1d (default: 1h) |
| metrics | array | No | Specific metrics to get (default: all) |

**Implementation**:
```python
# Use DataProvider for each metric:
# CVD, OI, OI_DELTA, TAKER, FUNDING, DEPTH, IMBALANCE
```

**Return Value**:
```json
{
  "symbol": "BTC",
  "period": "1h",
  "flow": {
    "CVD": {"value": 15000000, "interpretation": "Strong buying pressure"},
    "OI": {"value": 850000000, "change_pct": 1.2},
    "OI_DELTA": {"value": 10000000},
    "FUNDING": {"value": 0.0012, "interpretation": "Longs pay shorts"},
    "TAKER": {"buy": 25000000, "sell": 18000000, "ratio": 1.39}
  }
}
```

---

### 1.7 get_system_logs

**Purpose**: Get recent system error/warning logs for troubleshooting.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| level | string | No | "error", "warning", "all" (default: error) |
| limit | int | No | Max entries (default: 20, max: 50) |
| trader_id | int | No | Filter by AI Trader ID |

**Implementation**:
```python
# Option 1: Query from a system_logs table (if exists)
# Option 2: Parse recent log files (less efficient)
# Option 3: Query AIDecisionLog/ProgramExecutionLog for errors
# Recommend Option 3: query execution logs where success=false or executed=false
```

**Return Value**:
```json
{
  "logs": [
    {
      "time": "2026-02-20 10:15 UTC",
      "level": "error",
      "source": "program_execution",
      "trader_id": 1,
      "message": "Insufficient balance for order",
      "details": {"required": 1000, "available": 500}
    }
  ],
  "total": 5
}
```

---

### 1.8 get_contact_config

**Purpose**: Get support channel URLs dynamically.

**Parameters**: None

**Implementation**:
```python
# Fetch from external API: https://www.akooi.com/api/config/contact
# Cache for 1 hour to reduce external calls
# Fallback to hardcoded defaults if API fails
```

**Return Value**:
```json
{
  "twitter": {"url": "https://x.com/GptHammer3309", "enabled": true},
  "telegram": {"url": "https://t.me/+RqxjT7Gttm9hOGEx", "enabled": true},
  "github": {"url": "https://github.com/HammerGPT/Hyper-Alpha-Arena", "enabled": true}
}
```

---

## 2. Diagnostic Tools

### 2.1 diagnose_trader_issues

**Purpose**: Check why an AI Trader is not triggering and provide actionable suggestions.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| trader_id | int | Yes | AI Trader ID to diagnose |

**Implementation**:
```python
# Check sequence:
# 1. Account.is_active == "true"?
# 2. Account.auto_trading_enabled == "true"?
# 3. Has wallet bound? (HyperliquidWallet or BinanceWallet)
# 4. Wallet has balance? (query latest snapshot)
# 5. Has strategy bound? (AccountPromptBinding or AccountProgramBinding)
# 6. Signal pool enabled? (if using signal trigger)
# 7. Cooldown active? (check last_trigger_at vs trigger_interval)
# 8. Recent errors? (query AIDecisionLog/ProgramExecutionLog)
```

**Return Value**:
```json
{
  "trader_id": 1,
  "trader_name": "BTC Trend Trader",
  "status": "issues_found",
  "checks": [
    {"check": "trader_enabled", "passed": true},
    {"check": "wallet_bound", "passed": true, "wallet": "hyperliquid/testnet"},
    {"check": "wallet_balance", "passed": false, "balance": 0, "suggestion": "Deposit funds to wallet"},
    {"check": "strategy_bound", "passed": true, "type": "prompt"},
    {"check": "signal_pool_enabled", "passed": true},
    {"check": "cooldown_active", "passed": true, "next_trigger": "2026-02-20 10:35 UTC"}
  ],
  "summary": "Wallet balance is 0. Deposit funds to enable trading.",
  "recent_errors": []
}
```

---

## 3. Write Operations (Create/Save)

### 3.1 save_signal_pool

**Purpose**: Create or update a signal pool configuration.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| pool_id | int | No | Pool ID to update (omit for create) |
| pool_name | string | Yes | Display name for the pool |
| symbols | array | Yes | Symbols to monitor ["BTC", "ETH"] |
| signal_ids | array | Yes | Signal definition IDs to include |
| logic | string | No | "AND" or "OR" (default: OR) |
| exchange | string | No | "hyperliquid" or "binance" (default: hyperliquid) |
| enabled | bool | No | Enable/disable pool (default: true) |

**Implementation**:
```python
# Validate signal_ids exist in SignalDefinition table
# Validate symbols are supported for the exchange
# If pool_id: UPDATE signal_pools SET ...
# Else: INSERT INTO signal_pools ...
# Return created/updated pool with ID
```

**Return Value**:
```json
{
  "success": true,
  "pool_id": 5,
  "pool_name": "BTC Momentum Signals",
  "action": "created",
  "note": "Signal pool created. Bind it to an AI Trader to start receiving triggers."
}
```

---

### 3.2 save_prompt

**Purpose**: Create or update a trading prompt template.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| prompt_id | int | No | Prompt ID to update (omit for create) |
| name | string | Yes | Display name |
| description | string | No | Brief description |
| template_text | string | Yes | Main prompt content |
| system_template_text | string | No | System prompt (default provided) |

**Implementation**:
```python
# Validate template_text is not empty
# Extract variables from template_text, warn if unknown variables used
# If prompt_id: UPDATE prompt_templates SET ...
# Else: INSERT INTO prompt_templates (is_system=false, created_by='hyper_ai')
# Return created/updated prompt with ID
```

**Return Value**:
```json
{
  "success": true,
  "prompt_id": 12,
  "name": "Trend Following Strategy",
  "action": "updated",
  "variables_detected": ["current_price", "RSI14", "market_regime"],
  "unknown_variables": [],
  "note": "Prompt saved. Changes will apply to bound AI Traders on next trigger."
}
```

---

### 3.3 save_program

**Purpose**: Create or update a trading program.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| program_id | int | No | Program ID to update (omit for create) |
| name | string | Yes | Display name |
| description | string | No | Brief description |
| code | string | Yes | Python strategy code |
| params | object | No | Default parameters JSON |
| icon | string | No | Icon identifier |

**Implementation**:
```python
# Validate code syntax using validate_code logic
# Check for forbidden imports/operations (security)
# If program_id: UPDATE trading_programs SET ...
# Else: INSERT INTO trading_programs (user_id=1)
# Return created/updated program with ID
```

**Return Value**:
```json
{
  "success": true,
  "program_id": 8,
  "name": "RSI Mean Reversion",
  "action": "created",
  "validation": {
    "syntax_valid": true,
    "security_check": "passed"
  },
  "note": "Program saved. Use test_run_code to verify logic before binding to AI Trader."
}
```

---

### 3.4 create_ai_trader

**Purpose**: Create a new AI Trader with LLM config and strategy binding.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| name | string | Yes | Display name for the trader |
| model | string | Yes | LLM model name (e.g., "gpt-4o", "claude-3-5-sonnet") |
| base_url | string | Yes | LLM API base URL |
| api_key | string | Yes | LLM API key |
| strategy_type | string | Yes | "prompt" or "program" |
| strategy_id | int | Yes | Prompt ID or Program ID to bind |
| exchange | string | Yes | "hyperliquid" or "binance" |
| signal_pool_ids | array | No | Signal pool IDs for trigger |
| trigger_interval | int | No | Scheduled trigger interval (seconds) |
| scheduled_trigger_enabled | bool | No | Enable scheduled trigger |

**Implementation**:
```python
# IMPORTANT: Reuse existing API logic from account_routes.py

# Step 1: Test LLM connection FIRST (same as /api/accounts/test-llm)
#   - Call test_llm_connection(model, base_url, api_key)
#   - If fails, return error immediately, do NOT create trader

# Step 2: Create Account record (same as POST /api/accounts/)
#   - create_account(db, user_id=1, name, model, base_url, api_key, ...)
#   - Sets is_active=true, auto_trading_enabled=true

# Step 3: Create AccountStrategyConfig
#   - exchange, signal_pool_ids, trigger_interval, scheduled_trigger_enabled

# Step 4: Bind strategy
#   - If strategy_type == "prompt": Create AccountPromptBinding
#   - If strategy_type == "program": Create AccountProgramBinding

# NOTE: Wallet binding NOT done here - user must configure manually in Settings
```

**Return Value** (success):
```json
{
  "success": true,
  "trader_id": 15,
  "trader_name": "BTC Momentum Trader",
  "llm_config": {
    "model": "gpt-4o",
    "base_url": "https://api.openai.com/v1",
    "connection_tested": true
  },
  "strategy_type": "prompt",
  "strategy_id": 12,
  "exchange": "hyperliquid",
  "wallet_status": "not_configured",
  "note": "AI Trader created. Go to Settings → Wallets to bind a wallet before enabling trading."
}
```

**Return Value** (LLM connection failed):
```json
{
  "success": false,
  "error": "LLM connection test failed",
  "details": "Invalid API key or model not available",
  "note": "Please check your LLM credentials and try again."
}
```

**Security Note**:
- LLM API key is stored encrypted in Account.api_key
- Wallet configuration requires user to manually bind credentials in the AI Trader page (Hyperliquid: API Wallet private key + master wallet address; Binance: API key + secret key)
- This tool does NOT handle wallet setup (security + referral logic)

---

## 4. Implementation Priority

| Priority | Tool | Complexity | Notes |
|----------|------|------------|-------|
| P0 | get_system_overview | Low | Essential for orientation |
| P0 | diagnose_trader_issues | Medium | Critical for troubleshooting |
| P1 | get_wallet_status | Medium | Important for balance checks |
| P1 | save_prompt | Medium | Core workflow |
| P1 | save_program | Medium | Core workflow |
| P1 | create_ai_trader | High | Core workflow, multi-table |
| P2 | get_api_reference | Low | Reuse existing docs |
| P2 | save_signal_pool | Medium | Signal configuration |
| P2 | get_klines | Low | Market data access |
| P2 | get_market_regime | Low | Reuse DataProvider |
| P2 | get_market_flow | Low | Reuse DataProvider |
| P3 | get_system_logs | Medium | Troubleshooting |
| P3 | get_contact_config | Low | External API call |

---

## 5. Security Considerations

1. **No credential exposure**: Never return encrypted keys or secrets
2. **Read-only by default**: Query tools cannot modify data
3. **Wallet setup restricted**: create_ai_trader does NOT configure wallets
4. **Code validation**: save_program must validate for security risks
5. **Rate limiting**: Consider limits on expensive operations (backtest, klines)

---

## 6. Efficiency Guidelines

1. **Use indexes**: All queries should use indexed columns
2. **Limit results**: Always enforce max limits on list queries
3. **Cache static data**: Cache contact_config, variable definitions
4. **Batch queries**: Combine related queries where possible
5. **Avoid N+1**: Use JOINs instead of loop queries
