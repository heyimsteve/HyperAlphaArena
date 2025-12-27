import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { AlertTriangle, Sparkles, X, Info, RefreshCw } from 'lucide-react'
import AiAttributionChatModal from './AiAttributionChatModal'
import { TradingAccount, checkPnlSyncStatus, updateArenaPnl } from '@/lib/api'

// Types
interface SummaryMetrics {
  total_pnl: number
  total_fee: number
  net_pnl: number
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number
  avg_win: number | null
  avg_loss: number | null
  profit_factor: number | null
}

interface DataCompleteness {
  total_decisions: number
  with_strategy: number
  with_signal: number
  with_pnl: number
}

interface TriggerBreakdown {
  count: number
  net_pnl: number
}

interface SummaryResponse {
  period: { start: string | null; end: string | null }
  overview: SummaryMetrics
  data_completeness: DataCompleteness
  by_trigger_type: Record<string, TriggerBreakdown>
}

interface DimensionItem {
  metrics: SummaryMetrics
  by_trigger_type?: Record<string, TriggerBreakdown>
  [key: string]: unknown
}

interface DimensionResponse {
  items: DimensionItem[]
  unattributed?: { count: number; metrics: SummaryMetrics | null }
}

interface Account {
  id: number
  name: string
  account_type: string
  model?: string
}

// API functions
const API_BASE = '/api/analytics'

async function fetchSummary(params: URLSearchParams): Promise<SummaryResponse> {
  const res = await fetch(`${API_BASE}/summary?${params}`)
  if (!res.ok) throw new Error('Failed to fetch summary')
  return res.json()
}

async function fetchByDimension(dimension: string, params: URLSearchParams): Promise<DimensionResponse> {
  const res = await fetch(`${API_BASE}/by-${dimension}?${params}`)
  if (!res.ok) throw new Error(`Failed to fetch by-${dimension}`)
  return res.json()
}

async function fetchAccounts(): Promise<Account[]> {
  const res = await fetch('/api/account/list')
  if (!res.ok) throw new Error('Failed to fetch accounts')
  const data = await res.json()
  return data.map((acc: { id: number; name: string; account_type: string; model?: string }) => ({
    id: acc.id,
    name: acc.name,
    account_type: acc.account_type,
    model: acc.model
  }))
}

