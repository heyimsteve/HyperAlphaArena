import { useEffect, useState, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CoinIcon } from '@/components/ui/coin-icon'
import { TrendingUp, AlertTriangle, Eye, Zap } from 'lucide-react'
import { getHyperliquidBalance, getWalletRateLimit, getTradingStats, getBinanceTradingStats, TradingStats, getBinanceSummary, getBinanceDailyQuota } from '@/lib/hyperliquidApi'
import { getModelLogo } from './logoAssets'
import type { HyperliquidEnvironment } from '@/lib/types/hyperliquid'
import type { HyperliquidBalance } from '@/lib/types/hyperliquid'
import { useTradingMode } from '@/contexts/TradingModeContext'
import { formatDateTime } from '@/lib/dateTime'
import {
  getCachedData,
  setCachedData,
  getApiUsageCacheKey,
  getTradingStatsCacheKey,
  getCacheTimestamp,
} from '@/lib/cacheUtils'
import TraderDetailModal from './TraderDetailModal'
import QuotaUpgradeModal from '@/components/binance/QuotaUpgradeModal'

// Position type from parent component
export interface Position {
  symbol: string
  side: string
  size: number
  entry_price: number
  mark_price: number
  unrealized_pnl: number
  leverage: number
  account_id: number
  exchange?: string  // 'hyperliquid' | 'binance'
}

interface RateLimitData {
  cumVlm: number
  nRequestsUsed: number
  nRequestsCap: number
  remaining: number
  usagePercent: number
  isOverLimit: boolean
}

interface AccountBalance {
  accountId: number
  accountName: string
  exchange: string
  balance: HyperliquidBalance | null
  error: string | null
  loading: boolean
  rateLimit: RateLimitData | null
  rateLimitUpdated: number | null
  tradingStats: TradingStats | null
  tradingStatsUpdated: number | null
  quota?: {
    limited: boolean
    used: number
    limit: number
    remaining: number
    reset_at?: number
  } | null
}

interface HyperliquidMultiAccountSummaryProps {
  accounts: Array<{ account_id: number; account_name: string; exchange?: string }>
  refreshKey?: number
  selectedAccount?: number | 'all'
  positions?: Position[]
}

const getMarginStatus = (percent: number, t: (key: string, fallback?: string) => string) => {
  if (percent < 50) {
    return {
      color: 'bg-green-500',
      text: t('account.marginHealthy', 'Healthy'),
      icon: TrendingUp,
      textColor: 'text-green-600',
      dotColor: 'bg-green-500',
    } as const
  }
  if (percent < 75) {
    return {
      color: 'bg-yellow-500',
      text: t('account.marginModerate', 'Moderate'),
      icon: AlertTriangle,
      textColor: 'text-yellow-600',
      dotColor: 'bg-yellow-500',
    } as const
  }
  return {
    color: 'bg-red-500',
    text: t('account.marginHighRisk', 'High Risk'),
    icon: AlertTriangle,
    textColor: 'text-red-600',
    dotColor: 'bg-red-500',
  } as const
}

