import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'react-hot-toast'
import Cookies from 'js-cookie'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Plus, Trash2, Edit, Activity, Eye, Sparkles, FlaskConical, Wifi, WifiOff, RefreshCw, CircleHelp } from 'lucide-react'
import SignalPreviewChart from './SignalPreviewChart'
import AiSignalChatModal from './AiSignalChatModal'
import MarketRegimeConfig from './MarketRegimeConfig'
import PacmanLoader from '../ui/pacman-loader'
import { useCollectionDays } from '@/lib/useCollectionDays'

// Exchange SVG logos
const HyperliquidLogo = ({ className = '' }: { className?: string }) => (
  <svg width="16" height="16" viewBox="0 0 144 144" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
    <path d="M144 71.6991C144 119.306 114.866 134.582 99.5156 120.98C86.8804 109.889 83.1211 86.4521 64.116 84.0456C39.9942 81.0113 37.9057 113.133 22.0334 113.133C3.5504 113.133 0 86.2428 0 72.4315C0 58.3063 3.96809 39.0542 19.736 39.0542C38.1146 39.0542 39.1588 66.5722 62.132 65.1073C85.0007 63.5379 85.4184 34.8689 100.247 22.6271C113.195 12.0593 144 23.4641 144 71.6991Z" fill="#50e3c2"/>
  </svg>
)

const BinanceLogo = ({ className = '' }: { className?: string }) => (
  <img src="/static/binance_logo.svg" alt="Binance" width="16" height="16" className={className} />
)

// Exchange badge component
const ExchangeBadge = ({ exchange, size = 'sm' }: { exchange: string; size?: 'sm' | 'xs' }) => {
  const isHyperliquid = exchange === 'hyperliquid'
  const textSize = size === 'xs' ? 'text-[10px]' : 'text-xs'
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${isHyperliquid ? 'bg-emerald-500/10 text-emerald-400' : 'bg-yellow-500/10 text-yellow-400'}`}>
      {isHyperliquid ? <HyperliquidLogo /> : <BinanceLogo />}
      <span className={textSize}>{isHyperliquid ? 'Hyperliquid' : 'Binance'}</span>
    </span>
  )
}

// Types
interface SignalDefinition {
  id: number
  signal_name: string
  description: string | null
  trigger_condition: TriggerCondition
  enabled: boolean
  exchange: string
  created_at: string
  updated_at: string
}

interface TriggerCondition {
  metric?: string
  operator?: string
  threshold?: number
  time_window?: string
  logic?: string
  conditions?: TriggerCondition[]
}

interface SignalPool {
  id: number
  pool_name: string
  signal_ids: number[]
  symbols: string[]
  enabled: boolean
  logic: 'OR' | 'AND'
  exchange: string
  source_type?: 'market_signals' | 'wallet_tracking'
  source_config?: {
    addresses?: string[]
    event_types?: string[]
    sync_mode?: string
  }
  created_at: string
}

interface MarketRegimeData {
  regime: string
  direction: string
  confidence: number
  details?: Record<string, unknown>
}

interface SignalTriggerLog {
  id: number
  signal_id: number | null
  pool_id: number | null
  symbol: string
  trigger_value: Record<string, unknown> | null
  triggered_at: string
  market_regime: MarketRegimeData | null
}

interface WalletTrackingRuntimeStatus {
  enabled: boolean
  status: string
  tier: string | null
  synced_addresses: string[]
  last_connected_at: string | null
  last_message_at: string | null
  last_event_at: string | null
  last_error: string | null
  active_wallet_pool_count: number
  token_synced_at: string | null
}

function parseUtcNaiveString(value?: string | null): Date | null {
  if (!value) return null
  const normalized = /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`
  const parsed = new Date(normalized)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function formatWalletRuntimeTime(value?: string | null): string {
  const parsed = parseUtcNaiveString(value)
  return parsed ? parsed.toLocaleString() : '-'
}

function formatWalletTier(t: (key: string, fallback?: string) => string, tier?: string | null): string {
  if (tier === 'paid') {
    return t('signals.walletTracking.tierPremium', 'Premium (second-level detection)')
  }
  if (tier === 'free') {
    return t('signals.walletTracking.tierFree', 'Free (minute-level detection)')
  }
  return '-'
}

interface FactorItem {
  name: string
  category: string
  description: string
  expression: string
  source: string
}

type PoolSourceType = 'market_signals' | 'wallet_tracking'

const WALLET_EVENT_TYPES = [
  'position_change',
  'equity_change',
  'fill',
  'funding',
  'transfer',
  'liquidation',
]

function formatWalletEventType(t: (key: string, fallback?: string) => string, eventType: string): string {
  switch (eventType) {
    case 'position_change':
      return t('signals.walletTracking.eventTypePositionChange', 'Position Change')
    case 'equity_change':
      return t('signals.walletTracking.eventTypeEquityChange', 'Equity Change')
    case 'fill':
      return t('signals.walletTracking.eventTypeFill', 'Trade Fill')
    case 'funding':
      return t('signals.walletTracking.eventTypeFunding', 'Funding')
    case 'transfer':
      return t('signals.walletTracking.eventTypeTransfer', 'Transfer')
    case 'liquidation':
      return t('signals.walletTracking.eventTypeLiquidation', 'Liquidation')
    default:
      return eventType
  }
}

function formatWalletActionLabel(t: (key: string, fallback?: string) => string, action?: string | null): string {
  switch (action) {
    case 'open':
      return t('signals.walletTracking.actionOpen', 'Opened')
    case 'add':
      return t('signals.walletTracking.actionAdd', 'Increased')
    case 'reduce':
      return t('signals.walletTracking.actionReduce', 'Reduced')
    case 'close':
      return t('signals.walletTracking.actionClose', 'Closed')
    case 'flip':
      return t('signals.walletTracking.actionFlip', 'Flipped')
    case 'update':
      return t('signals.walletTracking.actionUpdate', 'Updated')
    default:
      return action || '-'
  }
}

function formatWalletDirectionLabel(t: (key: string, fallback?: string) => string, direction?: string | null): string {
  switch (direction) {
    case 'long':
      return t('signals.walletTracking.directionLong', 'Long')
    case 'short':
      return t('signals.walletTracking.directionShort', 'Short')
    case 'flat':
      return t('signals.walletTracking.directionFlat', 'Flat')
    default:
      return direction || '-'
  }
}

function formatWalletMetricValue(value: unknown, digits = 2): string | null {
  if (typeof value !== 'number' || Number.isNaN(value)) return null
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  })
}

function formatShortAddress(address?: string | null): string {
  if (!address) return '-'
  if (address.length <= 14) return address
  return `${address.slice(0, 6)}...${address.slice(-4)}`
}

function sortByCreatedAtDesc<T extends { created_at?: string | null }>(items: T[]): T[] {
  return [...items].sort((a, b) => {
    const aTime = parseUtcNaiveString(a.created_at)?.getTime() || 0
    const bTime = parseUtcNaiveString(b.created_at)?.getTime() || 0
    return bTime - aTime
  })
}

// API functions
const API_BASE = '/api/signals'

async function fetchSignals(): Promise<{ signals: SignalDefinition[]; pools: SignalPool[] }> {
  const res = await fetch(API_BASE)
  if (!res.ok) throw new Error('Failed to fetch signals')
  return res.json()
}

async function createSignal(data: Partial<SignalDefinition>): Promise<SignalDefinition> {
  const res = await fetch(`${API_BASE}/definitions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create signal')
  return res.json()
}

async function updateSignal(id: number, data: Partial<SignalDefinition>): Promise<SignalDefinition> {
  const res = await fetch(`${API_BASE}/definitions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update signal')
  return res.json()
}

async function deleteSignal(id: number): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/definitions/${id}`, { method: 'DELETE' })
  return res.json()
}

async function createPool(data: Partial<SignalPool>): Promise<SignalPool> {
  const res = await fetch(`${API_BASE}/pools`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create pool')
  return res.json()
}

async function updatePool(id: number, data: Partial<SignalPool>): Promise<SignalPool> {
  const res = await fetch(`${API_BASE}/pools/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update pool')
  return res.json()
}

async function deletePool(id: number): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/pools/${id}`, { method: 'DELETE' })
  return res.json()
}

// Create signal pool from AI-generated config
async function createPoolFromConfig(config: {
  name: string
  symbol: string
  description?: string
  logic: string
  signals: Array<{ metric: string; operator: string; threshold: number; time_window?: string }>
  exchange?: string
}): Promise<{ success: boolean; pool: SignalPool; signals: SignalDefinition[] }> {
  const res = await fetch(`${API_BASE}/create-pool-from-config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to create pool' }))
    throw new Error(error.detail || 'Failed to create pool')
  }
  return res.json()
}

async function fetchTriggerLogs(options: {
  poolId?: number
  symbol?: string
  limit?: number
  offset?: number
} = {}): Promise<{ logs: SignalTriggerLog[]; total: number }> {
  const { poolId, symbol, limit = 50, offset = 0 } = options
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (poolId) params.set('pool_id', String(poolId))
  if (symbol) params.set('symbol', symbol)
  const res = await fetch(`${API_BASE}/logs?${params}`)
  if (!res.ok) throw new Error('Failed to fetch logs')
  return res.json()
}

async function fetchWalletTrackingStatus(): Promise<WalletTrackingRuntimeStatus> {
  const res = await fetch(`${API_BASE}/wallet-tracking/status`)
  if (!res.ok) throw new Error('Failed to fetch wallet tracking status')
  return res.json()
}

async function updateWalletTrackingRuntime(data: { enabled: boolean; access_token?: string }): Promise<WalletTrackingRuntimeStatus> {
  const res = await fetch(`${API_BASE}/wallet-tracking/runtime`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to update wallet tracking runtime')
  return res.json()
}

async function fetchPoolBacktest(poolId: number, symbol: string): Promise<any> {
  const params = new URLSearchParams({ symbol })
  const res = await fetch(`${API_BASE}/pool-backtest/${poolId}?${params}`)
  if (!res.ok) throw new Error('Failed to fetch pool backtest')
  return res.json()
}

// Market Regime batch query
interface MarketRegimeResult {
  symbol: string
  regime: string
  direction: string
  confidence: number
  reason: string
}

async function fetchBatchMarketRegime(
  symbols: string[],
  timeframe: string,
  timestamps: number[]
): Promise<Map<number, MarketRegimeResult>> {
  const results = new Map<number, MarketRegimeResult>()
  // Query regime for each unique timestamp
  const uniqueTimestamps = [...new Set(timestamps)]
  for (const ts of uniqueTimestamps) {
    try {
      const res = await fetch('/api/market-regime/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbols,
          timeframe,
          timestamp_ms: ts,
        }),
      })
      if (res.ok) {
        const data = await res.json()
        if (data.results && data.results.length > 0) {
          results.set(ts, data.results[0])
        }
      }
    } catch (e) {
      console.error(`Failed to fetch regime for timestamp ${ts}:`, e)
    }
  }
  return results
}

interface MetricAnalysis {
  status: string
  symbol: string
  metric: string
  period: string
  sample_count: number
  time_range_hours: number
  warning?: string
  statistics?: {
    mean: number
    std: number
    min: number
    max: number
    abs_percentiles: { p75: number; p90: number; p95: number; p99: number }
  }
  suggestions?: {
    aggressive: { threshold: number; description: string }
    moderate: { threshold: number; description: string; recommended?: boolean }
    conservative: { threshold: number; description: string }
  }
  message?: string
}

async function fetchMetricAnalysis(symbol: string, metric: string, period: string, exchange: string = 'hyperliquid'): Promise<MetricAnalysis> {
  const params = new URLSearchParams({ symbol, metric, period, exchange })
  const res = await fetch(`${API_BASE}/analyze?${params}`)
  if (!res.ok) throw new Error('Failed to analyze metric')
  return res.json()
}

async function fetchFactorLibrary(): Promise<FactorItem[]> {
  const res = await fetch('/api/factors/library')
  if (!res.ok) return []
  const data = await res.json()
  return (data.factors || []).filter((f: FactorItem) =>
    f.source !== 'builtin'
  )
}

// Factor category labels for display
const FACTOR_CATEGORY_LABELS: Record<string, string> = {
  trend: 'Trend',
  momentum: 'Momentum',
  volatility: 'Volatility',
  volume: 'Volume',
  statistical: 'Statistical',
  composite: 'Composite',
  custom: 'Custom',
}

// Constants aligned with K-line indicators (MarketFlowIndicators.tsx)
const METRICS = [
  { value: 'oi_delta', label: 'OI Delta', desc: 'Open Interest change %. Positive=inflow, Negative=outflow' },
  { value: 'cvd', label: 'CVD', desc: 'Cumulative Volume Delta. Positive=buyers dominate, Negative=sellers dominate' },
  { value: 'funding', label: 'Funding Rate Change', desc: 'Funding rate change (aligned with K-line chart). Positive=rate increasing, Negative=rate decreasing' },
  { value: 'depth_ratio', label: 'Depth Ratio', desc: 'Bid/Ask depth ratio. >1=more bids, <1=more asks' },
  { value: 'taker_ratio', label: 'Taker Ratio', desc: 'Log taker ratio ln(buy/sell). >0=buyers, <0=sellers. Symmetric around 0' },
  { value: 'order_imbalance', label: 'Order Imbalance', desc: 'Order book imbalance (-1 to 1). Positive=buy pressure' },
  { value: 'oi', label: 'OI (Absolute)', desc: 'Absolute Open Interest value in USD' },
  { value: 'taker_volume', label: 'Taker Volume', desc: 'Composite signal: direction + ratio + volume threshold', isComposite: true },
  { value: 'macd', label: 'MACD', desc: 'MACD technical indicator events: golden cross, death cross, etc.', isEvent: true },
  { value: 'price_change', label: 'Price Change', desc: 'Price change % over time window. Formula: (current-prev)/prev*100. Positive=up, Negative=down' },
  { value: 'volatility', label: 'Volatility', desc: 'Price volatility % over time window. Formula: (high-low)/low*100. Always positive, detects swings' },
]