export default function AttributionAnalysis() {
  const { t } = useTranslation()

  // Filter states
  const [environment, setEnvironment] = useState<string>('mainnet')
  const [accountId, setAccountId] = useState<string>('all')
  const [timeRange, setTimeRange] = useState<string>('all')

  // Data states
  const [accounts, setAccounts] = useState<Account[]>([])
  const [summary, setSummary] = useState<SummaryResponse | null>(null)
  const [bySymbol, setBySymbol] = useState<DimensionResponse | null>(null)
  const [byStrategy, setByStrategy] = useState<DimensionResponse | null>(null)
  const [byTrigger, setByTrigger] = useState<DimensionResponse | null>(null)
  const [byOperation, setByOperation] = useState<DimensionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [showNotice, setShowNotice] = useState(true)
  const [aiChatOpen, setAiChatOpen] = useState(false)

  // PnL sync status
  const [needsSync, setNeedsSync] = useState(false)
  const [unsyncCount, setUnsyncCount] = useState(0)
  const [syncing, setSyncing] = useState(false)

  // Load accounts on mount
  useEffect(() => {
    fetchAccounts().then(setAccounts).catch(console.error)
  }, [])

  // Check PnL sync status when environment changes
  useEffect(() => {
    checkPnlSyncStatus(environment)
      .then(status => {
        setNeedsSync(status.needs_sync)
        setUnsyncCount(status.unsync_count)
      })
      .catch(console.error)
  }, [environment])

  // Handle PnL sync
  const handleSyncPnl = async () => {
    setSyncing(true)
    try {
      await updateArenaPnl()
      // Recheck status and reload data
      const status = await checkPnlSyncStatus(environment)
      setNeedsSync(status.needs_sync)
      setUnsyncCount(status.unsync_count)
      await loadData()
    } catch (error) {
      console.error('Failed to sync PnL:', error)
    } finally {
      setSyncing(false)
    }
  }

  // Load data when filters change
  useEffect(() => {
    loadData()
  }, [environment, accountId, timeRange])

  const buildParams = () => {
    const params = new URLSearchParams()
    params.set('environment', environment)
    if (accountId !== 'all') params.set('account_id', accountId)

    // Calculate date range based on timeRange
    const now = new Date()
    let startDate: Date | null = null

    if (timeRange === 'today') {
      startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    } else if (timeRange === 'week') {
      startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 7)
    } else if (timeRange === 'month') {
      startDate = new Date(now.getFullYear(), now.getMonth() - 1, now.getDate())
    }

    if (startDate) {
      params.set('start_date', startDate.toISOString().split('T')[0])
      params.set('end_date', now.toISOString().split('T')[0])
    }

    return params
  }

  const loadData = async () => {
    setLoading(true)
    try {
      const params = buildParams()
      const [summaryData, symbolData, strategyData, triggerData, operationData] = await Promise.all([
        fetchSummary(params),
        fetchByDimension('symbol', params),
        fetchByDimension('strategy', params),
        fetchByDimension('trigger-type', params),
        fetchByDimension('operation', params),
      ])
      setSummary(summaryData)
      setBySymbol(symbolData)
      setByStrategy(strategyData)
      setByTrigger(triggerData)
      setByOperation(operationData)
    } catch (error) {
      console.error('Failed to load analytics data:', error)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex-1 p-4 space-y-4 overflow-auto">
      {/* Combined Notice (dismissible) */}
      {showNotice && (
        <div className="flex items-start gap-2 p-3 rounded-lg border border-amber-600/60 bg-amber-600/15">
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-500 flex-shrink-0 mt-0.5" />
          <p className="flex-1 text-sm text-amber-700 dark:text-amber-400">
            {t('attribution.notice')}
          </p>
          <button onClick={() => setShowNotice(false)} className="text-amber-600 hover:text-amber-700 dark:text-amber-500 dark:hover:text-amber-400 p-0.5">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* PnL Sync Warning */}
      {needsSync && (
        <div className="flex items-center gap-3 p-3 rounded-lg border border-orange-500/60 bg-orange-500/15">
          <RefreshCw className="h-4 w-4 text-orange-600 dark:text-orange-400 flex-shrink-0" />
          <p className="flex-1 text-sm text-orange-700 dark:text-orange-300">
            {t('attribution.syncWarning', { count: unsyncCount })}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={handleSyncPnl}
            disabled={syncing}
            className="border-orange-500/60 text-orange-700 hover:bg-orange-500/20 dark:text-orange-300"
          >
            {syncing ? (
              <>
                <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
                {t('attribution.syncing', 'Syncing...')}
              </>
            ) : (
              t('attribution.syncPnl', 'Sync PnL Data')
            )}
          </Button>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-4 items-center justify-between">
        <div className="flex flex-wrap gap-4 items-center">
          <Select value={timeRange} onValueChange={setTimeRange}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="today">{t('attribution.today', 'Today')}</SelectItem>
              <SelectItem value="week">{t('attribution.thisWeek', 'This Week')}</SelectItem>
              <SelectItem value="month">{t('attribution.thisMonth', 'This Month')}</SelectItem>
              <SelectItem value="all">{t('attribution.allTime', 'All Time')}</SelectItem>
            </SelectContent>
          </Select>

          <Select value={environment} onValueChange={setEnvironment}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="testnet">Testnet</SelectItem>
              <SelectItem value="mainnet">Mainnet</SelectItem>
            </SelectContent>
          </Select>

          <Select value={accountId} onValueChange={setAccountId}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder={t('attribution.allAccounts', 'All Accounts')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('attribution.allAccounts', 'All Accounts')}</SelectItem>
              {accounts.map(acc => (
                <SelectItem key={acc.id} value={String(acc.id)}>{acc.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button
          size="sm"
          className="bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white border-0 shadow-lg hover:shadow-xl transition-all"
          onClick={() => setAiChatOpen(true)}
        >
          <Sparkles className="w-4 h-4 mr-2" />{t('attribution.aiAnalysisBtn', 'AI Attribution')}
        </Button>
      </div>

      {/* Summary Cards - placeholder */}
      {loading ? (
        <div className="text-center py-8 text-muted-foreground">Loading...</div>
      ) : (
        <>
          {/* Summary metrics will be added here */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Card><CardHeader className="pb-2"><CardTitle className="text-sm">{t('attribution.netPnl', 'Net PnL')}</CardTitle></CardHeader><CardContent><div className={`text-2xl font-bold ${(summary?.overview.net_pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>${summary?.overview.net_pnl?.toFixed(2) || '0.00'}</div></CardContent></Card>
            <Card><CardHeader className="pb-2"><CardTitle className="text-sm">{t('attribution.tradeCount', 'Trades')}</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold">{summary?.overview.trade_count || 0}</div></CardContent></Card>
            <Card><CardHeader className="pb-2"><CardTitle className="text-sm">{t('attribution.aiWinRate', 'AI Win Rate')}</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold">{((summary?.overview.win_rate || 0) * 100).toFixed(1)}%</div></CardContent></Card>
            <Card><CardHeader className="pb-2"><CardTitle className="text-sm">{t('attribution.profitFactor', 'Profit Factor')}</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold">{summary?.overview.profit_factor?.toFixed(2) || 'N/A'}</div></CardContent></Card>
          </div>

          {/* Dimension analysis will be added here */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* By Symbol */}
            <Card>
              <CardHeader><CardTitle>{t('attribution.bySymbol', 'By Symbol')}</CardTitle></CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {bySymbol?.items.map((item: DimensionItem & { symbol?: string }) => (
                    <div key={item.symbol} className="flex justify-between items-center py-1 border-b last:border-0">
                      <span className="font-medium">{item.symbol}</span>
                      <span className={item.metrics.net_pnl >= 0 ? 'text-green-500' : 'text-red-500'}>
                        ${item.metrics.net_pnl.toFixed(2)} ({item.metrics.trade_count})
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* By Strategy */}
            <Card>
              <CardHeader><CardTitle>{t('attribution.byStrategy', 'By Strategy')}</CardTitle></CardHeader>
              <CardContent>
                {byStrategy?.items.length === 0 ? (
                  <div className="text-muted-foreground text-sm">{t('attribution.noStrategyData', 'No strategy attribution data')}</div>
                ) : (
                  <div className="space-y-2">
                    {byStrategy?.items.map((item: DimensionItem & { strategy_id?: number; strategy_name?: string }) => (
                      <div key={item.strategy_id} className="flex justify-between items-center py-1 border-b last:border-0">
                        <span className="font-medium">{item.strategy_name}</span>
                        <span className={item.metrics.net_pnl >= 0 ? 'text-green-500' : 'text-red-500'}>
                          ${item.metrics.net_pnl.toFixed(2)} ({item.metrics.trade_count})
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {byStrategy?.unattributed && byStrategy.unattributed.count > 0 && (
                  <div className="mt-2 pt-2 border-t text-sm text-muted-foreground">
                    {t('attribution.unattributed', 'Unattributed')}: {byStrategy.unattributed.count} trades
                  </div>
                )}
              </CardContent>
            </Card>

            {/* By Trigger Type */}
            <Card>
              <CardHeader><CardTitle>{t('attribution.byTriggerType', 'By Trigger Type')}</CardTitle></CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {byTrigger?.items.map((item: DimensionItem & { trigger_type?: string }) => (
                    <div key={item.trigger_type} className="flex justify-between items-center py-1 border-b last:border-0">
                      <span className="font-medium capitalize">{item.trigger_type}</span>
                      <span className={item.metrics.net_pnl >= 0 ? 'text-green-500' : 'text-red-500'}>
                        ${item.metrics.net_pnl.toFixed(2)} ({item.metrics.trade_count})
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* By Operation */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  {t('attribution.byOperation', 'By Operation')}
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-4 h-4 text-muted-foreground hover:text-foreground cursor-help" />
                      </TooltipTrigger>
                      <TooltipContent side="bottom" className="max-w-xs p-3">
                        <p className="text-sm">{t('attribution.operationTooltip')}</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {byOperation?.items.map((item: DimensionItem & { operation?: string }) => (
                    <div key={item.operation} className="flex justify-between items-center py-1 border-b last:border-0">
                      <span className="font-medium uppercase">{item.operation}</span>
                      <span className={item.metrics.net_pnl >= 0 ? 'text-green-500' : 'text-red-500'}>
                        ${item.metrics.net_pnl.toFixed(2)} ({item.metrics.trade_count})
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>
        </>
      )}

      {/* AI Attribution Chat Modal */}
      <AiAttributionChatModal
        open={aiChatOpen}
        onOpenChange={setAiChatOpen}
        accounts={accounts as unknown as TradingAccount[]}
        accountsLoading={false}
      />
    </div>
  )
}