export default function HyperliquidMultiAccountSummary({
  accounts,
  refreshKey,
  selectedAccount = 'all',
  positions = [],
}: HyperliquidMultiAccountSummaryProps) {
  const { t } = useTranslation()
  const { tradingMode } = useTradingMode()
  const [accountBalances, setAccountBalances] = useState<AccountBalance[]>([])
  const [globalLastUpdate, setGlobalLastUpdate] = useState<string | null>(null)
  const [selectedTraderForModal, setSelectedTraderForModal] = useState<AccountBalance | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isQuotaModalOpen, setIsQuotaModalOpen] = useState(false)
  const [selectedQuotaAccount, setSelectedQuotaAccount] = useState<AccountBalance | null>(null)

  // Filter accounts based on selectedAccount - memoized to prevent infinite loops
  const filteredAccounts = useMemo(() => {
    return selectedAccount === 'all'
      ? accounts
      : accounts.filter(acc => acc.account_id === selectedAccount)
  }, [accounts, selectedAccount])

  // Get environment string
  const environment: HyperliquidEnvironment =
    tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : 'testnet'

  // Load balances first (fast), then async load API Usage and Trading Stats
  const loadAllBalances = useCallback(async () => {
    // Step 1: Load balances quickly with cached data
    const balanceResults = await Promise.allSettled(
      filteredAccounts.map(async (acc) => {
        const exchange = acc.exchange || 'hyperliquid'
        try {
          let balance: HyperliquidBalance | null = null
          let rateLimit: RateLimitData | null = null

          if (exchange === 'binance') {
            // Binance: use summary endpoint for balance + rate limit
            const summary = await getBinanceSummary(acc.account_id)
            balance = {
              totalEquity: summary.equity,
              availableBalance: summary.available_balance,
              usedMargin: summary.used_margin,
              marginUsagePercent: summary.margin_usage,
              unrealizedPnl: summary.unrealized_pnl,
              lastUpdated: summary.last_updated
                ? (typeof summary.last_updated === 'number'
                    ? new Date(summary.last_updated).toISOString()
                    : summary.last_updated)
                : new Date().toISOString(),
            } as HyperliquidBalance
            if (summary.rate_limit) {
              rateLimit = {
                cumVlm: 0,
                nRequestsUsed: summary.rate_limit.used_weight,
                nRequestsCap: summary.rate_limit.weight_cap,
                remaining: summary.rate_limit.remaining,
                usagePercent: summary.rate_limit.usage_percent,
                isOverLimit: summary.rate_limit.usage_percent >= 100,
              }
            }
          } else {
            balance = await getHyperliquidBalance(acc.account_id)
          }

          // Fetch daily quota for Binance mainnet accounts
          let quota = null
          if (exchange === 'binance' && environment === 'mainnet') {
            try {
              const quotaData = await getBinanceDailyQuota(acc.account_id)
              if (quotaData.limited) {
                quota = quotaData
              }
            } catch (e) { /* ignore quota fetch errors */ }
          }

          const apiUsageCacheKey = getApiUsageCacheKey(acc.account_id, environment, exchange)
          const statsCacheKey = getTradingStatsCacheKey(acc.account_id, environment, exchange)
          return {
            accountId: acc.account_id,
            accountName: acc.account_name,
            exchange,
            balance,
            error: null,
            loading: false,
            rateLimit: rateLimit || getCachedData<RateLimitData>(apiUsageCacheKey),
            rateLimitUpdated: rateLimit ? Date.now() : getCacheTimestamp(apiUsageCacheKey),
            tradingStats: getCachedData<TradingStats>(statsCacheKey),
            tradingStatsUpdated: getCacheTimestamp(statsCacheKey),
            quota,
          }
        } catch (error: any) {
          return {
            accountId: acc.account_id,
            accountName: acc.account_name,
            exchange,
            balance: null,
            error: error.message || 'Failed to load',
            loading: false,
            rateLimit: null,
            rateLimitUpdated: null,
            tradingStats: null,
            tradingStatsUpdated: null,
          }
        }
      })
    )

    const initialBalances: AccountBalance[] = balanceResults.map((result, index) => {
      if (result.status === 'fulfilled') return result.value
      return {
        accountId: filteredAccounts[index].account_id,
        accountName: filteredAccounts[index].account_name,
        exchange: filteredAccounts[index].exchange || 'hyperliquid',
        balance: null,
        error: 'Failed to load',
        loading: false,
        rateLimit: null,
        rateLimitUpdated: null,
        tradingStats: null,
        tradingStatsUpdated: null,
      }
    })

    setAccountBalances(initialBalances)

    // Update timestamp
    const latestUpdate = initialBalances
      .map((acc) => acc.balance?.lastUpdated)
      .filter((ts): ts is string => ts !== undefined)
      .sort()
      .reverse()[0]
    if (latestUpdate) setGlobalLastUpdate(formatDateTime(latestUpdate))

    // Step 2: Async load API Usage and Trading Stats for Hyperliquid accounts missing cache
    filteredAccounts.forEach(async (acc) => {
      // Skip Binance accounts - their rate limit is already loaded in Step 1
      if ((acc.exchange || 'hyperliquid') === 'binance') return

      const accExchange = acc.exchange || 'hyperliquid'
      const apiUsageCacheKey = getApiUsageCacheKey(acc.account_id, environment, accExchange)
      const statsCacheKey = getTradingStatsCacheKey(acc.account_id, environment, accExchange)
      let needsUpdate = false
      let newRateLimit = getCachedData<RateLimitData>(apiUsageCacheKey)
      let newRateLimitUpdated = getCacheTimestamp(apiUsageCacheKey)
      let newTradingStats = getCachedData<TradingStats>(statsCacheKey)
      let newTradingStatsUpdated = getCacheTimestamp(statsCacheKey)

      // Fetch API Usage if not cached
      if (!newRateLimit) {
        try {
          const res = await getWalletRateLimit(acc.account_id, environment)
          if (res.success && res.rateLimit) {
            newRateLimit = res.rateLimit
            setCachedData(apiUsageCacheKey, newRateLimit)
            newRateLimitUpdated = Date.now()
            needsUpdate = true
          }
        } catch (e) { /* ignore */ }
      }

      // Fetch Trading Stats if not cached
      if (!newTradingStats) {
        try {
          const res = accExchange === 'binance'
            ? await getBinanceTradingStats(acc.account_id, environment)
            : await getTradingStats(acc.account_id, environment)
          if (res.success && res.stats) {
            newTradingStats = res.stats
            setCachedData(statsCacheKey, newTradingStats)
            newTradingStatsUpdated = Date.now()
            needsUpdate = true
          }
        } catch (e) { /* ignore */ }
      }

      // Update state if new data fetched
      if (needsUpdate) {
        setAccountBalances(prev => prev.map(a =>
          (a.accountId === acc.account_id && a.exchange === (acc.exchange || 'hyperliquid'))
            ? { ...a, rateLimit: newRateLimit, rateLimitUpdated: newRateLimitUpdated, tradingStats: newTradingStats, tradingStatsUpdated: newTradingStatsUpdated }
            : a
        ))
      }
    })
  }, [filteredAccounts, environment])

  useEffect(() => {
    if (filteredAccounts.length === 0) {
      setAccountBalances([])
      return
    }

    // Only initialize with loading state on first load (when accountBalances is empty)
    const isFirstLoad = accountBalances.length === 0
    if (isFirstLoad) {
      setAccountBalances(
        filteredAccounts.map((acc) => ({
          accountId: acc.account_id,
          accountName: acc.account_name,
          exchange: acc.exchange || 'hyperliquid',
          balance: null,
          error: null,
          loading: true,
          rateLimit: null,
          rateLimitUpdated: null,
          tradingStats: null,
          tradingStatsUpdated: null,
        }))
      )
    }

    loadAllBalances()
  }, [filteredAccounts, tradingMode, refreshKey])

  // Get positions for a specific account and exchange
  const getAccountPositions = (accountId: number, exchange: string) => {
    return positions.filter(p => p.account_id === accountId && (p.exchange || 'hyperliquid') === exchange)
  }

  // Handle opening modal
  const handleViewDetails = (account: AccountBalance) => {
    setSelectedTraderForModal(account)
    setIsModalOpen(true)
  }

  if (tradingMode !== 'testnet' && tradingMode !== 'mainnet') {
    return null
  }

  if (filteredAccounts.length === 0) {
    return (
      <Card className="p-6">
        <div className="text-sm text-muted-foreground">
          {t('account.noAccountsConfigured', 'No Hyperliquid accounts configured')}
        </div>
      </Card>
    )
  }

  const isLoading = accountBalances.some((acc) => acc.loading)

  // Helper to get API usage color
  const getApiUsageColor = (usagePercent: number) => {
    if (usagePercent >= 90) return 'text-red-600'
    if (usagePercent >= 70) return 'text-yellow-600'
    return 'text-green-600'
  }

  // Use horizontal scroll layout when 4+ accounts to prevent card cramping
  const accountCount = accountBalances.length
  const useScrollLayout = accountCount >= 4

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{t('account.accountStatus', 'Account Status')}</h2>
        <Badge
          variant={environment === 'testnet' ? 'default' : 'destructive'}
          className="uppercase text-xs"
        >
          {environment}
        </Badge>
      </div>

      {globalLastUpdate && (
        <div className="text-xs text-muted-foreground -mt-2">
          {t('common.lastUpdate', 'Last update')}: {globalLastUpdate}
        </div>
      )}

      {/* Loading state - only show when no data yet */}
      {isLoading && accountBalances.every(a => !a.balance) && (
        <div className="text-sm text-muted-foreground">{t('account.loadingData', 'Loading account data...')}</div>
      )}

      {/* Account cards - scroll horizontally when 4+ accounts */}
      <div className={useScrollLayout
        ? 'flex gap-4 overflow-x-auto pb-2 snap-x snap-mandatory'
        : `grid gap-4 ${accountCount === 1 ? 'grid-cols-1' : accountCount === 2 ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3'}`
      }>
        {accountBalances.map((account) => {
          const logo = getModelLogo(account.accountName)
          const marginStatus = account.balance
            ? getMarginStatus(account.balance.marginUsagePercent, t)
            : null
          const accountPositions = getAccountPositions(account.accountId, account.exchange)
          const isBinance = account.exchange === 'binance'
          const exchangeLogo = isBinance ? '/static/binance_logo.svg' : '/static/hyperliquid_logo.svg'

          return (
            <Card
              key={`${account.accountId}_${account.exchange}`}
              className={`p-4 space-y-3 hover:shadow-md transition-shadow ${useScrollLayout ? 'min-w-[400px] flex-shrink-0 snap-start' : ''}`}
            >
              {/* Account header with logo and View Details button */}
              <div className="flex items-center justify-between pb-2 border-b border-border">
                <div className="flex items-center gap-2">
                  {logo && (
                    <img
                      src={logo.src}
                      alt={logo.alt}
                      className="h-6 w-6 rounded-full object-contain"
                    />
                  )}
                  <span className="font-semibold text-sm truncate">
                    {account.accountName}
                  </span>
                  <div className="flex items-center gap-1.5 px-1.5 py-0.5 rounded bg-slate-800/80">
                    <img
                      src={exchangeLogo}
                      alt={isBinance ? 'Binance' : 'Hyperliquid'}
                      className="h-3.5 w-3.5"
                    />
                    <span className="text-[10px] font-medium text-slate-200">
                      {isBinance ? 'Binance' : 'Hyperliquid'}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {/* Quota upgrade button for Binance mainnet limited accounts */}
                  {account.quota && account.quota.limited && (
                    <button
                      onClick={() => {
                        setSelectedQuotaAccount(account)
                        setIsQuotaModalOpen(true)
                      }}
                      className="px-2 py-1 bg-amber-100 dark:bg-amber-900/30 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-amber-800 dark:text-amber-200 rounded text-[10px] font-medium transition-colors"
                      title={account.quota.reset_at ? `Resets at ${new Date(account.quota.reset_at * 1000).toLocaleString()}` : undefined}
                    >
                      {account.quota.remaining}/{account.quota.limit}
                      {account.quota.reset_at && (
                        <span className="ml-1 opacity-75">
                          · Reset at {new Date(account.quota.reset_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                      )}
                      <span className="ml-1">· Upgrade</span>
                    </button>
                  )}
                  {account.balance && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-[10px] h-6 px-2"
                      onClick={() => handleViewDetails(account)}
                    >
                      <Eye className="w-3 h-3 mr-1" />
                      {t('common.details', 'Details')}
                    </Button>
                  )}
                </div>
              </div>

              {/* Error state */}
              {account.error && (
                <div className="text-xs text-red-600">{account.error}</div>
              )}

              {/* Main metrics grid */}
              {account.balance && (
                <div className="grid grid-cols-2 gap-3">
                  {/* Equity */}
                  <div>
                    <div className="text-[10px] text-muted-foreground">{t('account.equity', 'Equity')}</div>
                    <div className="text-sm font-bold">
                      ${account.balance.totalEquity.toLocaleString('en-US', {
                        minimumFractionDigits: 0,
                        maximumFractionDigits: 0,
                      })}
                    </div>
                  </div>

                  {/* Margin */}
                  <div>
                    <div className="text-[10px] text-muted-foreground">{t('account.margin', 'Margin')}</div>
                    <div className={`text-sm font-medium ${marginStatus?.textColor || ''}`}>
                      {account.balance.marginUsagePercent.toFixed(1)}%
                    </div>
                  </div>

                  {/* API Usage */}
                  <div>
                    <div className="text-[10px] text-muted-foreground">
                      {isBinance ? t('account.apiWeight', 'Weight/min') : 'API'}
                    </div>
                    {account.rateLimit ? (
                      isBinance ? (
                        <div className={`text-sm font-medium ${getApiUsageColor(account.rateLimit.usagePercent)}`}>
                          {account.rateLimit.nRequestsUsed}/{account.rateLimit.nRequestsCap}
                        </div>
                      ) : (
                        <div className={`text-sm font-medium ${getApiUsageColor(account.rateLimit.usagePercent)}`}>
                          {(100 - account.rateLimit.usagePercent).toFixed(0)}%
                          <span className="text-[10px] text-muted-foreground ml-1">{t('account.left', 'left')}</span>
                        </div>
                      )
                    ) : (
                      <div className="text-sm text-muted-foreground">--</div>
                    )}
                  </div>

                  {/* Win Rate */}
                  <div>
                    <div className="text-[10px] text-muted-foreground">{t('account.winRate', 'Win Rate')}</div>
                    {isBinance ? (
                      <div className="text-sm text-muted-foreground">N/A</div>
                    ) : account.tradingStats && account.tradingStats.total_trades > 0 ? (
                      <div className="text-sm font-medium">
                        {account.tradingStats.win_rate.toFixed(0)}%
                        <span className="text-[10px] text-muted-foreground ml-1">
                          ({account.tradingStats.wins}W/{account.tradingStats.losses}L)
                        </span>
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">--</div>
                    )}
                  </div>
                </div>
              )}

              {/* Positions section - always show */}
              <div className="pt-2 border-t border-border">
                <div className="text-[10px] text-muted-foreground mb-1">
                  {t('account.positions', 'Positions')} {accountPositions.length > 0 && `(${accountPositions.length})`}
                </div>
                {accountPositions.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {accountPositions.slice(0, 4).map((pos, idx) => {
                      const isLong = pos.side.toLowerCase() === 'long'
                      const pnlColor = pos.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'
                      return (
                        <div
                          key={idx}
                          className={`text-[10px] px-1.5 py-1 rounded border ${
                            isLong
                              ? 'bg-green-500/10 border-green-500/20'
                              : 'bg-red-500/10 border-red-500/20'
                          }`}
                        >
                          <div className="flex items-center gap-1">
                            <CoinIcon symbol={pos.symbol} size={14} />
                            <span className={`font-medium ${isLong ? 'text-green-600' : 'text-red-600'}`}>
                              {pos.symbol} {isLong ? 'L' : 'S'}
                            </span>
                            <span className="text-muted-foreground">{pos.leverage}x</span>
                          </div>
                          <div className="flex items-center gap-1 mt-0.5">
                            <span className="text-muted-foreground">{pos.size.toFixed(4)}</span>
                            <span className={`font-medium ${pnlColor}`}>
                              {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                            </span>
                          </div>
                        </div>
                      )
                    })}
                    {accountPositions.length > 4 && (
                      <div className="text-[10px] text-muted-foreground self-center">
                        +{accountPositions.length - 4} {t('common.more', 'more')}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-[10px] text-muted-foreground">{t('account.noOpenPositions', 'No open positions')}</div>
                )}
              </div>
            </Card>
          )
        })}
      </div>

      {/* Trader Detail Modal */}
      {selectedTraderForModal && (
        <TraderDetailModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          account={selectedTraderForModal}
          positions={getAccountPositions(selectedTraderForModal.accountId, selectedTraderForModal.exchange)}
          environment={environment}
        />
      )}

      {/* Quota Upgrade Modal */}
      <QuotaUpgradeModal
        isOpen={isQuotaModalOpen}
        onClose={() => setIsQuotaModalOpen(false)}
        quota={selectedQuotaAccount?.quota || undefined}
      />
    </div>
  )
}