// Direction options for taker_volume composite signal
const TAKER_DIRECTIONS = [
  { value: 'any', label: 'Any Direction', desc: 'Trigger on either buy or sell dominance' },
  { value: 'buy', label: 'Buy Dominant', desc: 'Only trigger when buyers dominate' },
  { value: 'sell', label: 'Sell Dominant', desc: 'Only trigger when sellers dominate' },
]

// MACD event types
const MACD_EVENT_TYPES = [
  { value: 'golden_cross', label: 'Golden Cross', desc: 'MACD crosses above Signal line (bullish)' },
  { value: 'death_cross', label: 'Death Cross', desc: 'MACD crosses below Signal line (bearish)' },
  { value: 'histogram_positive', label: 'Histogram Positive', desc: 'Histogram turns positive (same as golden cross)' },
  { value: 'histogram_negative', label: 'Histogram Negative', desc: 'Histogram turns negative (same as death cross)' },
  { value: 'macd_above_zero', label: 'MACD Above Zero', desc: 'MACD line crosses above zero (bullish confirmation)' },
  { value: 'macd_below_zero', label: 'MACD Below Zero', desc: 'MACD line crosses below zero (bearish confirmation)' },
]

const OPERATORS = [
  { value: 'abs_greater_than', label: '|x| > (Absolute)', desc: 'Triggers when absolute value exceeds threshold (ignores direction)' },
  { value: 'greater_than', label: '> (Greater)', desc: 'Triggers when value is greater than threshold' },
  { value: 'less_than', label: '< (Less)', desc: 'Triggers when value is less than threshold' },
  { value: 'equals', label: '= (Equals)', desc: 'Triggers when value equals threshold' },
]

const TIME_WINDOWS = [
  { value: '1m', label: '1 min', desc: 'Very short-term, high noise' },
  { value: '3m', label: '3 min', desc: 'Short-term signals' },
  { value: '5m', label: '5 min', desc: 'Recommended for most signals' },
  { value: '15m', label: '15 min', desc: 'Medium-term, more reliable' },
  { value: '30m', label: '30 min', desc: 'Longer-term trends' },
  { value: '1h', label: '1 hour', desc: 'Major trend changes only' },
  { value: '2h', label: '2 hours', desc: 'Long-term trend confirmation' },
  { value: '4h', label: '4 hours', desc: 'Very long-term, major moves only' },
]
// Symbols are now loaded dynamically from Hyperliquid watchlist (see watchlistSymbols state)

function formatDeps(deps: string[], t: (key: string) => string): string {
  const keyMap: [RegExp, string][] = [
    [/Signal Pool/i, 'common.dependencySignalPool'],
    [/Bound to.*Trader/i, 'common.dependencyActiveBinding'],
    [/Program Binding/i, 'common.dependencyProgramBinding'],
    [/AI Strategy/i, 'common.dependencyActiveBinding'],
    [/TriggerConfig/i, 'common.dependencyActiveBinding'],
  ]
  const messages = new Set<string>()
  for (const dep of deps) {
    const match = keyMap.find(([re]) => re.test(dep))
    messages.add(match ? t(match[1]) : dep)
  }
  return Array.from(messages).join(' ')
}

export default function SignalManager() {
  const { t } = useTranslation()
  const collectionDays = useCollectionDays()
  const [signals, setSignals] = useState<SignalDefinition[]>([])
  const [pools, setPools] = useState<SignalPool[]>([])
  const [logs, setLogs] = useState<SignalTriggerLog[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('signals')

  // Signal dialog state
  const [signalDialogOpen, setSignalDialogOpen] = useState(false)
  const [editingSignal, setEditingSignal] = useState<SignalDefinition | null>(null)
  const [signalForm, setSignalForm] = useState({
    signal_name: '',
    description: '',
    metric: 'oi_delta',
    operator: 'abs_greater_than',
    threshold: 5,
    time_window: '5m',
    enabled: true,
    exchange: 'hyperliquid',
    // taker_volume composite fields
    direction: 'any',
    ratio_threshold: 1.5,
    volume_threshold: 50000,
    // MACD event fields
    event_types: ['golden_cross', 'death_cross'] as string[],
  })

  // Pool dialog state
  const [poolDialogOpen, setPoolDialogOpen] = useState(false)
  const [editingPool, setEditingPool] = useState<SignalPool | null>(null)
  const [poolForm, setPoolForm] = useState({
    pool_name: '',
    signal_ids: [] as number[],
    symbols: [] as string[],
    enabled: true,
    logic: 'OR' as 'OR' | 'AND',
    exchange: 'hyperliquid',
    source_type: 'market_signals' as PoolSourceType,
    source_config: {
      addresses: [] as string[],
      event_types: ['position_change', 'fill', 'liquidation'] as string[],
      sync_mode: 'ws_only',
    },
  })

  // Metric analysis state
  const [metricAnalysis, setMetricAnalysis] = useState<MetricAnalysis | null>(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)

  // Factor library state
  const [factorLibrary, setFactorLibrary] = useState<FactorItem[]>([])
  const [factorCategory, setFactorCategory] = useState<string>('all')
  const [factorSearch, setFactorSearch] = useState('')

  // Signal preview state
  const [previewDialogOpen, setPreviewDialogOpen] = useState(false)
  const [previewSignal, setPreviewSignal] = useState<SignalDefinition | null>(null)
  const [previewSymbol, setPreviewSymbol] = useState('BTC')
  const [previewData, setPreviewData] = useState<any>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [chartTimeframe, setChartTimeframe] = useState('5m') // Independent chart timeframe

  // Save/delete loading states (for dialog buttons)
  const [savingSignal, setSavingSignal] = useState(false)
  const [savingPool, setSavingPool] = useState(false)

  // AI Signal Chat state
  const [aiChatOpen, setAiChatOpen] = useState(false)
  const [accounts, setAccounts] = useState<any[]>([])
  const [accountsLoading, setAccountsLoading] = useState(false)

  // Watchlist symbols for preview and analysis
  const [watchlistSymbols, setWatchlistSymbols] = useState<string[]>([])
  const [analysisSymbol, setAnalysisSymbol] = useState('BTC')

  // Pool preview state
  const [previewPool, setPreviewPool] = useState<SignalPool | null>(null)

  // Market Regime state
  const [regimeLoading, setRegimeLoading] = useState(false)

  // Trigger logs filter & pagination state
  const [logsFilterPool, setLogsFilterPool] = useState<number | null>(null)
  const [logsFilterSymbol, setLogsFilterSymbol] = useState<string>('')
  const [logsTotal, setLogsTotal] = useState(0)
  const [logsOffset, setLogsOffset] = useState(0)
  const [walletRuntime, setWalletRuntime] = useState<WalletTrackingRuntimeStatus | null>(null)
  const [walletRuntimeLoading, setWalletRuntimeLoading] = useState(false)
  const LOGS_PAGE_SIZE = 50
  const sortedSignals = sortByCreatedAtDesc(signals)
  const sortedPools = sortByCreatedAtDesc(pools)

  const loadData = async () => {
    try {
      setLoading(true)
      const data = await fetchSignals()
      setSignals(data.signals)
      setPools(data.pools)
      const logsData = await fetchTriggerLogs({ limit: LOGS_PAGE_SIZE, offset: 0 })
      setLogs(logsData.logs)
      setLogsTotal(logsData.total)
      setLogsOffset(0)
    } catch (err) {
      toast.error('Failed to load signal data')
    } finally {
      setLoading(false)
    }
  }

  // Silent refresh - no loading state, for use after save/delete operations
  const refreshDataSilently = async () => {
    try {
      const data = await fetchSignals()
      setSignals(data.signals)
      setPools(data.pools)
    } catch (err) {
      // Silent fail - data will refresh on next load
    }
  }

  const loadAccounts = async () => {
    try {
      setAccountsLoading(true)
      const res = await fetch('/api/account/list')
      if (res.ok) {
        const data = await res.json()
        // API returns array directly, not {accounts: [...]}
        setAccounts(Array.isArray(data) ? data : data.accounts || [])
      }
    } catch (err) {
      console.error('Failed to load accounts:', err)
    } finally {
      setAccountsLoading(false)
    }
  }

  const loadWalletRuntime = async (silent: boolean = false) => {
    try {
      if (!silent) setWalletRuntimeLoading(true)
      const data = await fetchWalletTrackingStatus()
      setWalletRuntime(data)
    } catch (err) {
      if (!silent) toast.error(t('signals.walletTracking.loadStatusFailed', 'Failed to load wallet tracking status'))
    } finally {
      if (!silent) setWalletRuntimeLoading(false)
    }
  }

  // Silent refresh for logs only (no loading state, always fetches first page)
  const refreshLogsSilently = async (poolId: number | null, symbol: string) => {
    try {
      const logsData = await fetchTriggerLogs({
        poolId: poolId ?? undefined,
        symbol: symbol || undefined,
        limit: LOGS_PAGE_SIZE,
        offset: 0,
      })
      setLogs(logsData.logs)
      setLogsTotal(logsData.total)
      setLogsOffset(0)
    } catch {
      // Silent fail - don't interrupt user
    }
  }

  // Load logs with filters (resets to first page)
  const loadLogsWithFilters = async (poolId?: number | null, symbol?: string) => {
    try {
      const logsData = await fetchTriggerLogs({
        poolId: poolId ?? undefined,
        symbol: symbol || undefined,
        limit: LOGS_PAGE_SIZE,
        offset: 0,
      })
      setLogs(logsData.logs)
      setLogsTotal(logsData.total)
      setLogsOffset(0)
    } catch {
      toast.error('Failed to load logs')
    }
  }

  // Load more logs (pagination)
  const loadMoreLogs = async () => {
    try {
      const newOffset = logsOffset + LOGS_PAGE_SIZE
      const logsData = await fetchTriggerLogs({
        poolId: logsFilterPool ?? undefined,
        symbol: logsFilterSymbol || undefined,
        limit: LOGS_PAGE_SIZE,
        offset: newOffset,
      })
      setLogs(prev => [...prev, ...logsData.logs])
      setLogsOffset(newOffset)
    } catch {
      toast.error('Failed to load more logs')
    }
  }

  // Load watchlist symbols
  const loadWatchlist = async (exchange: string = 'hyperliquid') => {
    try {
      const endpoint = exchange === 'binance'
        ? '/api/binance/symbols/watchlist'
        : '/api/hyperliquid/symbols/watchlist'
      const res = await fetch(endpoint)
      if (res.ok) {
        const data = await res.json()
        const symbols = data.symbols || []
        setWatchlistSymbols(symbols)
        if (symbols.length > 0 && !symbols.includes(analysisSymbol)) {
          setAnalysisSymbol(symbols[0])
        }
      }
    } catch {
      // Silent fail
    }
  }

  // Check Market Regime for all triggers
  const checkMarketRegime = async () => {
    if (!previewData?.triggers?.length || !previewData?.symbol) return
    setRegimeLoading(true)
    try {
      // Get max timeframe from signal/pool config
      const timeframe = previewData.time_window || '5m'
      const timestamps = previewData.triggers.map((t: any) => t.timestamp)
      const regimeMap = await fetchBatchMarketRegime([previewData.symbol], timeframe, timestamps)
      // Update triggers with regime data
      const updatedTriggers = previewData.triggers.map((t: any) => ({
        ...t,
        market_regime: regimeMap.get(t.timestamp) || null,
      }))
      setPreviewData({ ...previewData, triggers: updatedTriggers })
      toast.success(`Checked regime for ${regimeMap.size} trigger points`)
    } catch (e) {
      toast.error('Failed to check market regime')
    } finally {
      setRegimeLoading(false)
    }
  }

  // Initial load
  useEffect(() => {
    loadData()
    loadAccounts()
    loadWatchlist()
    loadWalletRuntime(true)
    fetchFactorLibrary().then(setFactorLibrary)

    /**
     * URL parameter support: #page-name?view=ID
     * When navigating from Hyper AI created entity card, switch to pools tab
     * and highlight/scroll to the specific pool.
     * Note: Parameters are in the hash (after #), not in search (before #).
     */
    const hash = window.location.hash
    const hashParamIndex = hash.indexOf('?')
    if (hashParamIndex !== -1) {
      const hashParams = new URLSearchParams(hash.slice(hashParamIndex))
      const viewId = hashParams.get('view')
      if (viewId) {
        const numId = Number(viewId)
        if (!isNaN(numId)) {
          // Switch to pools tab to show the created pool
          setActiveTab('pools')
          // TODO: Could scroll to and highlight the specific pool
        }
        // Clean up URL after handling (keep hash without params)
        window.history.replaceState({}, '', window.location.pathname + hash.slice(0, hashParamIndex))
      }
    }
  }, [])

  useEffect(() => {
    if (activeTab !== 'wallets') return
    loadWalletRuntime()
    const interval = setInterval(() => {
      loadWalletRuntime(true)
    }, 15000)
    return () => clearInterval(interval)
  }, [activeTab])

  // Auto-refresh logs only when on logs tab (silent, no loading)
  useEffect(() => {
    if (activeTab !== 'logs') return
    const interval = setInterval(() => {
      refreshLogsSilently(logsFilterPool, logsFilterSymbol)
    }, 15000)
    return () => clearInterval(interval)
  }, [activeTab, logsFilterPool, logsFilterSymbol])

  // Fetch metric analysis when dialog opens or metric/period/symbol/exchange changes
  useEffect(() => {
    if (!signalDialogOpen) {
      setMetricAnalysis(null)
      return
    }
    // Clear previous analysis immediately to avoid data mismatch during loading
    setMetricAnalysis(null)
    const loadAnalysis = async () => {
      // Skip analysis for event-based metrics (no threshold suggestions needed)
      if (signalForm.metric === 'macd' || signalForm.metric === 'taker_volume' || signalForm.metric === '_pick_factor') {
        setAnalysisLoading(false)
        return
      }
      setAnalysisLoading(true)
      try {
        // Factor metrics use evaluate API for effectiveness data
        if (signalForm.metric.startsWith('factor:')) {
          const factorName = signalForm.metric.split(':')[1]
          const factor = factorLibrary.find(f => f.name === factorName)
          if (!factor) { setMetricAnalysis(null); return }
          const res = await fetch('/api/factors/evaluate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              expression: factor.expression,
              symbol: analysisSymbol,
              exchange: signalForm.exchange,
              period: signalForm.time_window,
            }),
          })
          if (!res.ok) { setMetricAnalysis(null); return }
          const evalData = await res.json()
          if (evalData.status === 'ok') {
            setMetricAnalysis({
              status: 'ok',
              metric: signalForm.metric,
              sample_count: 300,
              time_range_hours: 300,
              statistics: null as any,
              suggestions: null as any,
              factor_effectiveness: evalData.effectiveness,
              factor_latest_value: evalData.latest_value,
              factor_percentiles: evalData.percentiles,
            } as any)
          } else {
            setMetricAnalysis(null)
          }
        } else {
          const data = await fetchMetricAnalysis(analysisSymbol, signalForm.metric, signalForm.time_window, signalForm.exchange)
          setMetricAnalysis(data)
        }
      } catch {
        setMetricAnalysis(null)
      } finally {
        setAnalysisLoading(false)
      }
    }
    loadAnalysis()
  }, [signalDialogOpen, signalForm.metric, signalForm.time_window, signalForm.exchange, analysisSymbol])

  const openSignalDialog = (signal?: SignalDefinition) => {
    if (signal) {
      setEditingSignal(signal)
      const cond = signal.trigger_condition
      // Map old metric names to new names (backward compatibility)
      const metricNameMap: Record<string, string> = {
        'oi_delta_percent': 'oi_delta',
        'funding_rate': 'funding',
        'taker_buy_ratio': 'taker_ratio',
      }
      const normalizedMetric = metricNameMap[cond.metric] || cond.metric || 'oi_delta'
      setSignalForm({
        signal_name: signal.signal_name,
        description: signal.description || '',
        metric: normalizedMetric,
        operator: cond.operator || 'abs_greater_than',
        threshold: cond.threshold ?? 5,
        time_window: cond.time_window || '5m',
        enabled: signal.enabled,
        exchange: signal.exchange || 'hyperliquid',
        // taker_volume composite fields
        direction: (cond as any).direction || 'any',
        ratio_threshold: (cond as any).ratio_threshold ?? 1.5,
        volume_threshold: (cond as any).volume_threshold ?? 50000,
        // MACD event fields
        event_types: (cond as any).event_types || ['golden_cross', 'death_cross'],
      })
    } else {
      setEditingSignal(null)
      setSignalForm({
        signal_name: '',
        description: '',
        metric: 'oi_delta',
        operator: 'abs_greater_than',
        threshold: 5,
        time_window: '5m',
        enabled: true,
        exchange: 'hyperliquid',
        direction: 'any',
        ratio_threshold: 1.5,
        volume_threshold: 50000,
        event_types: ['golden_cross', 'death_cross'],
      })
    }
    setSignalDialogOpen(true)
  }

  const handleSaveSignal = async () => {
    setSavingSignal(true)
    try {
      // Build trigger_condition based on metric type
      let trigger_condition: Record<string, unknown>
      if (signalForm.metric === 'taker_volume') {
        // Composite signal: direction + ratio + volume
        trigger_condition = {
          metric: signalForm.metric,
          direction: signalForm.direction,
          ratio_threshold: signalForm.ratio_threshold,
          volume_threshold: signalForm.volume_threshold,
          time_window: signalForm.time_window,
        }
      } else if (signalForm.metric === 'macd') {
        // MACD event-based signal
        if (signalForm.event_types.length === 0) {
          toast.error('Please select at least one MACD event type')
          setSavingSignal(false)
          return
        }
        trigger_condition = {
          metric: signalForm.metric,
          event_types: signalForm.event_types,
          time_window: signalForm.time_window,
        }
      } else {
        // Standard signal: operator + threshold
        trigger_condition = {
          metric: signalForm.metric,
          operator: signalForm.operator,
          threshold: signalForm.threshold,
          time_window: signalForm.time_window,
        }
      }
      const data = {
        signal_name: signalForm.signal_name,
        description: signalForm.description,
        trigger_condition,
        enabled: signalForm.enabled,
        exchange: signalForm.exchange,
      }
      if (editingSignal) {
        await updateSignal(editingSignal.id, data)
        toast.success('Signal updated')
      } else {
        await createSignal(data)
        toast.success('Signal created')
      }
      setSignalDialogOpen(false)
      refreshDataSilently()
    } catch (err) {
      toast.error('Failed to save signal')
    } finally {
      setSavingSignal(false)
    }
  }

  const handleDeleteSignal = async (id: number) => {
    if (!confirm('Delete this signal?')) return
    try {
      const data = await deleteSignal(id)
      if (data.deleted) {
        toast.success('Signal deleted')
        refreshDataSilently()
      } else if (data.dependencies) {
        const msg = formatDeps(data.dependencies as string[], t)
        toast.error(`${t('common.cannotDelete')}: ${msg}`, { duration: 5000 })
      } else {
        toast.error((data.error as string) || 'Failed to delete signal')
      }
    } catch (err) {
      toast.error('Failed to delete signal')
    }
  }

  const openPreviewDialog = async (signal: SignalDefinition, symbol: string = 'BTC') => {
    // Get time_window from signal's trigger condition and set as default chart timeframe
    const signalTimeWindow = signal.trigger_condition?.time_window || '5m'
    const signalExchange = signal.exchange || 'hyperliquid'
    setChartTimeframe(signalTimeWindow)
    setPreviewSignal(signal)
    setPreviewPool(null)
    setPreviewSymbol(symbol)
    setPreviewDialogOpen(true)
    setPreviewLoading(true)
    setPreviewData(null)

    // Load watchlist for the signal's exchange
    loadWatchlist(signalExchange)

    try {
      // Step 1: Fetch K-lines from market API (ensures fresh data)
      // Use 500 klines to match the K-line page and provide more historical context
      // Include MACD indicator for chart display
      const klineRes = await fetch(
        `/api/market/kline-with-indicators/${symbol}?market=${signalExchange}&period=${signalTimeWindow}&count=500&indicators=MACD`
      )
      if (!klineRes.ok) throw new Error('Failed to fetch K-line data')
      const klineData = await klineRes.json()

      if (!klineData.klines || klineData.klines.length === 0) {
        throw new Error('No K-line data available')
      }

      // Get time range from K-lines (timestamps are in seconds from market API)
      const klines = klineData.klines
      const klineMinTs = Math.min(...klines.map((k: any) => k.timestamp)) * 1000
      const klineMaxTs = Math.max(...klines.map((k: any) => k.timestamp)) * 1000

      // Step 2: Fetch triggers from backtest API with time range
      const triggerRes = await fetch(
        `/api/signals/backtest/${signal.id}?symbol=${symbol}&kline_min_ts=${klineMinTs}&kline_max_ts=${klineMaxTs}`
      )
      if (!triggerRes.ok) throw new Error('Failed to fetch trigger data')
      const triggerData = await triggerRes.json()

      // Combine data for preview chart
      // Convert K-line timestamps to milliseconds for consistency
      const formattedKlines = klines.map((k: any) => ({
        timestamp: k.timestamp * 1000,
        open: k.open,
        high: k.high,
        low: k.low,
        close: k.close,
      }))

      setPreviewData({
        ...triggerData,
        klines: formattedKlines,
        kline_count: formattedKlines.length,
        macd: klineData.indicators?.MACD,
      })
    } catch (err) {
      toast.error('Failed to load preview data')
    } finally {
      setPreviewLoading(false)
    }
  }

  const openPoolPreviewDialog = async (pool: SignalPool, symbol: string = 'BTC') => {
    // Use first signal's time_window or default to 5m, set as default chart timeframe
    const firstSignalId = pool.signal_ids[0]
    const firstSignal = signals.find(s => s.id === firstSignalId)
    const poolTimeWindow = firstSignal?.trigger_condition?.time_window || '5m'
    const poolExchange = pool.exchange || 'hyperliquid'
    setChartTimeframe(poolTimeWindow)
    setPreviewPool(pool)
    setPreviewSignal(null)
    setPreviewSymbol(symbol)
    setPreviewDialogOpen(true)
    setPreviewLoading(true)
    setPreviewData(null)

    // Load watchlist for the pool's exchange
    loadWatchlist(poolExchange)

    try {
      // Step 1: Fetch K-lines with MACD indicator
      const klineRes = await fetch(
        `/api/market/kline-with-indicators/${symbol}?market=${poolExchange}&period=${poolTimeWindow}&count=500&indicators=MACD`
      )
      if (!klineRes.ok) throw new Error('Failed to fetch K-line data')
      const klineData = await klineRes.json()

      if (!klineData.klines || klineData.klines.length === 0) {
        throw new Error('No K-line data available')
      }

      const klines = klineData.klines
      const klineMinTs = Math.min(...klines.map((k: any) => k.timestamp)) * 1000
      const klineMaxTs = Math.max(...klines.map((k: any) => k.timestamp)) * 1000

      // Step 2: Fetch pool backtest
      const triggerRes = await fetch(
        `/api/signals/pool-backtest/${pool.id}?symbol=${symbol}&kline_min_ts=${klineMinTs}&kline_max_ts=${klineMaxTs}`
      )
      if (!triggerRes.ok) throw new Error('Failed to fetch pool backtest')
      const triggerData = await triggerRes.json()

      const formattedKlines = klines.map((k: any) => ({
        timestamp: k.timestamp * 1000,
        open: k.open,
        high: k.high,
        low: k.low,
        close: k.close,
      }))

      setPreviewData({
        ...triggerData,
        klines: formattedKlines,
        kline_count: formattedKlines.length,
        isPoolPreview: true,
        macd: klineData.indicators?.MACD,
      })
    } catch (err) {
      toast.error('Failed to load pool preview data')
    } finally {
      setPreviewLoading(false)
    }
  }

  // AI Signal handlers - returns true on success for UI feedback
  const handleAiCreateSignal = async (config: any): Promise<boolean> => {
    try {
      const signalData = {
        signal_name: config.name,
        description: config.description || '',
        trigger_condition: config.trigger_condition,
        enabled: true,
        exchange: config.exchange || 'hyperliquid',
      }
      await createSignal(signalData)
      toast.success(`Signal "${config.name}" created`)
      // Silent refresh - don't close dialog, user may want to create more signals
      const data = await fetchSignals()
      setSignals(data.signals)
      setPools(data.pools)
      return true
    } catch (err) {
      toast.error('Failed to create signal')
      return false
    }
  }

  // AI Signal Pool handler - creates pool from AI-generated config
  const handleAiCreatePool = async (config: any): Promise<boolean> => {
    try {
      const poolConfig = {
        name: config.name,
        symbol: config.symbol,
        description: config.description || '',
        logic: config.logic || 'AND',
        signals: config.signals || [],
        exchange: config.exchange || 'hyperliquid',
      }
      const result = await createPoolFromConfig(poolConfig)
      toast.success(`Signal Pool "${config.name}" created with ${result.signals.length} signals`)
      // Refresh signals and pools
      const data = await fetchSignals()
      setSignals(data.signals)
      setPools(data.pools)
      return true
    } catch (err: any) {
      toast.error(err.message || 'Failed to create signal pool')
      return false
    }
  }

  const handleAiPreviewSignal = async (config: any) => {
    // Create a temporary signal object for preview
    const tempSignal: SignalDefinition = {
      id: 0,
      signal_name: config.name,
      description: config.description || '',
      trigger_condition: config.trigger_condition,
      enabled: true,
      exchange: config.exchange || 'hyperliquid',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }

    const symbol = config.symbol || 'BTC'
    const tempTimeWindow = config.trigger_condition?.time_window || '5m'
    const tempExchange = config.exchange || 'hyperliquid'
    setChartTimeframe(tempTimeWindow)
    setPreviewSignal(tempSignal)
    setPreviewSymbol(symbol)
    setPreviewDialogOpen(true)
    setPreviewLoading(true)
    setPreviewData(null)

    try {
      // Fetch K-lines with MACD indicator
      const klineRes = await fetch(
        `/api/market/kline-with-indicators/${symbol}?market=${tempExchange}&period=${tempTimeWindow}&count=500&indicators=MACD`
      )
      if (!klineRes.ok) throw new Error('Failed to fetch K-line data')
      const klineData = await klineRes.json()

      if (!klineData.klines || klineData.klines.length === 0) {
        throw new Error('No K-line data available')
      }

      const klines = klineData.klines
      const klineMinTs = Math.min(...klines.map((k: any) => k.timestamp)) * 1000
      const klineMaxTs = Math.max(...klines.map((k: any) => k.timestamp)) * 1000

      // Use temp backtest API for preview
      const triggerRes = await fetch('/api/signals/backtest-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          triggerCondition: config.trigger_condition,
          klineMinTs,
          klineMaxTs,
          exchange: tempExchange,
        }),
      })
      if (!triggerRes.ok) throw new Error('Failed to fetch trigger data')
      const triggerData = await triggerRes.json()

      const formattedKlines = klines.map((k: any) => ({
        timestamp: k.timestamp * 1000,
        open: k.open,
        high: k.high,
        low: k.low,
        close: k.close,
      }))

      setPreviewData({
        ...triggerData,
        klines: formattedKlines,
        kline_count: formattedKlines.length,
        macd: klineData.indicators?.MACD,
      })
    } catch (err) {
      toast.error('Failed to load preview data')
    } finally {
      setPreviewLoading(false)
    }
  }

  // Refresh preview with new chart timeframe (keeps same signal/pool, just changes K-line period)
  const refreshPreviewWithTimeframe = async (newTimeframe: string) => {
    setChartTimeframe(newTimeframe)
    setPreviewLoading(true)

    // Get exchange from current preview context
    const previewExchange = previewPool?.exchange || previewSignal?.exchange || 'hyperliquid'

    try {
      // Fetch K-lines with new timeframe and MACD indicator
      const klineRes = await fetch(
        `/api/market/kline-with-indicators/${previewSymbol}?market=${previewExchange}&period=${newTimeframe}&count=500&indicators=MACD`
      )
      if (!klineRes.ok) throw new Error('Failed to fetch K-line data')
      const klineData = await klineRes.json()

      if (!klineData.klines || klineData.klines.length === 0) {
        throw new Error('No K-line data available')
      }

      const klines = klineData.klines
      const klineMinTs = Math.min(...klines.map((k: any) => k.timestamp)) * 1000
      const klineMaxTs = Math.max(...klines.map((k: any) => k.timestamp)) * 1000

      // Fetch triggers based on whether it's a pool or signal preview
      let triggerData
      if (previewPool) {
        const triggerRes = await fetch(
          `/api/signals/pool-backtest/${previewPool.id}?symbol=${previewSymbol}&kline_min_ts=${klineMinTs}&kline_max_ts=${klineMaxTs}`
        )
        if (!triggerRes.ok) throw new Error('Failed to fetch pool backtest')
        triggerData = await triggerRes.json()
      } else if (previewSignal) {
        if (previewSignal.id === 0) {
          // Temp signal (AI preview)
          const triggerRes = await fetch('/api/signals/backtest-preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              symbol: previewSymbol,
              triggerCondition: previewSignal.trigger_condition,
              klineMinTs,
              klineMaxTs,
              exchange: previewExchange,
            }),
          })
          if (!triggerRes.ok) throw new Error('Failed to fetch trigger data')
          triggerData = await triggerRes.json()
        } else {
          // Saved signal (backtest_signal gets exchange from DB)
          const triggerRes = await fetch(
            `/api/signals/backtest/${previewSignal.id}?symbol=${previewSymbol}&kline_min_ts=${klineMinTs}&kline_max_ts=${klineMaxTs}`
          )
          if (!triggerRes.ok) throw new Error('Failed to fetch trigger data')
          triggerData = await triggerRes.json()
        }
      }

      const formattedKlines = klines.map((k: any) => ({
        timestamp: k.timestamp * 1000,
        open: k.open,
        high: k.high,
        low: k.low,
        close: k.close,
      }))

      setPreviewData({
        ...triggerData,
        klines: formattedKlines,
        kline_count: formattedKlines.length,
        isPoolPreview: !!previewPool,
        chart_timeframe: newTimeframe,
        macd: klineData.indicators?.MACD,
      })
    } catch (err) {
      toast.error('Failed to refresh preview')
    } finally {
      setPreviewLoading(false)
    }
  }

  const openPoolDialog = (pool?: SignalPool, initialSourceType: PoolSourceType = 'market_signals') => {
    if (pool) {
      setEditingPool(pool)
      setPoolForm({
        pool_name: pool.pool_name,
        signal_ids: pool.signal_ids,
        symbols: pool.symbols,
        enabled: pool.enabled,
        logic: pool.logic || 'OR',
        exchange: pool.exchange || 'hyperliquid',
        source_type: pool.source_type || 'market_signals',
        source_config: {
          addresses: pool.source_config?.addresses || [],
          event_types: pool.source_config?.event_types || ['position_change', 'fill', 'liquidation'],
          sync_mode: pool.source_config?.sync_mode || 'ws_only',
        },
      })
    } else {
      setEditingPool(null)
      setPoolForm({
        pool_name: '',
        signal_ids: [],
        symbols: [],
        enabled: true,
        logic: 'OR',
        exchange: 'hyperliquid',
        source_type: initialSourceType,
        source_config: {
          addresses: [],
          event_types: ['position_change', 'fill', 'liquidation'],
          sync_mode: 'ws_only',
        },
      })
    }
    setPoolDialogOpen(true)
  }

  const handleSavePool = async () => {
    setSavingPool(true)
    try {
      if (poolForm.source_type === 'wallet_tracking') {
        if (!(poolForm.source_config.addresses || []).length) {
          toast.error(t('signals.walletTracking.addressRequired', 'Select at least one synced wallet'))
          return
        }
        if (!(poolForm.source_config.event_types || []).length) {
          toast.error(t('signals.walletTracking.eventTypeRequired', 'Select at least one wallet event type'))
          return
        }
      }
      const data = {
        pool_name: poolForm.pool_name,
        signal_ids: poolForm.signal_ids,
        symbols: poolForm.symbols,
        enabled: poolForm.enabled,
        logic: poolForm.logic,
        exchange: poolForm.exchange,
        source_type: poolForm.source_type,
        source_config: poolForm.source_config,
      }
      if (editingPool) {
        await updatePool(editingPool.id, data)
        toast.success('Pool updated')
      } else {
        await createPool(data)
        toast.success('Pool created')
      }
      setPoolDialogOpen(false)
      refreshDataSilently()
    } catch (err) {
      toast.error('Failed to save pool')
    } finally {
      setSavingPool(false)
    }
  }

  const handleDeletePool = async (id: number) => {
    if (!confirm('Delete this pool?')) return
    try {
      const data = await deletePool(id)
      if (data.deleted) {
        toast.success('Pool deleted')
        refreshDataSilently()
      } else if (data.dependencies) {
        const msg = formatDeps(data.dependencies as string[], t)
        toast.error(`${t('common.cannotDelete')}: ${msg}`, { duration: 5000 })
      } else {
        toast.error((data.error as string) || 'Failed to delete pool')
      }
    } catch (err) {
      toast.error('Failed to delete pool')
    }
  }

  const toggleSymbol = (symbol: string) => {
    setPoolForm(prev => ({
      ...prev,
      symbols: prev.symbols.includes(symbol)
        ? prev.symbols.filter(s => s !== symbol)
        : [...prev.symbols, symbol]
    }))
  }

  const toggleSignalInPool = (signalId: number) => {
    setPoolForm(prev => ({
      ...prev,
      signal_ids: prev.signal_ids.includes(signalId)
        ? prev.signal_ids.filter(id => id !== signalId)
        : [...prev.signal_ids, signalId]
    }))
  }

  const toggleWalletEventType = (eventType: string) => {
    setPoolForm(prev => {
      const current = prev.source_config.event_types || []
      const nextEventTypes = current.includes(eventType)
        ? current.filter(item => item !== eventType)
        : [...current, eventType]
      return {
        ...prev,
        source_config: {
          ...prev.source_config,
          event_types: nextEventTypes,
        },
      }
    })
  }

  const toggleWalletAddressInPool = (address: string) => {
    setPoolForm(prev => {
      const current = prev.source_config.addresses || []
      const nextAddresses = current.includes(address)
        ? current.filter(item => item !== address)
        : [...current, address]
      return {
        ...prev,
        source_config: {
          ...prev.source_config,
          addresses: nextAddresses,
        },
      }
    })
  }

  const handleEnableWalletTracking = async () => {
    try {
      setWalletRuntimeLoading(true)
      const accessToken = Cookies.get('arena_token')
      const data = await updateWalletTrackingRuntime({
        enabled: true,
        access_token: accessToken,
      })
      setWalletRuntime(data)
      toast.success(t('signals.walletTracking.enabledSuccess', 'Wallet tracking integration enabled'))
    } catch (err) {
      toast.error(t('signals.walletTracking.enableFailed', 'Failed to enable wallet tracking integration'))
    } finally {
      setWalletRuntimeLoading(false)
    }
  }

  const handleDisableWalletTracking = async () => {
    try {
      setWalletRuntimeLoading(true)
      const data = await updateWalletTrackingRuntime({ enabled: false })
      setWalletRuntime(data)
      toast.success(t('signals.walletTracking.disabledSuccess', 'Wallet tracking integration disabled'))
    } catch (err) {
      toast.error(t('signals.walletTracking.disableFailed', 'Failed to disable wallet tracking integration'))
    } finally {
      setWalletRuntimeLoading(false)
    }
  }

  const formatCondition = (cond: TriggerCondition) => {
    const metric = cond.metric?.startsWith('factor:')
      ? `⚗ ${cond.metric.split(':')[1]}`
      : METRICS.find(m => m.value === cond.metric)?.label || cond.metric
    // Handle taker_volume composite signal
    if (cond.metric === 'taker_volume') {
      const dir = (cond as any).direction || 'any'
      const ratio = (cond as any).ratio_threshold || 1.5
      const vol = ((cond as any).volume_threshold || 0).toLocaleString()
      return `${metric} | ${dir.toUpperCase()} ≥${ratio} Vol≥$${vol} (${cond.time_window})`
    }
    // Handle MACD event-based signal
    if (cond.metric === 'macd') {
      const events = (cond as any).event_types || []
      const eventLabels = events.map((e: string) => {
        const found = MACD_EVENT_TYPES.find(m => m.value === e)
        return found ? found.label : e
      }).join(', ')
      return `${metric} | ${eventLabels || 'No events'} (${cond.time_window})`
    }
    const op = OPERATORS.find(o => o.value === cond.operator)?.label || cond.operator
    return `${metric} ${op} ${cond.threshold} (${cond.time_window})`
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64">{t('signals.loading', 'Loading...')}</div>
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 p-4 space-y-4">
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <div className="flex items-center justify-between gap-4 mb-4">
          <TabsList className="justify-start">
            <TabsTrigger value="signals" className="min-w-[100px]">{t('signals.tabs.signals', 'Signals')}</TabsTrigger>
            <TabsTrigger value="pools" className="min-w-[120px]">{t('signals.tabs.pools', 'Signal Pools')}</TabsTrigger>
            <TabsTrigger value="wallets" className="min-w-[140px]">{t('signals.tabs.walletTracking', 'Wallet Tracking')}</TabsTrigger>
            <TabsTrigger value="logs" className="min-w-[120px]">{t('signals.tabs.logs', 'Trigger Logs')}</TabsTrigger>
            <TabsTrigger value="regime" className="min-w-[130px]">{t('signals.tabs.regime', 'Market Regime')}</TabsTrigger>
          </TabsList>
          <div className="text-xs">
            <p className="text-amber-600 font-medium flex items-center gap-1">
              <span>⚠️</span>
              <span>{t('signals.mainnetWarning', 'Signal system analyzes Mainnet data only (testnet data unreliable)')}</span>
            </p>
            {collectionDays !== null && collectionDays > 0 && (
              <p className="text-muted-foreground mt-0.5">
                {t('signals.collectionDaysHint', 'Signal backtest relies on market flow data, collected for {{days}} days', { days: collectionDays })}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button onClick={() => openSignalDialog()} size="sm">
              <Plus className="w-4 h-4 mr-2" />{t('signals.newSignal', 'New Signal')}
            </Button>
            <Button onClick={() => openPoolDialog()} size="sm">
              <Plus className="w-4 h-4 mr-2" />{t('signals.newPool', 'New Pool')}
            </Button>
            <Button
              onClick={() => setAiChatOpen(true)}
              size="sm"
              className="bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white border-0 shadow-lg hover:shadow-xl transition-all"
            >
              <Sparkles className="w-4 h-4 mr-2" />{t('signals.aiSetSignal', 'AI Set Signal')}
            </Button>
          </div>
        </div>

        <TabsContent value="signals" className="space-y-4 flex-1 min-h-0 overflow-y-auto">
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {sortedSignals.map(signal => (
              <Card key={signal.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-lg">{signal.signal_name}</CardTitle>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" onClick={() => openSignalDialog(signal)}>
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleDeleteSignal(signal.id)}>
                        <Trash2 className="w-4 h-4 text-destructive" />
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-muted-foreground mb-2">{signal.description}</p>
                  <p className="text-sm font-mono bg-muted p-2 rounded">
                    {formatCondition(signal.trigger_condition)}
                  </p>
                  <div className="flex items-center justify-between mt-2">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${signal.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
                      <span className="text-xs">{signal.enabled ? t('signals.enabled', 'Enabled') : t('signals.disabled', 'Disabled')}</span>
                      <ExchangeBadge exchange={signal.exchange || 'hyperliquid'} size="xs" />
                    </div>
                    <Button variant="outline" size="sm" onClick={() => openPreviewDialog(signal)}>
                      <Eye className="w-4 h-4 mr-1" />{t('signals.backtest', 'Backtest')}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="pools" className="space-y-4 flex-1 min-h-0 overflow-y-auto">
          <div className="grid gap-4 md:grid-cols-2">
            {sortedPools.map(pool => (
              <Card key={pool.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-lg">{pool.pool_name}</CardTitle>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" onClick={() => openPoolDialog(pool)}>
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleDeletePool(pool.id)}>
                        <Trash2 className="w-4 h-4 text-destructive" />
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs px-2 py-0.5 rounded bg-muted text-muted-foreground">
                        {pool.source_type === 'wallet_tracking'
                          ? t('signals.walletTracking.sourceTypeLabel', 'Wallet Tracking')
                          : t('signals.dialog.marketSignalsType', 'Market Signals')}
                      </span>
                    </div>
                    {pool.source_type === 'wallet_tracking' ? (
                      <>
                        <div>
                          <span className="text-sm font-medium">{t('signals.walletTracking.addresses', 'Tracked Wallets')}: </span>
                          <span className="text-sm">
                            {pool.source_config?.addresses?.join(', ') || t('signals.walletTracking.noneSelected', 'None selected')}
                          </span>
                        </div>
                        <div>
                          <span className="text-sm font-medium">{t('signals.walletTracking.eventTypes', 'Event Types')}: </span>
                          <span className="text-sm">
                            {(pool.source_config?.event_types || []).length
                              ? (pool.source_config?.event_types || []).map(eventType => formatWalletEventType(t, eventType)).join(', ')
                              : t('signals.walletTracking.noneSelected', 'None selected')}
                          </span>
                        </div>
                      </>
                    ) : (
                      <>
                        <div>
                          <span className="text-sm font-medium">{t('signals.symbols', 'Symbols')}: </span>
                          <span className="text-sm">{pool.symbols.join(', ') || 'None'}</span>
                        </div>
                        <div>
                          <span className="text-sm font-medium">{t('signals.tabs.signals', 'Signals')}: </span>
                          <span className="text-sm">
                            {pool.signal_ids.map(id => signals.find(s => s.id === id)?.signal_name).filter(Boolean).join(', ') || 'None'}
                          </span>
                        </div>
                      </>
                    )}
                    {pool.source_type !== 'wallet_tracking' && (
                      <div>
                        <span className="text-sm font-medium">{t('signals.logic', 'Logic')}: </span>
                        <span className={`text-sm px-2 py-0.5 rounded ${pool.logic === 'AND' ? 'bg-blue-500/20 text-blue-400' : 'bg-green-500/20 text-green-400'}`}>
                          {pool.logic || 'OR'}
                        </span>
                        <span className="text-xs text-muted-foreground ml-2">
                          {pool.logic === 'AND' ? `(${t('signals.allSignalsTrigger', 'All signals must trigger')})` : `(${t('signals.anySignalTriggers', 'Any signal triggers')})`}
                        </span>
                      </div>
                    )}
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${pool.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
                        <span className="text-xs">{pool.enabled ? t('signals.enabled', 'Enabled') : t('signals.disabled', 'Disabled')}</span>
                        <ExchangeBadge exchange={pool.exchange || 'hyperliquid'} size="xs" />
                      </div>
                      {pool.source_type === 'wallet_tracking' ? (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  disabled
                                >
                                  <Eye className="w-4 h-4 mr-1" />
                                  {t('signals.backtest', 'Backtest')}
                                  <CircleHelp className="w-3.5 h-3.5 ml-1 opacity-70" />
                                </Button>
                              </span>
                            </TooltipTrigger>
                            <TooltipContent side="top" className="max-w-[260px] p-3">
                              <p className="text-xs">{t('signals.walletTracking.backtestHint', 'Wallet signals come from real-time external events and are not available for historical replay backtesting in the current version.')}</p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      ) : (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openPoolPreviewDialog(pool, watchlistSymbols[0] || 'BTC')}
                          disabled={pool.signal_ids.length === 0}
                        >
                          <Eye className="w-4 h-4 mr-1" />
                          {t('signals.backtest', 'Backtest')}
                        </Button>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="wallets" className="space-y-4 flex-1 min-h-0 overflow-y-auto">
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>{t('signals.walletTracking.title', 'Wallet Tracking')}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 lg:grid-cols-[0.95fr_1.05fr]">
                  <div className="rounded-lg border bg-muted/30 p-4">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <div className="text-sm font-medium">{t('signals.walletTracking.connectionStatus', 'Connection Status')}</div>
                        <div className="text-xs text-muted-foreground">{t('signals.walletTracking.connectionHint', 'Enable Hyper Insight sync here. HAA will keep synced wallets available for pool selection and runtime matching.')}</div>
                      </div>
                      <span className={`text-xs px-2 py-1 rounded inline-flex items-center gap-1 ${
                        walletRuntime?.status === 'connected'
                          ? 'bg-emerald-500/10 text-emerald-600'
                          : walletRuntime?.enabled
                            ? 'bg-amber-500/10 text-amber-600'
                            : 'bg-muted text-muted-foreground'
                      }`}>
                        {walletRuntime?.status === 'connected'
                          ? <Wifi className="w-3 h-3" />
                          : walletRuntime?.enabled
                            ? <RefreshCw className="w-3 h-3 animate-spin" />
                            : <WifiOff className="w-3 h-3" />}
                        {walletRuntime?.status === 'connected'
                          ? ((walletRuntime?.synced_addresses?.length || 0) > 0
                            ? t('signals.walletTracking.connected', 'Connected')
                            : t('signals.walletTracking.connectedNoWallets', 'Connected · No tracked wallets'))
                          : walletRuntime?.status === 'waiting_for_token'
                            ? t('signals.walletTracking.waitingForToken', 'Waiting for token')
                            : walletRuntime?.enabled
                                ? t('signals.walletTracking.connecting', 'Connecting')
                                : t('signals.walletTracking.notConnected', 'Not Connected')}
                      </span>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-muted-foreground">
                      <div>{t('signals.walletTracking.tier', 'Tier')}: <span className="text-foreground">{formatWalletTier(t, walletRuntime?.tier)}</span></div>
                      <div>{t('signals.walletTracking.syncedWalletCount', 'Synced wallets')}: <span className="text-foreground">{walletRuntime?.synced_addresses?.length || 0}</span></div>
                      <div>{t('signals.walletTracking.lastEventAt', 'Last event')}: <span className="text-foreground">{formatWalletRuntimeTime(walletRuntime?.last_event_at)}</span></div>
                    </div>
                    {walletRuntime?.last_error && (
                      <div className="mt-3 text-xs text-red-500">
                        {t('signals.walletTracking.lastError', 'Last error')}: {walletRuntime.last_error}
                      </div>
                    )}
                  </div>

                  <div className="rounded-lg border p-4 space-y-3">
                    <div className="text-sm font-medium">{t('signals.walletTracking.syncedWallets', 'Synced Wallets')}</div>
                    {walletRuntimeLoading ? (
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <RefreshCw className="w-4 h-4 animate-spin" />
                        {t('signals.walletTracking.loading', 'Loading...')}
                      </div>
                    ) : walletRuntime?.synced_addresses?.length ? (
                      <div className="flex flex-wrap gap-2">
                        {walletRuntime.synced_addresses.map(address => (
                          <span key={address} className="rounded-md border px-2 py-1 text-xs">
                            {address}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        {t('signals.walletTracking.noSyncedWallets', 'No synced wallets yet. Track wallets on Hyper Insight first.')}
                      </p>
                    )}
                  </div>
                </div>

                <div className="flex gap-2">
                  <Button asChild variant="outline" size="sm">
                    <a href="https://hyper.akooi.com/" target="_blank" rel="noopener noreferrer">
                      {t('signals.walletTracking.manageOnInsight', 'Manage on Hyper Insight')}
                    </a>
                  </Button>
                  {walletRuntime?.enabled ? (
                    <Button onClick={handleDisableWalletTracking} size="sm" variant="outline" disabled={walletRuntimeLoading}>
                      {t('signals.walletTracking.disable', 'Disable Sync')}
                    </Button>
                  ) : (
                    <Button onClick={handleEnableWalletTracking} size="sm" disabled={walletRuntimeLoading}>
                      {t('signals.walletTracking.enable', 'Enable Sync')}
                    </Button>
                  )}
                  <Button onClick={() => openPoolDialog(undefined, 'wallet_tracking')} size="sm">
                    <Plus className="w-4 h-4 mr-2" />
                    {t('signals.walletTracking.createWalletPool', 'Create Wallet Pool')}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {t('signals.walletTracking.inlineHint', 'Connect here first. Once tracked wallets appear, choose which ones should enter HAA signal pools.')}
                </p>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="logs" className="flex-1">
          <Card className="h-full flex flex-col">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2">
                <Activity className="w-5 h-5" />{t('signals.triggerHistory', 'Trigger History')}
              </CardTitle>
              {/* Filter controls */}
              <div className="flex items-center gap-3 mt-2">
                <Select
                  value={logsFilterPool === null ? 'all' : String(logsFilterPool)}
                  onValueChange={(v) => {
                    const poolId = v === 'all' ? null : Number(v)
                    setLogsFilterPool(poolId)
                    loadLogsWithFilters(poolId, logsFilterSymbol)
                  }}
                >
                  <SelectTrigger className="w-[180px] h-8">
                    <SelectValue placeholder={t('signals.allPools', 'All Pools')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">{t('signals.allPools', 'All Pools')}</SelectItem>
                    {pools.map(p => (
                      <SelectItem key={p.id} value={String(p.id)}>{p.pool_name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={logsFilterSymbol || 'all'}
                  onValueChange={(v) => {
                    const symbol = v === 'all' ? '' : v
                    setLogsFilterSymbol(symbol)
                    loadLogsWithFilters(logsFilterPool, symbol)
                  }}
                >
                  <SelectTrigger className="w-[120px] h-8">
                    <SelectValue placeholder={t('signals.allSymbols', 'All Symbols')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">{t('signals.allSymbols', 'All Symbols')}</SelectItem>
                    {watchlistSymbols.map(s => (
                      <SelectItem key={s} value={s}>{s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className="text-xs text-muted-foreground ml-auto">
                  {t('signals.logsCount', '{{count}} logs', { count: logsTotal })}
                </span>
              </div>
            </CardHeader>
            <CardContent className="flex-1 overflow-hidden pt-2">
              {logs.length === 0 ? (
                <p className="text-muted-foreground text-center py-8">{t('signals.noTriggers', 'No triggers recorded yet')}</p>
              ) : (
                <ScrollArea className="h-[calc(100vh-280px)]">
                  <div className="space-y-2">
                    {logs.map(log => {
                      const triggerData = log.trigger_value as Record<string, unknown> | null
                      const timestamp = log.triggered_at.endsWith('Z') ? log.triggered_at : log.triggered_at + 'Z'
                      const isWalletTrigger = Boolean(
                        log.pool_id &&
                        triggerData &&
                        triggerData.source_type === 'wallet_tracking'
                      )
                      const isPoolTrigger = log.pool_id && triggerData && 'logic' in triggerData
                      const pool = log.pool_id ? pools.find(p => p.id === log.pool_id) : null
                      const signal = log.signal_id ? signals.find(s => s.id === log.signal_id) : null
                      const poolName = log.pool_id ? pool?.pool_name : null
                      const signalName = signal?.signal_name
                      const logExchange = pool?.exchange || signal?.exchange || 'hyperliquid'

                      const formatTriggerDetails = () => {
                        if (!triggerData) return null
                        if (triggerData.source_type === 'wallet_tracking') {
                          const eventType = typeof triggerData.event_type === 'string'
                            ? formatWalletEventType(t, triggerData.event_type)
                            : t('signals.walletTracking.sourceTypeLabel', 'Wallet Tracking')
                          const address = typeof triggerData.address === 'string' ? triggerData.address : null
                          const summary = typeof triggerData.summary === 'string' ? triggerData.summary : null
                          const detail = (typeof triggerData.detail === 'object' && triggerData.detail && !Array.isArray(triggerData.detail))
                            ? triggerData.detail as Record<string, unknown>
                            : null
                          const action = typeof detail?.action === 'string'
                            ? formatWalletActionLabel(t, detail.action)
                            : null
                          const direction = typeof detail?.direction === 'string'
                            ? formatWalletDirectionLabel(t, detail.direction)
                            : null
                          const notionalValue = formatWalletMetricValue(detail?.notional_value)
                          const entryPrice = formatWalletMetricValue(detail?.entry_price, 4)
                          const leverage = formatWalletMetricValue(detail?.leverage)
                          const unrealizedPnl = formatWalletMetricValue(detail?.unrealized_pnl)
                          const liquidationPrice = formatWalletMetricValue(detail?.liquidation_price, 4)
                          const closedPnl = formatWalletMetricValue(detail?.closed_pnl)
                          const averagePrice = formatWalletMetricValue(detail?.average_price, 4)
                          return (
                            <div className="space-y-1">
                              <div className="flex items-center gap-2">
                                <span className="px-1.5 py-0.5 rounded text-xs bg-purple-500/20 text-purple-400">
                                  {eventType}
                                </span>
                                {address && <span>{formatShortAddress(address)}</span>}
                              </div>
                              {summary && <div>{summary}</div>}
                              {(action || direction || notionalValue || entryPrice || leverage || unrealizedPnl || liquidationPrice || closedPnl || averagePrice) && (
                                <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                                  {action && (
                                    <span>{t('signals.walletTracking.logAction', 'Action')}: <span className="text-foreground">{action}</span></span>
                                  )}
                                  {direction && (
                                    <span>{t('signals.walletTracking.logDirection', 'Direction')}: <span className="text-foreground">{direction}</span></span>
                                  )}
                                  {notionalValue && (
                                    <span>{t('signals.walletTracking.logNotional', 'Notional')}: <span className="text-foreground">${notionalValue}</span></span>
                                  )}
                                  {entryPrice && (
                                    <span>{t('signals.walletTracking.logEntryPrice', 'Entry Price')}: <span className="text-foreground">{entryPrice}</span></span>
                                  )}
                                  {leverage && (
                                    <span>{t('signals.walletTracking.logLeverage', 'Leverage')}: <span className="text-foreground">{leverage}</span></span>
                                  )}
                                  {unrealizedPnl && (
                                    <span>{t('signals.walletTracking.logUnrealizedPnl', 'Unrealized PnL')}: <span className="text-foreground">${unrealizedPnl}</span></span>
                                  )}
                                  {liquidationPrice && (
                                    <span>{t('signals.walletTracking.logLiquidationPrice', 'Liquidation Price')}: <span className="text-foreground">{liquidationPrice}</span></span>
                                  )}
                                  {closedPnl && (
                                    <span>{t('signals.walletTracking.logClosedPnl', 'Closed PnL')}: <span className="text-foreground">${closedPnl}</span></span>
                                  )}
                                  {averagePrice && (
                                    <span>{t('signals.walletTracking.logAveragePrice', 'Avg Price')}: <span className="text-foreground">{averagePrice}</span></span>
                                  )}
                                </div>
                              )}
                            </div>
                          )
                        }
                        // Pool trigger (new format)
                        if ('logic' in triggerData && 'signals_triggered' in triggerData) {
                          const logic = triggerData.logic as string
                          const triggeredSignals = triggerData.signals_triggered as Array<{
                            signal_name: string; metric: string; current_value?: number; threshold?: number;
                            direction?: string; volume?: number; volume_threshold?: number;
                          }>
                          return (
                            <div className="space-y-1">
                              <div className="flex items-center gap-2">
                                <span className={`px-1.5 py-0.5 rounded text-xs ${logic === 'AND' ? 'bg-blue-500/20 text-blue-400' : 'bg-green-500/20 text-green-400'}`}>
                                  {logic}
                                </span>
                                <span>Triggered signals:</span>
                              </div>
                              {triggeredSignals.map((s, i) => (
                                <div key={i} className="ml-4 text-xs">
                                  {s.metric === 'taker_volume' ? (
                                    <>• {s.signal_name}: {s.direction?.toUpperCase()} | ratio={s.current_value?.toFixed(2)} (≥{s.threshold}) | vol=${((s.volume || 0) / 1e6).toFixed(2)}M (≥${((s.volume_threshold || 0) / 1e6).toFixed(2)}M)</>
                                  ) : s.metric === 'macd' ? (
                                    <>• {s.signal_name}: {s.triggered_event} | MACD={s.values?.macd?.toFixed(4)} | Hist={s.values?.histogram?.toFixed(4)}</>
                                  ) : (
                                    <>• {s.signal_name}: {s.metric} = {s.current_value?.toFixed(4)} (threshold: {s.threshold})</>
                                  )}
                                </div>
                              ))}
                            </div>
                          )
                        }
                        // taker_volume composite signal (legacy)
                        if ('direction' in triggerData && 'ratio' in triggerData) {
                          const dir = triggerData.direction as string
                          const ratio = (triggerData.ratio as number)?.toFixed(2)
                          const ratioThreshold = (triggerData.ratio_threshold as number) || 1.5
                          const buy = (triggerData.buy as number) || 0
                          const sell = (triggerData.sell as number) || 0
                          const totalVol = (buy + sell).toLocaleString()
                          const volThreshold = ((triggerData.volume_threshold as number) || 0).toLocaleString()
                          return `${dir.toUpperCase()} | Ratio: ${ratio} (≥${ratioThreshold}) | Vol: $${totalVol} (≥$${volThreshold})`
                        }
                        // Standard signal (legacy)
                        if ('metric' in triggerData && 'value' in triggerData) {
                          const val = (triggerData.value as number)?.toFixed(4)
                          return `${triggerData.metric}: ${val} ${triggerData.operator} ${triggerData.threshold}`
                        }
                        return null
                      }
                      return (
                        <div key={log.id} className="p-3 bg-muted rounded">
                          <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-primary">{log.symbol}</span>
                            <ExchangeBadge exchange={logExchange} size="xs" />
                              {isWalletTrigger || isPoolTrigger ? (
                                <span className="text-sm px-2 py-0.5 bg-purple-500/20 text-purple-400 rounded">
                                  Pool: {poolName || `#${log.pool_id}`}
                                </span>
                              ) : (
                                <span className="text-sm">{signalName || `Signal #${log.signal_id}`}</span>
                              )}
                            </div>
                            <span className="text-xs text-muted-foreground">
                              {new Date(timestamp).toLocaleString()}
                            </span>
                          </div>
                          {triggerData && (
                            <div className="text-xs text-muted-foreground mt-1">
                              {formatTriggerDetails()}
                            </div>
                          )}
                          {log.market_regime && (
                            <div className="text-xs mt-1 flex items-center gap-2">
                              <span className={`px-1.5 py-0.5 rounded ${
                                log.market_regime.regime === 'breakout' ? 'bg-green-500/20 text-green-400' :
                                log.market_regime.regime === 'continuation' ? 'bg-blue-500/20 text-blue-400' :
                                log.market_regime.regime === 'absorption' ? 'bg-yellow-500/20 text-yellow-400' :
                                log.market_regime.regime === 'stop_hunt' ? 'bg-red-500/20 text-red-400' :
                                log.market_regime.regime === 'trap' ? 'bg-orange-500/20 text-orange-400' :
                                log.market_regime.regime === 'exhaustion' ? 'bg-purple-500/20 text-purple-400' :
                                'bg-gray-500/20 text-gray-400'
                              }`}>
                                {log.market_regime.regime.toUpperCase()}
                              </span>
                              <span className={log.market_regime.direction === 'bullish' ? 'text-green-400' : 'text-red-400'}>
                                {log.market_regime.direction}
                              </span>
                              <span className="text-muted-foreground">
                                conf: {(log.market_regime.confidence * 100).toFixed(0)}%
                              </span>
                            </div>
                          )}
                        </div>
                      )
                    })}
                    {/* Load more button */}
                    {logs.length < logsTotal && (
                      <div className="flex justify-center pt-2">
                        <Button variant="outline" size="sm" onClick={loadMoreLogs}>
                          {t('signals.loadMore', 'Load More')} ({logs.length}/{logsTotal})
                        </Button>
                      </div>
                    )}
                  </div>
                </ScrollArea>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="regime" className="space-y-4">
          <MarketRegimeConfig />
        </TabsContent>
      </Tabs>

      {/* Signal Dialog — wide two-column when factor selected */}
      <Dialog open={signalDialogOpen} onOpenChange={v => { setSignalDialogOpen(v); if (!v) { setFactorSearch(''); setFactorCategory('all') } }}>
        <DialogContent className={signalForm.metric.startsWith('factor:') || signalForm.metric === '_pick_factor' ? 'max-w-[960px]' : 'max-w-lg'}>
          <DialogHeader>
            <DialogTitle>{editingSignal ? t('signals.dialog.editSignal', 'Edit Signal') : t('signals.dialog.newSignal', 'New Signal')}</DialogTitle>
            <DialogDescription>{t('signals.dialog.configureSignal', 'Configure when this signal should trigger')}</DialogDescription>
          </DialogHeader>
          <div className={signalForm.metric.startsWith('factor:') || signalForm.metric === '_pick_factor' ? 'grid grid-cols-[340px_1fr] gap-6' : ''}>
          {/* Left column: signal config */}
          <div className="space-y-4">
            <div>
              <Label>{t('signals.dialog.signalNameLabel', 'Signal Name')}</Label>
              <Input
                value={signalForm.signal_name}
                onChange={e => setSignalForm(prev => ({ ...prev, signal_name: e.target.value }))}
                placeholder={t('signals.dialog.signalNamePlaceholder', 'e.g., OI Surge Signal')}
              />
            </div>
            <div>
              <Label>{t('signals.dialog.descriptionLabel', 'Description')}</Label>
              <Input
                value={signalForm.description}
                onChange={e => setSignalForm(prev => ({ ...prev, description: e.target.value }))}
                placeholder={t('signals.dialog.descriptionPlaceholder', 'What market condition does this signal detect?')}
              />
            </div>
            <div>
              <Label>{t('signals.dialog.exchangeLabel', 'Exchange')}</Label>
              <Select value={signalForm.exchange} onValueChange={v => setSignalForm(prev => ({ ...prev, exchange: v }))}>
                <SelectTrigger>
                  <SelectValue>
                    <span className="flex items-center gap-2">
                      {signalForm.exchange === 'hyperliquid' ? <HyperliquidLogo /> : <BinanceLogo />}
                      {signalForm.exchange === 'hyperliquid' ? 'Hyperliquid' : 'Binance'}
                    </span>
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="hyperliquid">
                    <span className="flex items-center gap-2"><HyperliquidLogo />Hyperliquid</span>
                  </SelectItem>
                  <SelectItem value="binance">
                    <span className="flex items-center gap-2"><BinanceLogo />Binance</span>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{t('signals.dialog.metricLabel', 'Metric')}</Label>
              <Select
                value={signalForm.metric === '_pick_factor' ? '_pick_factor' : signalForm.metric}
                onValueChange={v => {
                  if (v === '_pick_factor') {
                    setSignalForm(prev => ({ ...prev, metric: '_pick_factor' }))
                  } else {
                    setSignalForm(prev => ({ ...prev, metric: v }))
                    setFactorSearch(''); setFactorCategory('all')
                  }
                }}
              >
                <SelectTrigger>
                  <SelectValue>
                    {signalForm.metric.startsWith('factor:')
                      ? <span className="flex items-center gap-1.5"><FlaskConical className="w-3.5 h-3.5 text-[#B8860B]" />{signalForm.metric.split(':')[1]}</span>
                      : signalForm.metric === '_pick_factor'
                        ? <span className="flex items-center gap-1.5 text-[#B8860B]"><FlaskConical className="w-3.5 h-3.5" />{t('signals.dialog.selectFactor', 'Select factor →')}</span>
                        : undefined}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <div className="px-2 py-1 text-xs font-semibold text-muted-foreground flex items-center gap-1.5"><Activity className="w-3.5 h-3.5" />{t('signals.dialog.marketFlowMetrics', 'Market Flow')}</div>
                  {METRICS.map(m => <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>)}
                  {factorLibrary.length > 0 && (
                    <SelectItem value="_pick_factor">
                      <span className="flex items-center gap-1.5 text-[#B8860B]">
                        <FlaskConical className="w-3.5 h-3.5" />
                        {t('signals.dialog.factorMetrics', 'Factor Library')} ({factorLibrary.length})
                      </span>
                    </SelectItem>
                  )}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                {signalForm.metric.startsWith('factor:')
                  ? factorLibrary.find(f => f.name === signalForm.metric.split(':')[1])?.description || signalForm.metric
                  : signalForm.metric === '_pick_factor'
                    ? t('signals.dialog.pickFactorHint', 'Browse and select a factor from the panel on the right')
                    : METRICS.find(m => m.value === signalForm.metric)?.desc}
              </p>
            </div>
            {signalForm.metric === 'taker_volume' ? (
              /* Composite signal UI for taker_volume */
              <div className="space-y-4 p-3 bg-blue-500/10 rounded-lg border border-blue-500/30">
                <div className="text-xs font-medium text-blue-400">{t('signals.dialog.compositeConfig', 'Composite Signal Configuration')}</div>
                <div>
                  <Label>{t('signals.dialog.directionLabel', 'Direction')}</Label>
                  <Select value={signalForm.direction} onValueChange={v => setSignalForm(prev => ({ ...prev, direction: v }))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {TAKER_DIRECTIONS.map(d => <SelectItem key={d.value} value={d.value}>{d.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground mt-1">
                    {TAKER_DIRECTIONS.find(d => d.value === signalForm.direction)?.desc}
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <Label>{t('signals.dialog.ratioThreshold', 'Ratio Threshold')}</Label>
                    <Input
                      type="number"
                      step="0.1"
                      min="1.01"
                      value={signalForm.ratio_threshold}
                      onChange={e => setSignalForm(prev => ({ ...prev, ratio_threshold: parseFloat(e.target.value) || 1.5 }))}
                    />
                    <p className="text-xs text-muted-foreground mt-1">{t('signals.dialog.ratioThresholdDesc', 'Multiplier (e.g., 1.5 = 50% more). Symmetric for buy/sell.')}</p>
                  </div>
                  <div>
                    <Label>{t('signals.dialog.volumeThreshold', 'Volume Threshold')}</Label>
                    <Input
                      type="number"
                      step="1000"
                      min="0"
                      value={signalForm.volume_threshold}
                      onChange={e => setSignalForm(prev => ({ ...prev, volume_threshold: parseFloat(e.target.value) || 0 }))}
                    />
                    <p className="text-xs text-muted-foreground mt-1">{t('signals.dialog.volumeThresholdDesc', 'Min volume (USD)')}</p>
                  </div>
                </div>
              </div>
            ) : signalForm.metric === 'macd' ? (
              /* MACD event-based signal UI */
              <div className="space-y-4 p-3 bg-purple-500/10 rounded-lg border border-purple-500/30">
                <div className="text-xs font-medium text-purple-400">{t('signals.dialog.macdConfig', 'MACD Event Configuration')}</div>
                <div>
                  <Label>{t('signals.dialog.eventTypes', 'Event Types (select one or more)')}</Label>
                  <div className="grid grid-cols-2 gap-2 mt-2">
                    {MACD_EVENT_TYPES.map(evt => (
                      <label key={evt.value} className="flex items-center gap-2 p-2 rounded border cursor-pointer hover:bg-accent">
                        <input
                          type="checkbox"
                          checked={signalForm.event_types.includes(evt.value)}
                          onChange={e => {
                            if (e.target.checked) {
                              setSignalForm(prev => ({ ...prev, event_types: [...prev.event_types, evt.value] }))
                            } else {
                              setSignalForm(prev => ({ ...prev, event_types: prev.event_types.filter(v => v !== evt.value) }))
                            }
                          }}
                          className="rounded"
                        />
                        <div>
                          <div className="text-sm font-medium">{evt.label}</div>
                          <div className="text-xs text-muted-foreground">{evt.desc}</div>
                        </div>
                      </label>
                    ))}
                  </div>
                  {signalForm.event_types.length === 0 && (
                    <p className="text-xs text-red-500 mt-1">{t('signals.dialog.selectAtLeastOne', 'Please select at least one event type')}</p>
                  )}
                </div>
              </div>
            ) : (
              /* Standard signal UI */
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label>{t('signals.dialog.operatorLabel', 'Operator')}</Label>
                  <Select value={signalForm.operator} onValueChange={v => setSignalForm(prev => ({ ...prev, operator: v }))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {OPERATORS.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground mt-1">
                    {OPERATORS.find(o => o.value === signalForm.operator)?.desc}
                  </p>
                </div>
                <div>
                  <Label>{t('signals.dialog.thresholdLabel', 'Threshold')}</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={signalForm.threshold}
                    onChange={e => setSignalForm(prev => ({ ...prev, threshold: parseFloat(e.target.value) || 0 }))}
                  />
                  <p className="text-xs text-muted-foreground mt-1">{t('signals.dialog.thresholdDesc', 'Value to compare against')}</p>
                </div>
              </div>
            )}
            <div>
              <Label>{t('signals.dialog.timeWindowLabel', 'Time Window')}</Label>
              <Select value={signalForm.time_window} onValueChange={v => setSignalForm(prev => ({ ...prev, time_window: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TIME_WINDOWS.map(tw => <SelectItem key={tw.value} value={tw.value}>{tw.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                {TIME_WINDOWS.find(tw => tw.value === signalForm.time_window)?.desc}
              </p>
            </div>

            {/* Statistical Analysis Preview - hide for event-based signals like MACD and factor metrics (factor analysis shown in right panel) */}
            {signalForm.metric !== 'macd' && !signalForm.metric.startsWith('factor:') && signalForm.metric !== '_pick_factor' && (
            <div className="p-3 bg-muted/50 rounded-lg border">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-medium">{t('signals.dialog.statisticalAnalysis', 'Statistical Analysis')}</span>
                <Select value={analysisSymbol} onValueChange={setAnalysisSymbol}>
                  <SelectTrigger className="w-24 h-7 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {watchlistSymbols.length > 0 ? (
                      watchlistSymbols.map(sym => <SelectItem key={sym} value={sym}>{sym}</SelectItem>)
                    ) : (
                      <SelectItem value="BTC">BTC</SelectItem>
                    )}
                  </SelectContent>
                </Select>
                {watchlistSymbols.length === 0 && (
                  <span className="text-xs text-muted-foreground">{t('signals.dialog.addSymbolsHint', '(Add symbols in Settings)')}</span>
                )}
              </div>
              {analysisLoading ? (
                <p className="text-xs text-muted-foreground">{t('signals.dialog.loadingAnalysis', 'Loading analysis...')}</p>
              ) : metricAnalysis?.status === 'ok' && metricAnalysis.metric === signalForm.metric ? (
                signalForm.metric === 'taker_volume' && (metricAnalysis as any).ratio_statistics ? (
                  /* taker_volume composite analysis */
                  <div className="space-y-3">
                    <p className="text-xs text-muted-foreground">
                      Based on {metricAnalysis.sample_count} samples over {metricAnalysis.time_range_hours.toFixed(1)} hours
                    </p>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="p-2 bg-background rounded border">
                        <div className="text-xs font-medium mb-1">Ratio Multiplier</div>
                        <div className="text-xs text-muted-foreground mb-2">
                          Log range: {(metricAnalysis as any).ratio_statistics?.min?.toFixed(2)} ~ {(metricAnalysis as any).ratio_statistics?.max?.toFixed(2)} (0=balanced)
                        </div>
                        <div className="flex flex-wrap gap-1">
                          <button type="button" onClick={() => setSignalForm(prev => ({ ...prev, ratio_threshold: (metricAnalysis as any).suggestions?.ratio?.aggressive }))} className="text-xs px-1.5 py-0.5 bg-muted border rounded hover:bg-accent">
                            {(metricAnalysis as any).suggestions?.ratio?.aggressive?.toFixed(2)}x
                          </button>
                          <button type="button" onClick={() => setSignalForm(prev => ({ ...prev, ratio_threshold: (metricAnalysis as any).suggestions?.ratio?.moderate }))} className="text-xs px-1.5 py-0.5 bg-primary/10 border border-primary rounded hover:bg-primary/20">
                            {(metricAnalysis as any).suggestions?.ratio?.moderate?.toFixed(2)}x ★
                          </button>
                          <button type="button" onClick={() => setSignalForm(prev => ({ ...prev, ratio_threshold: (metricAnalysis as any).suggestions?.ratio?.conservative }))} className="text-xs px-1.5 py-0.5 bg-muted border rounded hover:bg-accent">
                            {(metricAnalysis as any).suggestions?.ratio?.conservative?.toFixed(2)}x
                          </button>
                        </div>
                      </div>
                      <div className="p-2 bg-background rounded border">
                        <div className="text-xs font-medium mb-1">Volume (USD)</div>
                        <div className="text-xs text-muted-foreground mb-2">
                          Range: {((metricAnalysis as any).volume_statistics?.min / 1000)?.toFixed(0)}K ~ {((metricAnalysis as any).volume_statistics?.max / 1000)?.toFixed(0)}K
                        </div>
                        <div className="flex flex-wrap gap-1">
                          <button type="button" onClick={() => setSignalForm(prev => ({ ...prev, volume_threshold: (metricAnalysis as any).suggestions?.volume?.low }))} className="text-xs px-1.5 py-0.5 bg-muted border rounded hover:bg-accent">
                            {((metricAnalysis as any).suggestions?.volume?.low / 1000)?.toFixed(0)}K
                          </button>
                          <button type="button" onClick={() => setSignalForm(prev => ({ ...prev, volume_threshold: (metricAnalysis as any).suggestions?.volume?.medium }))} className="text-xs px-1.5 py-0.5 bg-primary/10 border border-primary rounded hover:bg-primary/20">
                            {((metricAnalysis as any).suggestions?.volume?.medium / 1000)?.toFixed(0)}K ★
                          </button>
                          <button type="button" onClick={() => setSignalForm(prev => ({ ...prev, volume_threshold: (metricAnalysis as any).suggestions?.volume?.high }))} className="text-xs px-1.5 py-0.5 bg-muted border rounded hover:bg-accent">
                            {((metricAnalysis as any).suggestions?.volume?.high / 1000)?.toFixed(0)}K
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : metricAnalysis.suggestions ? (
                  /* Standard metric analysis with suggestions */
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">
                      Based on {metricAnalysis.sample_count} samples over {metricAnalysis.time_range_hours.toFixed(1)} hours
                    </p>
                    {metricAnalysis.warning && (
                      <p className="text-xs text-yellow-600">{metricAnalysis.warning}</p>
                    )}
                    <div className="text-xs">
                      <span className="text-muted-foreground">Range: </span>
                      {signalForm.metric === 'funding'
                        ? `${metricAnalysis.statistics?.min.toFixed(1)} ~ ${metricAnalysis.statistics?.max.toFixed(1)}`
                        : `${metricAnalysis.statistics?.min.toFixed(4)} ~ ${metricAnalysis.statistics?.max.toFixed(4)}`
                      }
                    </div>
                    <div className="text-xs font-medium mt-2">{t('signals.dialog.suggestedThresholds', 'Suggested thresholds:')}</div>
                    <div className="flex flex-wrap gap-2 mt-1">
                      <button
                        type="button"
                        onClick={() => setSignalForm(prev => ({ ...prev, threshold: metricAnalysis.suggestions!.aggressive.threshold }))}
                        className="text-xs px-2 py-1 bg-background border rounded hover:bg-accent"
                        title={metricAnalysis.suggestions.aggressive.description}
                      >
                        {t('signals.dialog.aggressive', 'Aggressive')} {signalForm.metric === 'funding'
                          ? metricAnalysis.suggestions.aggressive.threshold.toFixed(1)
                          : metricAnalysis.suggestions.aggressive.threshold.toFixed(4)}
                        {(metricAnalysis.suggestions.aggressive as any).multiplier && ` (${(metricAnalysis.suggestions.aggressive as any).multiplier}x)`}
                      </button>
                      <button
                        type="button"
                        onClick={() => setSignalForm(prev => ({ ...prev, threshold: metricAnalysis.suggestions!.moderate.threshold }))}
                        className="text-xs px-2 py-1 bg-primary/10 border border-primary rounded hover:bg-primary/20"
                        title={metricAnalysis.suggestions.moderate.description}
                      >
                        {t('signals.dialog.moderate', 'Moderate')} {signalForm.metric === 'funding'
                          ? metricAnalysis.suggestions.moderate.threshold.toFixed(1)
                          : metricAnalysis.suggestions.moderate.threshold.toFixed(4)}
                        {(metricAnalysis.suggestions.moderate as any).multiplier && ` (${(metricAnalysis.suggestions.moderate as any).multiplier}x)`} ★
                      </button>
                      <button
                        type="button"
                        onClick={() => setSignalForm(prev => ({ ...prev, threshold: metricAnalysis.suggestions!.conservative.threshold }))}
                        className="text-xs px-2 py-1 bg-background border rounded hover:bg-accent"
                        title={metricAnalysis.suggestions.conservative.description}
                      >
                        {t('signals.dialog.conservative', 'Conservative')} {signalForm.metric === 'funding'
                          ? metricAnalysis.suggestions.conservative.threshold.toFixed(1)
                          : metricAnalysis.suggestions.conservative.threshold.toFixed(4)}
                        {(metricAnalysis.suggestions.conservative as any).multiplier && ` (${(metricAnalysis.suggestions.conservative as any).multiplier}x)`}
                      </button>
                    </div>
                  </div>
                ) : (
                  /* Fallback when no suggestions available */
                  <p className="text-xs text-muted-foreground">{t('signals.dialog.analysisDataMismatch', 'Analysis data format mismatch. Please reselect metric.')}</p>
                )
              ) : metricAnalysis?.status === 'insufficient_data' ? (
                <p className="text-xs text-yellow-600">{metricAnalysis.message}</p>
              ) : (
                <p className="text-xs text-muted-foreground">{t('signals.dialog.unableToLoadAnalysis', 'Unable to load analysis')}</p>
              )}
            </div>
            )}

            <div className="flex items-center gap-2">
              <Switch checked={signalForm.enabled} onCheckedChange={v => setSignalForm(prev => ({ ...prev, enabled: v }))} />
              <Label>{t('signals.dialog.enabledLabel', 'Enabled')}</Label>
            </div>
          </div>

          {/* Right column: Factor browser panel (only when factor mode) */}
          {(signalForm.metric.startsWith('factor:') || signalForm.metric === '_pick_factor') && (
            <div className="space-y-3 border-l pl-5">
              <div className="flex items-center gap-2">
                <FlaskConical className="w-4 h-4 text-[#B8860B]" />
                <span className="text-sm font-medium text-[#B8860B]">{t('signals.dialog.factorBrowser', 'Factor Browser')}</span>
              </div>
              {/* Search */}
              <Input
                placeholder={t('signals.dialog.searchFactors', 'Search factors...')}
                value={factorSearch}
                onChange={e => setFactorSearch(e.target.value)}
                className="h-8 text-xs"
              />
              {/* Category filter */}
              <div className="flex flex-wrap gap-1">
                {['all', ...Object.keys(FACTOR_CATEGORY_LABELS)].filter(c =>
                  c === 'all' || factorLibrary.some(f => f.category === c)
                ).map(c => (
                  <button key={c} type="button"
                    className={`text-[10px] px-1.5 py-0.5 rounded border ${factorCategory === c ? 'bg-[#B8860B]/20 text-[#B8860B] border-[#B8860B]/40' : 'text-muted-foreground border-transparent hover:bg-accent'}`}
                    onClick={() => setFactorCategory(c)}
                  >{c === 'all' ? 'All' : FACTOR_CATEGORY_LABELS[c] || c}</button>
                ))}
              </div>
              {/* Factor list */}
              <ScrollArea className="h-[280px]">
                <div className="space-y-1 pr-2">
                  {factorLibrary
                    .filter(f => (factorCategory === 'all' || f.category === factorCategory) &&
                      (!factorSearch || f.name.toLowerCase().includes(factorSearch.toLowerCase()) || f.description.toLowerCase().includes(factorSearch.toLowerCase())))
                    .map(f => {
                      const isSelected = signalForm.metric === `factor:${f.name}`
                      return (
                        <button key={f.name} type="button"
                          className={`w-full text-left p-2 rounded text-xs transition-colors ${isSelected ? 'bg-[#B8860B]/15 border border-[#B8860B]/40' : 'hover:bg-accent border border-transparent'}`}
                          onClick={() => setSignalForm(prev => ({ ...prev, metric: `factor:${f.name}` }))}
                        >
                          <div className="flex items-center gap-2">
                            <span className={`text-[10px] px-1 rounded ${isSelected ? 'bg-[#B8860B]/30 text-[#D4A832]' : 'bg-muted text-muted-foreground'}`}>
                              {FACTOR_CATEGORY_LABELS[f.category] || f.category}
                            </span>
                            <span className={`font-mono font-medium ${isSelected ? 'text-[#D4A832]' : ''}`}>{f.name}</span>
                          </div>
                          <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-1">{f.description}</p>
                        </button>
                      )
                    })}
                </div>
              </ScrollArea>
              {/* Selected factor details */}
              {signalForm.metric.startsWith('factor:') && (() => {
                const factor = factorLibrary.find(f => f.name === signalForm.metric.split(':')[1])
                if (!factor) return null
                return (
                  <div className="space-y-2 pt-2 border-t">
                    <div className="p-2 bg-[#B8860B]/10 rounded border border-[#B8860B]/30">
                      <code className="text-[11px] text-[#D4A832] break-all">{factor.expression}</code>
                    </div>
                    {/* Factor percentile distribution + threshold suggestions */}
                    {analysisLoading ? (
                      <p className="text-[10px] text-muted-foreground">{t('signals.dialog.loadingAnalysis', 'Loading analysis...')}</p>
                    ) : (metricAnalysis as any)?.factor_percentiles ? (() => {
                      const pct = (metricAnalysis as any).factor_percentiles
                      const val = (metricAnalysis as any).factor_latest_value
                      const isZeroCentered = pct.min < 0 && pct.max > 0
                      return (
                        <div className="space-y-2">
                          {val != null && (
                            <p className="text-[10px]">
                              {t('signals.dialog.currentValue', 'Current value')}: <span className="font-mono font-bold">{Number(val).toFixed(6)}</span>
                              <span className="text-muted-foreground ml-1">(P{pct.current_pct?.toFixed(0)})</span>
                            </p>
                          )}
                          <div className="grid grid-cols-5 gap-1">
                            {['p5', 'p25', 'p50', 'p75', 'p95'].map(k => (
                              <button key={k} type="button"
                                className="p-1 bg-background rounded border text-center hover:bg-accent transition-colors"
                                onClick={() => setSignalForm(prev => ({ ...prev, threshold: pct[k] }))}
                                title={t('signals.dialog.clickToSetThreshold', 'Click to set as threshold')}
                              >
                                <div className="text-[9px] text-muted-foreground uppercase">{k}</div>
                                <div className="text-[10px] font-mono font-bold">{pct[k]?.toFixed(4)}</div>
                              </button>
                            ))}
                          </div>
                          <p className="text-[9px] text-muted-foreground">
                            {isZeroCentered
                              ? t('signals.dialog.zeroCenteredHint', 'Zero-centered factor: |x| > is useful for bidirectional deviation.')
                              : t('signals.dialog.factorThresholdHint', 'Set threshold based on the current value above. Factor triggers at K-line close.')}
                          </p>
                        </div>
                      )
                    })() : null}
                  </div>
                )
              })()}
            </div>
          )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSignalDialogOpen(false)} disabled={savingSignal}>{t('signals.dialog.cancel', 'Cancel')}</Button>
            <Button onClick={handleSaveSignal} disabled={savingSignal || signalForm.metric === '_pick_factor'}>
              {savingSignal ? t('signals.dialog.saving', 'Saving...') : t('signals.dialog.save', 'Save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Pool Dialog */}
      <Dialog open={poolDialogOpen} onOpenChange={setPoolDialogOpen}>
        <DialogContent className="max-w-5xl">
          <DialogHeader>
            <DialogTitle>{editingPool ? t('signals.dialog.editPool', 'Edit Pool') : t('signals.dialog.newPool', 'New Pool')}</DialogTitle>
            <DialogDescription>{t('signals.dialog.configurePool', 'Configure signal pool')}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-6 lg:grid-cols-[0.95fr_1.25fr]">
            <div className="space-y-4">
              <div>
                <Label>{t('signals.dialog.poolNameLabel', 'Pool Name')}</Label>
                <Input
                  value={poolForm.pool_name}
                  onChange={e => setPoolForm(prev => ({ ...prev, pool_name: e.target.value }))}
                  placeholder={t('signals.dialog.poolNamePlaceholder', 'e.g., BTC Momentum Pool')}
                />
              </div>
              <div>
                <Label>{t('signals.dialog.sourceTypeLabel', 'Source Type')}</Label>
                <Select
                  value={poolForm.source_type}
                  onValueChange={(v: PoolSourceType) =>
                    setPoolForm(prev => ({
                      ...prev,
                      source_type: v,
                      logic: v === 'wallet_tracking' ? 'OR' : prev.logic,
                      signal_ids: v === 'market_signals' ? prev.signal_ids : [],
                      symbols: v === 'market_signals' ? prev.symbols : [],
                      source_config: v === 'wallet_tracking'
                        ? {
                            ...prev.source_config,
                            addresses: prev.source_config.addresses || [],
                            event_types: prev.source_config.event_types || ['position_change', 'fill', 'liquidation'],
                            sync_mode: 'ws_only',
                          }
                        : {
                            addresses: [],
                            event_types: ['position_change', 'fill', 'liquidation'],
                            sync_mode: 'ws_only',
                          },
                    }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="market_signals">{t('signals.dialog.marketSignalsType', 'Market Signals')}</SelectItem>
                    <SelectItem value="wallet_tracking">{t('signals.walletTracking.sourceTypeLabel', 'Wallet Tracking')}</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground mt-1">
                  {poolForm.source_type === 'wallet_tracking'
                    ? t('signals.walletTracking.poolConfigHint', 'Wallet pools use synced Hyper Insight wallets and real-time event types instead of market indicators.')
                    : t('signals.dialog.marketSignalsTypeHint', 'Market pools continue to use symbols, signal definitions, and exchange-specific trigger logic.')}
                </p>
              </div>
              <div>
                <Label>{t('signals.dialog.exchangeLabel', 'Exchange')}</Label>
                <Select value={poolForm.exchange} onValueChange={v => {
                  if (poolForm.source_type === 'market_signals') {
                    const matchingSignalIds = poolForm.signal_ids.filter(id => {
                      const signal = signals.find(s => s.id === id)
                      return signal?.exchange === v
                    })
                    setPoolForm(prev => ({ ...prev, exchange: v, signal_ids: matchingSignalIds }))
                    loadWatchlist(v)
                    return
                  }
                  setPoolForm(prev => ({ ...prev, exchange: v }))
                }}>
                  <SelectTrigger>
                    <SelectValue>
                      <span className="flex items-center gap-2">
                        {poolForm.exchange === 'hyperliquid' ? <HyperliquidLogo /> : <BinanceLogo />}
                        {poolForm.exchange === 'hyperliquid' ? 'Hyperliquid' : 'Binance'}
                      </span>
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="hyperliquid">
                      <span className="flex items-center gap-2"><HyperliquidLogo />Hyperliquid</span>
                    </SelectItem>
                    <SelectItem value="binance">
                      <span className="flex items-center gap-2"><BinanceLogo />Binance</span>
                    </SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground mt-1">
                  {t('signals.dialog.exchangeDesc', 'Select the target exchange for this pool')}
                </p>
              </div>
              <div className="flex items-center gap-2 pt-2">
                <Switch checked={poolForm.enabled} onCheckedChange={v => setPoolForm(prev => ({ ...prev, enabled: v }))} />
                <Label>{t('signals.dialog.enabledLabel', 'Enabled')}</Label>
              </div>
            </div>

            <div className="space-y-4">
              {poolForm.source_type === 'market_signals' ? (
                <>
                  <div>
                    <Label>{t('signals.dialog.symbolsLabel', 'Symbols')}</Label>
                    <div className="flex flex-wrap gap-2 mt-2 rounded-lg border p-3">
                      {watchlistSymbols.length > 0 ? (
                        watchlistSymbols.map(symbol => (
                          <Button
                            key={symbol}
                            variant={poolForm.symbols.includes(symbol) ? 'default' : 'outline'}
                            size="sm"
                            onClick={() => toggleSymbol(symbol)}
                          >
                            {symbol}
                          </Button>
                        ))
                      ) : (
                        <p className="text-sm text-muted-foreground">{t('signals.dialog.noSymbolsInWatchlist', 'No symbols in watchlist. Configure watchlist first.')}</p>
                      )}
                    </div>
                  </div>
                  <div>
                    <Label>{t('signals.dialog.signalsLabel', 'Signals')}</Label>
                    <div className="space-y-2 mt-2 max-h-48 overflow-y-auto rounded-lg border p-3">
                      {signals.map(signal => {
                        const isMatchingExchange = signal.exchange === poolForm.exchange
                        const isDisabled = !isMatchingExchange
                        return (
                          <div key={signal.id} className={`flex items-center gap-2 ${isDisabled ? 'opacity-50' : ''}`}>
                            <Switch
                              checked={poolForm.signal_ids.includes(signal.id)}
                              onCheckedChange={() => toggleSignalInPool(signal.id)}
                              disabled={isDisabled}
                            />
                            <span className="text-sm flex items-center gap-1.5">
                              {signal.signal_name}
                              {isDisabled && (
                                <span className="inline-flex items-center" title={`${signal.exchange} signal`}>
                                  {signal.exchange === 'binance' ? <BinanceLogo /> : <HyperliquidLogo />}
                                </span>
                              )}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                  <div>
                    <Label>{t('signals.dialog.triggerLogicLabel', 'Trigger Logic')}</Label>
                    <Select value={poolForm.logic} onValueChange={(v: 'OR' | 'AND') => setPoolForm(prev => ({ ...prev, logic: v }))}>
                      <SelectTrigger className="mt-2">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="OR">{t('signals.dialog.orLogic', 'OR - Any signal triggers pool')}</SelectItem>
                        <SelectItem value="AND">{t('signals.dialog.andLogic', 'AND - All signals must trigger')}</SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground mt-1">
                      {poolForm.logic === 'AND'
                        ? t('signals.dialog.andLogicDesc', 'Pool triggers only when ALL selected signals meet their conditions simultaneously')
                        : t('signals.dialog.orLogicDesc', 'Pool triggers when ANY selected signal meets its condition')}
                    </p>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <Label>{t('signals.walletTracking.addresses', 'Tracked Wallets')}</Label>
                    <div className="mt-2 flex flex-wrap gap-2 rounded-lg border p-3 min-h-[120px] content-start">
                      {walletRuntime?.synced_addresses?.length ? (
                        walletRuntime.synced_addresses.map(address => (
                          <Button
                            key={address}
                            variant={(poolForm.source_config.addresses || []).includes(address) ? 'default' : 'outline'}
                            size="sm"
                            onClick={() => toggleWalletAddressInPool(address)}
                          >
                            {address}
                          </Button>
                        ))
                      ) : (
                        <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground w-full">
                          {t('signals.walletTracking.addressSyncPlaceholder', 'Tracked wallet sync will appear here after the Hyper Insight websocket client is enabled. New synced wallets stay opt-in and are never added to an existing pool automatically.')}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Label>{t('signals.walletTracking.eventTypes', 'Event Types')}</Label>
                    <div className="flex flex-wrap gap-2 mt-2 rounded-lg border p-3 min-h-[88px] content-start">
                      {WALLET_EVENT_TYPES.map(eventType => (
                        <Button
                          key={eventType}
                          variant={(poolForm.source_config.event_types || []).includes(eventType) ? 'default' : 'outline'}
                          size="sm"
                          onClick={() => toggleWalletEventType(eventType)}
                        >
                          {formatWalletEventType(t, eventType)}
                        </Button>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPoolDialogOpen(false)} disabled={savingPool}>{t('signals.dialog.cancel', 'Cancel')}</Button>
            <Button onClick={handleSavePool} disabled={savingPool}>
              {savingPool ? t('signals.dialog.saving', 'Saving...') : t('signals.dialog.save', 'Save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Signal/Pool Preview Dialog */}
      <Dialog open={previewDialogOpen} onOpenChange={setPreviewDialogOpen}>
        <DialogContent className="w-[1200px] max-w-[95vw] h-[860px] max-h-[95vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3">
              <span>{previewPool ? t('signals.preview.poolPreview', { name: previewPool.pool_name, defaultValue: `Pool Preview: ${previewPool.pool_name}` }) : t('signals.preview.signalPreview', { name: previewSignal?.signal_name, defaultValue: `Signal Preview: ${previewSignal?.signal_name}` })}</span>
              <ExchangeBadge exchange={previewPool?.exchange || previewSignal?.exchange || 'hyperliquid'} />
            </DialogTitle>
            <DialogDescription>
              {previewPool
                ? t('signals.preview.poolBacktestDesc', { logic: previewPool.logic || 'OR', defaultValue: `Historical backtest showing combined triggers (${previewPool.logic || 'OR'} logic)` })
                : t('signals.preview.signalBacktestDesc', 'Historical backtest showing where this signal would have triggered')}
            </DialogDescription>
          </DialogHeader>

          {previewLoading ? (
            <div className="flex items-center justify-center h-[500px] gap-3">
              <PacmanLoader className="w-16 h-8" />
              <span className="text-muted-foreground">{t('signals.preview.loadingPreview', 'Loading preview data...')}</span>
            </div>
          ) : previewData?.error ? (
            <div className="flex items-center justify-center h-[500px]">
              <div className="text-center text-destructive">
                <p className="font-medium">{t('signals.preview.previewError', 'Preview Error')}</p>
                <p className="text-sm mt-2">{previewData.error}</p>
              </div>
            </div>
          ) : previewData?.klines ? (
            <div className="space-y-4">
              {/* Signal Info */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div className="bg-muted p-3 rounded">
                  <div className="text-muted-foreground">{t('signals.preview.symbol', 'Symbol')}</div>
                  <div className="font-medium">{previewData.symbol}</div>
                </div>
                <div className="bg-muted p-3 rounded">
                  <div className="text-muted-foreground">{t('signals.preview.timeWindow', 'Time Window')}</div>
                  <div className="font-medium">{previewData.time_window}</div>
                </div>
                <div className="bg-muted p-3 rounded">
                  <div className="text-muted-foreground">{t('signals.preview.klines', 'K-lines')}</div>
                  <div className="font-medium">{previewData.kline_count}</div>
                </div>
                <div className="bg-muted p-3 rounded">
                  <div className="text-muted-foreground">{t('signals.preview.triggers', 'Triggers')}</div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-yellow-500">{previewData.trigger_count}</span>
                    {previewData.trigger_count > 0 && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-6 text-xs px-2"
                        disabled={regimeLoading}
                        onClick={checkMarketRegime}
                      >
                        {regimeLoading ? t('signals.preview.checking', 'Checking...') : t('signals.preview.checkRegime', 'Check Regime')}
                      </Button>
                    )}
                  </div>
                </div>
              </div>

              {/* Condition Display */}
              <div className="bg-muted p-3 rounded text-sm">
                {previewData.isPoolPreview ? (
                  <div className="space-y-1">
                    <div>
                      <span className="text-muted-foreground">{t('signals.preview.logic', 'Logic')}: </span>
                      <span className={`px-2 py-0.5 rounded ${previewData.logic === 'AND' ? 'bg-blue-500/20 text-blue-400' : 'bg-green-500/20 text-green-400'}`}>
                        {previewData.logic || 'OR'}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">{t('signals.preview.signals', 'Signals')}: </span>
                      <span className="font-mono">
                        {Object.values(previewData.signal_names || {}).join(', ')}
                      </span>
                    </div>
                  </div>
                ) : (
                  <>
                    <span className="text-muted-foreground">{t('signals.preview.condition', 'Condition')}: </span>
                    <span className="font-mono">
                      {previewData.condition?.metric} {previewData.condition?.operator} {previewData.condition?.threshold}
                    </span>
                  </>
                )}
              </div>

              {/* Chart */}
              <div className="border rounded-lg overflow-hidden">
                <SignalPreviewChart
                  klines={previewData.klines}
                  triggers={previewData.triggers || []}
                  timeWindow={chartTimeframe}
                  macd={previewData.macd}
                />
              </div>

              {/* Chart Timeframe Selector */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-muted-foreground">{t('signals.preview.chartTimeframe', 'Chart timeframe:')}</span>
                {['1m', '3m', '5m', '15m', '30m', '1h', '4h'].map(tf => (
                  <Button
                    key={tf}
                    variant={chartTimeframe === tf ? 'default' : 'outline'}
                    size="sm"
                    disabled={previewLoading}
                    onClick={() => refreshPreviewWithTimeframe(tf)}
                  >
                    {tf}
                  </Button>
                ))}
                <span className="text-xs text-muted-foreground ml-2">
                  ({t('signals.preview.signal', 'Signal')}: {previewData.time_window || '5m'})
                </span>
                <span className="text-xs text-yellow-500 ml-2">
                  {t('signals.preview.largerTimeframeNote', 'Note: Larger timeframes require longer calculation time')}
                </span>
              </div>

              {/* Symbol Selector */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-muted-foreground">{t('signals.preview.changeSymbol', 'Change symbol:')}</span>
                {watchlistSymbols.length > 0 ? (
                  watchlistSymbols.map(sym => (
                    <Button
                      key={sym}
                      variant={previewSymbol === sym ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => {
                        if (previewPool) {
                          openPoolPreviewDialog(previewPool, sym)
                        } else if (previewSignal) {
                          openPreviewDialog(previewSignal, sym)
                        }
                      }}
                    >
                      {sym}
                    </Button>
                  ))
                ) : (
                  <span className="text-sm text-muted-foreground italic">No symbols in Watchlist</span>
                )}
                <span className="text-xs text-muted-foreground ml-2">
                  (Manage symbols in Settings page)
                </span>
              </div>
            </div>
          ) : (
            <div className="text-center text-muted-foreground py-8">
              No data available
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* AI Signal Chat Modal */}
      <AiSignalChatModal
        open={aiChatOpen}
        onOpenChange={setAiChatOpen}
        onCreateSignal={handleAiCreateSignal}
        onCreatePool={handleAiCreatePool}
        onPreviewSignal={handleAiPreviewSignal}
        accounts={accounts}
        accountsLoading={accountsLoading}
      />
    </div>
  )
}
