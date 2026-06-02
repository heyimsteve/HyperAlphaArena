import React, { useCallback, useEffect, useRef, useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  getHyperliquidAvailableSymbols,
  getHyperliquidWatchlist,
  updateHyperliquidWatchlist,
  getBinanceAvailableSymbols,
  getBinanceWatchlist,
  updateBinanceWatchlist,
  getNewsSources,
  updateNewsSources,
  testNewsSource,
  getNewsStats,
} from '@/lib/api'
import type {
  HyperliquidSymbolMeta,
  BinanceSymbolMeta,
  NewsSourceConfig,
  NewsStatsResponse,
  TestNewsSourceResponse,
} from '@/lib/api'
import DataCoverageHeatmap from './DataCoverageHeatmap'
import ExchangeIcon from '@/components/exchange/ExchangeIcon'
import { CoinIcon } from '@/components/ui/coin-icon'

interface StorageStats {
  exchange: string
  total_size_mb: number
  tables: Record<string, number>
  retention_days: number
  symbol_count: number
  estimated_per_symbol_per_day_mb: number
}

export default function SettingsPage() {
  const { t, i18n } = useTranslation()
  const [activeTab, setActiveTab] = useState('watchlist')

  // Language state
  const currentLang = i18n.language === 'zh' ? 'zh' : 'en'

  // Hyperliquid Watchlist state
  const [hlAvailableSymbols, setHlAvailableSymbols] = useState<HyperliquidSymbolMeta[]>([])
  const [hlWatchlistSymbols, setHlWatchlistSymbols] = useState<string[]>([])
  const [hlMaxSymbols, setHlMaxSymbols] = useState(10)
  const [hlLoading, setHlLoading] = useState(true)
  const [hlSaving, setHlSaving] = useState(false)
  const [hlError, setHlError] = useState<string | null>(null)
  const [hlSuccess, setHlSuccess] = useState<string | null>(null)
  const [hlSearchQuery, setHlSearchQuery] = useState('')

  // Binance Watchlist state
  const [bnAvailableSymbols, setBnAvailableSymbols] = useState<BinanceSymbolMeta[]>([])
  const [bnWatchlistSymbols, setBnWatchlistSymbols] = useState<string[]>([])
  const [bnMaxSymbols, setBnMaxSymbols] = useState(10)
  const [bnLoading, setBnLoading] = useState(true)
  const [bnSaving, setBnSaving] = useState(false)
  const [bnError, setBnError] = useState<string | null>(null)
  const [bnSuccess, setBnSuccess] = useState<string | null>(null)
  const [bnSearchQuery, setBnSearchQuery] = useState('')

  // Legacy aliases for compatibility
  const availableSymbols = hlAvailableSymbols
  const watchlistSymbols = hlWatchlistSymbols
  const maxWatchlistSymbols = hlMaxSymbols
  const watchlistLoading = hlLoading
  const watchlistSaving = hlSaving
  const watchlistError = hlError
  const watchlistSuccess = hlSuccess

  // Storage stats state - per exchange
  const [storageStats, setStorageStats] = useState<Record<string, StorageStats>>({})
  const [storageLoading, setStorageLoading] = useState(false)
  const [retentionDays, setRetentionDays] = useState<Record<string, string>>({
    hyperliquid: '365',
    binance: '365',
  })
  const [retentionSaving, setRetentionSaving] = useState(false)
  const [retentionError, setRetentionError] = useState<string | null>(null)
  const [retentionSuccess, setRetentionSuccess] = useState<string | null>(null)

  // Backfill state - per exchange
  const [backfillStatus, setBackfillStatus] = useState<Record<string, {
    status: string
    progress: number
    task_id?: number
    symbols?: string[]
    error_message?: string
  }>>({})
  const [backfillStarting, setBackfillStarting] = useState<Record<string, boolean>>({})
  // Track if we just completed a backfill (for one-time success message)
  const [backfillJustCompleted, setBackfillJustCompleted] = useState<Record<string, boolean>>({})

  // News sources state
  const [newsSources, setNewsSources] = useState<NewsSourceConfig[]>([])
  const [newsSourcesSnapshot, setNewsSourcesSnapshot] = useState('[]')
  const [newsStats, setNewsStats] = useState<NewsStatsResponse | null>(null)
  const [newsLoading, setNewsLoading] = useState(false)
  const [newsSaving, setNewsSaving] = useState(false)
  const [newsError, setNewsError] = useState<string | null>(null)
  const [newsSuccess, setNewsSuccess] = useState<string | null>(null)
  const [newsFormAdapter, setNewsFormAdapter] = useState<'rss_generic' | 'cryptopanic' | 'finnhub_calendar'>('rss_generic')
  const [newsTestUrl, setNewsTestUrl] = useState('')
  const [newsFormInterval, setNewsFormInterval] = useState('300')
  const [newsFormAuthToken, setNewsFormAuthToken] = useState('')
  const [newsFormApiKey, setNewsFormApiKey] = useState('')
  const [newsTesting, setNewsTesting] = useState(false)
  const [newsTestError, setNewsTestError] = useState<string | null>(null)
  const [newsTestResult, setNewsTestResult] = useState<TestNewsSourceResponse | null>(null)

  // Determine current exchange from active tab
  const currentExchange = activeTab === 'hyperliquid-data' ? 'hyperliquid' : activeTab === 'binance-data' ? 'binance' : null

  const toggleLanguage = (lang: 'en' | 'zh') => {
    i18n.changeLanguage(lang)
    // Sync language to backend for Bot integration
    fetch('/api/config/ui_language', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: lang }),
    }).catch(() => {})
  }

  const fetchWatchlist = useCallback(async () => {
    setHlLoading(true)
    setBnLoading(true)
    setHlError(null)
    setBnError(null)
    try {
      // Fetch both Hyperliquid and Binance data in parallel
      const [hlAvailable, hlWatchlist, bnAvailable, bnWatchlist] = await Promise.all([
        getHyperliquidAvailableSymbols(),
        getHyperliquidWatchlist(),
        getBinanceAvailableSymbols(),
        getBinanceWatchlist(),
      ])
      setHlAvailableSymbols(hlAvailable.symbols || [])
      setHlMaxSymbols(hlWatchlist.max_symbols ?? 10)
      setHlWatchlistSymbols(hlWatchlist.symbols || [])
      setBnAvailableSymbols(bnAvailable.symbols || [])
      setBnMaxSymbols(bnWatchlist.max_symbols ?? 10)
      setBnWatchlistSymbols(bnWatchlist.symbols || [])
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Failed to load watchlist'
      setHlError(errorMsg)
      setBnError(errorMsg)
    } finally {
      setHlLoading(false)
      setBnLoading(false)
    }
  }, [])

  const fetchStorageStats = useCallback(async (exchange: string) => {
    setStorageLoading(true)
    try {
      const res = await fetch(`/api/system/storage-stats?exchange=${exchange}`)
      if (res.ok) {
        const data: StorageStats = await res.json()
        setStorageStats((prev) => ({ ...prev, [exchange]: data }))
        setRetentionDays((prev) => ({ ...prev, [exchange]: data.retention_days.toString() }))
      }
    } catch (err) {
      console.error('Failed to fetch storage stats:', err)
    } finally {
      setStorageLoading(false)
    }
  }, [])

  const fetchNewsSourcesData = useCallback(async () => {
    setNewsLoading(true)
    setNewsError(null)
    try {
      const [sourcesRes, statsRes] = await Promise.all([
        getNewsSources(),
        getNewsStats(),
      ])
      const nextSources = sourcesRes.sources || []
      setNewsSources(nextSources)
      setNewsSourcesSnapshot(JSON.stringify(nextSources))
      setNewsStats(statsRes)
    } catch (err) {
      setNewsError(err instanceof Error ? err.message : 'Failed to load news sources')
    } finally {
      setNewsLoading(false)
    }
  }, [])

  // Load watchlist on mount
  useEffect(() => {
    fetchWatchlist()
  }, [fetchWatchlist])

  // Load storage stats when exchange data tab is active
  useEffect(() => {
    if (currentExchange && !storageStats[currentExchange]) {
      fetchStorageStats(currentExchange)
    }
  }, [currentExchange, storageStats, fetchStorageStats])

  useEffect(() => {
    if (activeTab === 'news-sources' && !newsLoading && !newsStats && newsSources.length === 0) {
      fetchNewsSourcesData()
    }
  }, [activeTab, newsLoading, newsStats, newsSources.length, fetchNewsSourcesData])

  // Fetch backfill status for an exchange
  const fetchBackfillStatus = useCallback(async (exchange: string) => {
    try {
      const res = await fetch(`/api/system/${exchange}/backfill/status`)
      if (res.ok) {
        const data = await res.json()
        setBackfillStatus(prev => {
          const prevStatus = prev[exchange]?.status
          // Track completion for one-time message
          if ((prevStatus === 'running' || prevStatus === 'pending') && data.status === 'completed') {
            setBackfillJustCompleted(p => ({ ...p, [exchange]: true }))
          }
          return { ...prev, [exchange]: data }
        })
      }
    } catch (err) {
      console.error(`Failed to fetch ${exchange} backfill status:`, err)
    }
  }, [])

  // Use ref to track if polling should continue
  const pollingRef = useRef<Record<string, boolean>>({})

  useEffect(() => {
    if (currentExchange) {
      // Initial fetch
      fetchBackfillStatus(currentExchange)
      pollingRef.current[currentExchange] = true

      // Poll while running - use functional update to get latest status
      const interval = setInterval(async () => {
        const res = await fetch(`/api/system/${currentExchange}/backfill/status`)
        if (res.ok) {
          const data = await res.json()
          setBackfillStatus(prev => {
            const prevStatus = prev[currentExchange]?.status
            // Track completion for one-time message
            if ((prevStatus === 'running' || prevStatus === 'pending') && data.status === 'completed') {
              setBackfillJustCompleted(p => ({ ...p, [currentExchange]: true }))
            }
            return { ...prev, [currentExchange]: data }
          })
          // Stop polling if completed or failed
          if (data.status !== 'running' && data.status !== 'pending') {
            pollingRef.current[currentExchange] = false
          }
        }
      }, 2000)

      return () => {
        clearInterval(interval)
        pollingRef.current[currentExchange] = false
      }
    }
  }, [activeTab, currentExchange, fetchBackfillStatus])

  const handleStartBackfill = async (exchange: string, force: boolean = false) => {
    setBackfillStarting(prev => ({ ...prev, [exchange]: true }))
    setBackfillJustCompleted(prev => ({ ...prev, [exchange]: false }))
    try {
      const url = force
        ? `/api/system/${exchange}/backfill?force=true`
        : `/api/system/${exchange}/backfill`
      const res = await fetch(url, { method: 'POST' })
      if (res.ok) {
        await fetchBackfillStatus(exchange)
      } else {
        const data = await res.json()
        alert(data.detail || 'Failed to start backfill')
      }
    } catch (err) {
      console.error('Failed to start backfill:', err)
    } finally {
      setBackfillStarting(prev => ({ ...prev, [exchange]: false }))
    }
  }

  const toggleWatchlistSymbol = (symbol: string) => {
    const symbolUpper = symbol.toUpperCase()
    setHlError(null)
    setHlSuccess(null)
    setHlWatchlistSymbols((prev) => {
      if (prev.includes(symbolUpper)) {
        return prev.filter((s) => s !== symbolUpper)
      }
      if (prev.length >= hlMaxSymbols) {
        setHlError(t('settings.maxSymbolsReached', `Maximum ${hlMaxSymbols} symbols`))
        return prev
      }
      return [...prev, symbolUpper]
    })
  }

  const toggleBnWatchlistSymbol = (symbol: string) => {
    const symbolUpper = symbol.toUpperCase()
    setBnError(null)
    setBnSuccess(null)
    setBnWatchlistSymbols((prev) => {
      if (prev.includes(symbolUpper)) {
        return prev.filter((s) => s !== symbolUpper)
      }
      if (prev.length >= bnMaxSymbols) {
        setBnError(t('settings.maxSymbolsReached', `Maximum ${bnMaxSymbols} symbols`))
        return prev
      }
      return [...prev, symbolUpper]
    })
  }

  const handleSaveWatchlist = async () => {
    setHlSaving(true)
    setHlError(null)
    setHlSuccess(null)
    try {
      await updateHyperliquidWatchlist(hlWatchlistSymbols)
      setHlSuccess(t('settings.watchlistSaved', 'Watchlist saved'))
    } catch (err) {
      setHlError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setHlSaving(false)
    }
  }

  const handleSaveBnWatchlist = async () => {
    setBnSaving(true)
    setBnError(null)
    setBnSuccess(null)
    try {
      await updateBinanceWatchlist(bnWatchlistSymbols)
      setBnSuccess(t('settings.watchlistSaved', 'Watchlist saved'))
    } catch (err) {
      setBnError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setBnSaving(false)
    }
  }

  // Filtered symbols for search
  const filteredHlSymbols = useMemo(() => {
    if (!hlSearchQuery.trim()) return hlAvailableSymbols
    const query = hlSearchQuery.toUpperCase()
    return hlAvailableSymbols.filter((sym) =>
      sym.name?.toUpperCase().includes(query) || sym.symbol?.toUpperCase().includes(query)
    )
  }, [hlAvailableSymbols, hlSearchQuery])

  const filteredBnSymbols = useMemo(() => {
    if (!bnSearchQuery.trim()) return bnAvailableSymbols
    const query = bnSearchQuery.toUpperCase()
    return bnAvailableSymbols.filter((sym) =>
      sym.name?.toUpperCase().includes(query) || sym.symbol?.toUpperCase().includes(query)
    )
  }, [bnAvailableSymbols, bnSearchQuery])

  const handleSaveRetention = async () => {
    if (!currentExchange) return
    const days = parseInt(retentionDays[currentExchange], 10)
    if (isNaN(days) || days < 7 || days > 730) {
      setRetentionError(t('settings.retentionRange', 'Must be between 7 and 730 days'))
      return
    }
    setRetentionSaving(true)
    setRetentionError(null)
    setRetentionSuccess(null)
    try {
      const res = await fetch('/api/system/retention-days', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days, exchange: currentExchange }),
      })
      if (!res.ok) throw new Error('Failed to update')
      setRetentionSuccess(t('settings.retentionSaved', 'Retention updated'))
      const stats = storageStats[currentExchange]
      if (stats) {
        setStorageStats((prev) => ({
          ...prev,
          [currentExchange]: { ...stats, retention_days: days },
        }))
      }
    } catch (err) {
      setRetentionError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setRetentionSaving(false)
    }
  }

  const enabledNewsSourceCount = useMemo(
    () => newsSources.filter((source) => source.enabled).length,
    [newsSources]
  )

  const hasUnsavedNewsSources = useMemo(
    () => JSON.stringify(newsSources) !== newsSourcesSnapshot,
    [newsSources, newsSourcesSnapshot]
  )

  const newsCountsByDomain = useMemo(() => {
    return Object.entries(newsStats?.last_24h?.by_domain || {}).reduce<Record<string, number>>((acc, [domain, count]) => {
      const normalizedDomain = domain.replace(/^www\./, '')
      acc[normalizedDomain] = (acc[normalizedDomain] || 0) + count
      return acc
    }, {})
  }, [newsStats])

  const extractDomain = (url: string) => {
    try {
      return new URL(url).hostname.replace(/^www\./, '')
    } catch {
      return url
    }
  }

  const formatDateTime = (value?: string | null) => {
    if (!value) return t('settings.notAvailable', 'N/A')
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return t('settings.notAvailable', 'N/A')
    return date.toLocaleString(currentLang === 'zh' ? 'zh-CN' : 'en-US')
  }

  const handleToggleNewsSource = (index: number, enabled: boolean) => {
    setNewsError(null)
    setNewsSuccess(null)
    setNewsSources((prev) => prev.map((source, sourceIndex) => (
      sourceIndex === index ? { ...source, enabled } : source
    )))
  }

  const handleSaveNewsSources = async () => {
    setNewsSaving(true)
    setNewsError(null)
    setNewsSuccess(null)
    try {
      const result = await updateNewsSources(newsSources)
      setNewsSources(result.sources || [])
      setNewsSourcesSnapshot(JSON.stringify(result.sources || []))
      setNewsSuccess(t('settings.newsSourcesSaved', 'News sources saved'))
      const stats = await getNewsStats()
      setNewsStats(stats)
    } catch (err) {
      setNewsError(err instanceof Error ? err.message : 'Failed to save news sources')
    } finally {
      setNewsSaving(false)
    }
  }

  const buildNewsSourceConfig = (
    adapter: 'rss_generic' | 'cryptopanic' | 'finnhub_calendar',
    url: string
  ) => {
    const interval = parseInt(newsFormInterval, 10)
    const intervalSeconds = Number.isFinite(interval) && interval > 0 ? interval : 300

    const config: Record<string, any> = {}
    if (adapter === 'cryptopanic' && newsFormAuthToken.trim()) {
      config.auth_token = newsFormAuthToken.trim()
    }
    if (adapter === 'finnhub_calendar' && newsFormApiKey.trim()) {
      config.api_key = newsFormApiKey.trim()
    }

    return {
      type: adapter === 'rss_generic' ? 'rss' : 'api',
      adapter,
      url,
      enabled: true,
      interval_seconds: intervalSeconds,
      config,
    } satisfies NewsSourceConfig
  }

  const handleTestNewsSource = async () => {
    const trimmedUrl = newsTestUrl.trim()
    if (!trimmedUrl) {
      setNewsTestError(t('settings.newsSourceUrlRequired', 'Please enter a source URL'))
      setNewsTestResult(null)
      return
    }

    try {
      new URL(trimmedUrl)
    } catch {
      setNewsTestError(t('settings.newsSourceUrlInvalid', 'Please enter a valid URL'))
      setNewsTestResult(null)
      return
    }

    const interval = parseInt(newsFormInterval, 10)
    if (!Number.isFinite(interval) || interval < 10) {
      setNewsTestError(t('settings.newsSourceIntervalInvalid', 'Interval must be at least 10 seconds'))
      setNewsTestResult(null)
      return
    }

    if (newsFormAdapter === 'cryptopanic' && !newsFormAuthToken.trim()) {
      setNewsTestError(t('settings.newsSourceAuthTokenRequired', 'Please enter a CryptoPanic auth token'))
      setNewsTestResult(null)
      return
    }

    if (newsFormAdapter === 'finnhub_calendar' && !newsFormApiKey.trim()) {
      setNewsTestError(t('settings.newsSourceApiKeyRequired', 'Please enter a Finnhub API key'))
      setNewsTestResult(null)
      return
    }

    setNewsTesting(true)
    setNewsTestError(null)
    setNewsTestResult(null)
    setNewsSuccess(null)

    try {
      const sourceConfig = buildNewsSourceConfig(newsFormAdapter, trimmedUrl)
      const result = await testNewsSource({
        url: trimmedUrl,
        adapter: newsFormAdapter,
        config: sourceConfig.config || {},
      })
      setNewsTestResult(result)
      if (!result.success) {
        setNewsTestError(result.error || t('settings.newsSourceTestFailed', 'Test failed'))
      }
    } catch (err) {
      setNewsTestError(err instanceof Error ? err.message : 'Failed to test source')
    } finally {
      setNewsTesting(false)
    }
  }

  const handleAddNewsSource = () => {
    const trimmedUrl = newsTestUrl.trim()
    if (!newsTestResult?.success || !trimmedUrl) {
      return
    }

    if (newsSources.some((source) => source.url === trimmedUrl)) {
      setNewsTestError(t('settings.newsSourceDuplicate', 'This source already exists'))
      return
    }

    const nextSources = [...newsSources, buildNewsSourceConfig(newsFormAdapter, trimmedUrl)]

    setNewsSources(nextSources)
    setNewsFormAdapter('rss_generic')
    setNewsTestUrl('')
    setNewsFormInterval('300')
    setNewsFormAuthToken('')
    setNewsFormApiKey('')
    setNewsTestError(null)
    setNewsTestResult(null)
    setNewsSuccess(t('settings.newsSourceAdded', 'Source added to the list. Save to apply.'))
  }

  const handleNewsSourceIntervalChange = (index: number, value: string) => {
    setNewsError(null)
    setNewsSuccess(null)
    setNewsSources((prev) => prev.map((source, sourceIndex) => {
      if (sourceIndex !== index) return source
      const parsed = parseInt(value, 10)
      return {
        ...source,
        interval_seconds: Number.isFinite(parsed) && parsed > 0 ? parsed : source.interval_seconds,
      }
    }))
  }

  return (
    <div className="p-6 h-[calc(100vh-64px)] flex flex-col overflow-hidden">
      {/* Language Settings - Compact row with border */}
      <div className="flex items-center gap-3 mb-6 shrink-0 p-4 border rounded-lg bg-card">
        <span className="text-sm font-medium">{t('settings.language', 'Language')}</span>
        <select
          value={currentLang}
          onChange={(e) => toggleLanguage(e.target.value as 'en' | 'zh')}
          className="border rounded px-2 py-1 text-sm bg-background"
        >
          <option value="en">English</option>
          <option value="zh">中文</option>
        </select>
      </div>

      {/* Tabs: Watchlist | Hyperliquid Data | Binance Data | News Sources */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
        <TabsList className="grid w-full grid-cols-4 max-w-3xl shrink-0">
          <TabsTrigger value="watchlist">{t('settings.watchlist', 'Watchlist')}</TabsTrigger>
          <TabsTrigger value="hyperliquid-data" className="flex items-center gap-1.5">
            <ExchangeIcon exchangeId="hyperliquid" size={16} />
            Hyperliquid
          </TabsTrigger>
          <TabsTrigger value="binance-data" className="flex items-center gap-1.5">
            <ExchangeIcon exchangeId="binance" size={16} />
            Binance
          </TabsTrigger>
          <TabsTrigger value="news-sources">{t('settings.newsSources', 'News Sources')}</TabsTrigger>
        </TabsList>

        {/* Watchlist Tab */}
        <TabsContent value="watchlist" className="mt-4 flex-1 min-h-0 flex flex-col overflow-auto">
          <div className="space-y-6">
            {/* Hyperliquid Watchlist */}
            <Card>
              <CardHeader className="shrink-0 pb-3">
                <div className="flex items-center gap-2">
                  <ExchangeIcon exchangeId="hyperliquid" size={24} />
                  <CardTitle className="text-base">Hyperliquid</CardTitle>
                </div>
                <CardDescription className="text-xs">
                  {t('settings.selectedCount', 'Selected')}: {hlWatchlistSymbols.length} / {hlMaxSymbols}
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                {hlLoading ? (
                  <div className="text-muted-foreground text-sm">{t('common.loading', 'Loading...')}</div>
                ) : (
                  <>
                    {/* Search input */}
                    <div className="mb-3">
                      <Input
                        type="text"
                        placeholder={t('settings.searchSymbol', 'Search symbol...')}
                        value={hlSearchQuery}
                        onChange={(e) => setHlSearchQuery(e.target.value)}
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
                      {filteredHlSymbols.map((sym) => {
                        const symbolName = sym.name || sym.symbol || ''
                        const isSelected = hlWatchlistSymbols.includes(symbolName.toUpperCase())
                        return (
                          <Button
                            key={symbolName}
                            variant={isSelected ? 'default' : 'outline'}
                            size="sm"
                            className="h-7 px-2 text-xs gap-1.5"
                            onClick={() => toggleWatchlistSymbol(symbolName)}
                          >
                            <CoinIcon symbol={symbolName} size={14} />
                            {symbolName}
                          </Button>
                        )
                      })}
                    </div>
                  </>
                )}
              </CardContent>
              <CardFooter className="shrink-0 border-t pt-3 flex items-center gap-3">
                <Button
                  size="sm"
                  onClick={handleSaveWatchlist}
                  disabled={hlSaving || hlLoading}
                >
                  {hlSaving ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                </Button>
                {hlError && <span className="text-red-500 text-xs">{hlError}</span>}
                {hlSuccess && <span className="text-green-500 text-xs">{hlSuccess}</span>}
              </CardFooter>
            </Card>

            {/* Binance Watchlist */}
            <Card>
              <CardHeader className="shrink-0 pb-3">
                <div className="flex items-center gap-2">
                  <ExchangeIcon exchangeId="binance" size={24} />
                  <CardTitle className="text-base">Binance</CardTitle>
                </div>
                <CardDescription className="text-xs">
                  {t('settings.selectedCount', 'Selected')}: {bnWatchlistSymbols.length} / {bnMaxSymbols}
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                {bnLoading ? (
                  <div className="text-muted-foreground text-sm">{t('common.loading', 'Loading...')}</div>
                ) : (
                  <>
                    {/* Search input */}
                    <div className="mb-3">
                      <Input
                        type="text"
                        placeholder={t('settings.searchSymbol', 'Search symbol...')}
                        value={bnSearchQuery}
                        onChange={(e) => setBnSearchQuery(e.target.value)}
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
                      {filteredBnSymbols.map((sym) => {
                        const symbolName = sym.name || sym.symbol || ''
                        const isSelected = bnWatchlistSymbols.includes(symbolName.toUpperCase())
                        return (
                          <Button
                            key={symbolName}
                            variant={isSelected ? 'default' : 'outline'}
                            size="sm"
                            className="h-7 px-2 text-xs gap-1.5"
                            onClick={() => toggleBnWatchlistSymbol(symbolName)}
                          >
                            <CoinIcon symbol={symbolName} size={14} />
                            {symbolName}
                          </Button>
                        )
                      })}
                    </div>
                  </>
                )}
              </CardContent>
              <CardFooter className="shrink-0 border-t pt-3 flex items-center gap-3">
                <Button
                  size="sm"
                  onClick={handleSaveBnWatchlist}
                  disabled={bnSaving || bnLoading}
                >
                  {bnSaving ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                </Button>
                {bnError && <span className="text-red-500 text-xs">{bnError}</span>}
                {bnSuccess && <span className="text-green-500 text-xs">{bnSuccess}</span>}
              </CardFooter>
            </Card>
          </div>
        </TabsContent>

        {/* Hyperliquid Data Tab */}
        <TabsContent value="hyperliquid-data" className="mt-4 flex-1 min-h-0 flex flex-col">
          <Card className="flex flex-col flex-1 min-h-0">
            <CardHeader className="shrink-0">
              <CardTitle className="flex items-center gap-2">
                <ExchangeIcon exchangeId="hyperliquid" size={24} />
                {t('settings.dataCollection', 'Data Collection')}
              </CardTitle>
              <CardDescription>
                {t('settings.dataCollectionDesc', 'Market flow data storage statistics')}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto min-h-0 space-y-6">
              {storageLoading ? (
                <div className="text-muted-foreground">{t('common.loading', 'Loading...')}</div>
              ) : storageStats['hyperliquid'] ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {t('settings.currentStorage', 'Current Storage')}
                      </div>
                      <div className="text-xl font-semibold">{storageStats['hyperliquid'].total_size_mb} MB</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {t('settings.collectedSymbols', 'Collected Symbols')}
                      </div>
                      <div className="text-xl font-semibold">{storageStats['hyperliquid'].symbol_count}</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {t('settings.retentionDays', 'Retention Days')}
                      </div>
                      <div className="text-xl font-semibold">{storageStats['hyperliquid'].retention_days}</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {t('settings.maxStorageEstimate', 'Max Storage Estimate')}
                      </div>
                      <div className="text-xl font-semibold">
                        {(watchlistSymbols.length * parseInt(retentionDays['hyperliquid'] || '365', 10) * storageStats['hyperliquid'].estimated_per_symbol_per_day_mb).toFixed(1)} MB
                      </div>
                    </div>
                  </div>
                  <div className="pt-4 border-t">
                    <div className="text-sm font-medium mb-2">
                      {t('settings.setRetention', 'Set Retention Period')}
                    </div>
                    <div className="flex items-center gap-2">
                      <Input
                        type="number"
                        value={retentionDays['hyperliquid'] || '365'}
                        onChange={(e) => setRetentionDays((prev) => ({ ...prev, hyperliquid: e.target.value }))}
                        className="w-24"
                        min={7}
                        max={730}
                      />
                      <span className="text-sm text-muted-foreground">{t('settings.days', 'days')}</span>
                      <Button onClick={handleSaveRetention} disabled={retentionSaving} size="sm">
                        {retentionSaving ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                      </Button>
                    </div>
                    {retentionError && <div className="text-red-500 text-sm mt-2">{retentionError}</div>}
                    {retentionSuccess && <div className="text-green-500 text-sm mt-2">{retentionSuccess}</div>}
                    <div className="text-xs text-muted-foreground mt-1">
                      {t('settings.retentionHint', 'Data older than this will be automatically cleaned up (7-730 days)')}
                    </div>
                  </div>
                  {/* Hyperliquid Backfill Section */}
                  <div className="pt-4 border-t">
                    <div className="text-sm font-medium mb-2">
                      {t('settings.backfillHistory', 'Backfill Historical Data')}
                    </div>
                    <div className="text-xs text-muted-foreground mb-3">
                      {t('settings.hyperliquidBackfillDesc', 'K-lines (~5000 records, ~3.5 days per symbol)')}
                    </div>
                    {backfillStatus['hyperliquid']?.status === 'running' || backfillStatus['hyperliquid']?.status === 'pending' ? (
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                            <div
                              className="h-full bg-primary transition-all duration-300"
                              style={{ width: `${backfillStatus['hyperliquid']?.progress || 0}%` }}
                            />
                          </div>
                          <span className="text-sm font-medium">{backfillStatus['hyperliquid']?.progress || 0}%</span>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {t('settings.backfillRunning', 'Backfilling')} {backfillStatus['hyperliquid']?.symbols?.join(', ')}...
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <Button
                          onClick={() => handleStartBackfill('hyperliquid')}
                          disabled={backfillStarting['hyperliquid']}
                          size="sm"
                          variant="outline"
                        >
                          {backfillStarting['hyperliquid'] ? t('common.loading', 'Loading...') : t('settings.startBackfill', 'Start Backfill')}
                        </Button>
                        {backfillJustCompleted['hyperliquid'] && (
                          <div className="text-xs text-green-500">
                            {t('settings.backfillCompleted', 'Last backfill completed successfully')}
                          </div>
                        )}
                        {backfillStatus['hyperliquid']?.status === 'failed' && backfillStatus['hyperliquid']?.task_id && (
                          <div className="text-xs text-red-500">
                            {t('settings.backfillFailed', 'Last backfill failed')}: {backfillStatus['hyperliquid']?.error_message}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="text-muted-foreground">{t('settings.noData', 'No data available')}</div>
              )}
              <div className="pt-4 border-t">
                <div className="text-sm font-medium mb-3">{t('settings.marketFlowCoverage', 'Market Flow Coverage')}</div>
                <DataCoverageHeatmap exchange="hyperliquid" dataType="market_flow" />
              </div>
              <div className="pt-4 border-t">
                <div className="text-sm font-medium mb-3">{t('settings.klineCoverage', 'K-line Coverage')}</div>
                <DataCoverageHeatmap exchange="hyperliquid" dataType="klines" />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Binance Data Tab */}
        <TabsContent value="binance-data" className="mt-4 flex-1 min-h-0 flex flex-col">
          <Card className="flex flex-col flex-1 min-h-0">
            <CardHeader className="shrink-0">
              <CardTitle className="flex items-center gap-2">
                <ExchangeIcon exchangeId="binance" size={24} />
                {t('settings.dataCollection', 'Data Collection')}
              </CardTitle>
              <CardDescription>
                {t('settings.dataCollectionDesc', 'Market flow data storage statistics')}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto min-h-0 space-y-6">
              {storageLoading ? (
                <div className="text-muted-foreground">{t('common.loading', 'Loading...')}</div>
              ) : storageStats['binance'] ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {t('settings.currentStorage', 'Current Storage')}
                      </div>
                      <div className="text-xl font-semibold">{storageStats['binance'].total_size_mb} MB</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {t('settings.collectedSymbols', 'Collected Symbols')}
                      </div>
                      <div className="text-xl font-semibold">{storageStats['binance'].symbol_count}</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {t('settings.retentionDays', 'Retention Days')}
                      </div>
                      <div className="text-xl font-semibold">{storageStats['binance'].retention_days}</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {t('settings.maxStorageEstimate', 'Max Storage Estimate')}
                      </div>
                      <div className="text-xl font-semibold">
                        {(watchlistSymbols.length * parseInt(retentionDays['binance'] || '365', 10) * storageStats['binance'].estimated_per_symbol_per_day_mb).toFixed(1)} MB
                      </div>
                    </div>
                  </div>
                  <div className="pt-4 border-t">
                    <div className="text-sm font-medium mb-2">
                      {t('settings.setRetention', 'Set Retention Period')}
                    </div>
                    <div className="flex items-center gap-2">
                      <Input
                        type="number"
                        value={retentionDays['binance'] || '365'}
                        onChange={(e) => setRetentionDays((prev) => ({ ...prev, binance: e.target.value }))}
                        className="w-24"
                        min={7}
                        max={730}
                      />
                      <span className="text-sm text-muted-foreground">{t('settings.days', 'days')}</span>
                      <Button onClick={handleSaveRetention} disabled={retentionSaving} size="sm">
                        {retentionSaving ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                      </Button>
                    </div>
                    {retentionError && <div className="text-red-500 text-sm mt-2">{retentionError}</div>}
                    {retentionSuccess && <div className="text-green-500 text-sm mt-2">{retentionSuccess}</div>}
                    <div className="text-xs text-muted-foreground mt-1">
                      {t('settings.retentionHint', 'Data older than this will be automatically cleaned up (7-730 days)')}
                    </div>
                  </div>
                  {/* Backfill Section */}
                  <div className="pt-4 border-t">
                    <div className="text-sm font-medium mb-2">
                      {t('settings.backfillHistory', 'Backfill Historical Data')}
                    </div>
                    <div className="text-xs text-muted-foreground mb-3">
                      {t('settings.backfillDesc', 'K-lines (25h), OI (30d), Funding Rate (365d), Long/Short Ratio (30d)')}
                    </div>
                    {backfillStatus['binance']?.status === 'running' || backfillStatus['binance']?.status === 'pending' ? (
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                            <div
                              className="h-full bg-primary transition-all duration-300"
                              style={{ width: `${backfillStatus['binance']?.progress || 0}%` }}
                            />
                          </div>
                          <span className="text-sm font-medium">{backfillStatus['binance']?.progress || 0}%</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <div className="text-xs text-muted-foreground">
                            {t('settings.backfillRunning', 'Backfilling')} {backfillStatus['binance']?.symbols?.join(', ')}...
                          </div>
                          <Button
                            onClick={() => handleStartBackfill('binance', true)}
                            disabled={backfillStarting['binance']}
                            size="sm"
                            variant="ghost"
                            className="h-6 px-2 text-xs"
                          >
                            {t('settings.restartBackfill', 'Restart')}
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <Button
                          onClick={() => handleStartBackfill('binance')}
                          disabled={backfillStarting['binance']}
                          size="sm"
                          variant="outline"
                        >
                          {backfillStarting['binance'] ? t('common.loading', 'Loading...') : t('settings.startBackfill', 'Start Backfill')}
                        </Button>
                        {backfillJustCompleted['binance'] && (
                          <div className="text-xs text-green-500">
                            {t('settings.backfillCompleted', 'Last backfill completed successfully')}
                          </div>
                        )}
                        {backfillStatus['binance']?.status === 'failed' && backfillStatus['binance']?.task_id && (
                          <div className="text-xs text-red-500">
                            {t('settings.backfillFailed', 'Last backfill failed')}: {backfillStatus['binance']?.error_message}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="text-muted-foreground">{t('settings.noData', 'No data available')}</div>
              )}
              <div className="pt-4 border-t">
                <div className="text-sm font-medium mb-3">{t('settings.marketFlowCoverage', 'Market Flow Coverage')}</div>
                <DataCoverageHeatmap exchange="binance" dataType="market_flow" />
              </div>
              <div className="pt-4 border-t">
                <div className="text-sm font-medium mb-3">{t('settings.klineCoverage', 'K-line Coverage')}</div>
                <DataCoverageHeatmap exchange="binance" dataType="klines" />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="news-sources" className="mt-4 flex-1 min-h-0 flex flex-col overflow-auto">
          <div className="space-y-6">
            <Card>
              <CardHeader className="shrink-0">
                <CardTitle>{t('settings.newsSources', 'News Sources')}</CardTitle>
                <CardDescription>
                  {t('settings.newsSourcesDesc', 'Manage RSS sources and review collection health')}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {newsLoading ? (
                  <div className="text-muted-foreground text-sm">{t('common.loading', 'Loading...')}</div>
                ) : (
                  <>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                      <div>
                        <div className="text-sm text-muted-foreground">
                          {t('settings.newsTotalArticles', 'Total Articles')}
                        </div>
                        <div className="text-xl font-semibold">{newsStats?.total_articles ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted-foreground">
                          {t('settings.newsLast24h', 'New in 24h')}
                        </div>
                        <div className="text-xl font-semibold">{newsStats?.last_24h?.total ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted-foreground">
                          {t('settings.newsEnabledSources', 'Enabled Sources')}
                        </div>
                        <div className="text-xl font-semibold">{enabledNewsSourceCount}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted-foreground">
                          {t('settings.newsLatestCollected', 'Latest Article')}
                        </div>
                        <div className="text-sm font-medium break-words">
                          {formatDateTime(newsStats?.latest_article_at)}
                        </div>
                      </div>
                    </div>

                    <div className="pt-4 border-t space-y-3">
                      <div className="text-sm font-medium">
                        {t('settings.newsConfiguredSources', 'Configured Sources')}
                      </div>
                      {newsSources.length === 0 ? (
                        <div className="text-sm text-muted-foreground">
                          {t('settings.newsNoSources', 'No news sources configured')}
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {newsSources.map((source, index) => {
                            const domain = extractDomain(source.url)
                            const last24hCount = newsCountsByDomain[domain] ?? 0
                            return (
                              <div
                                key={`${source.url}-${index}`}
                                className="flex flex-col gap-3 rounded-lg border p-3 md:flex-row md:items-center md:justify-between"
                              >
                                <div className="min-w-0 flex-1 space-y-1">
                                  <div className="flex items-center gap-2">
                                    <span className="inline-flex rounded-full bg-muted px-2 py-0.5 text-xs font-medium">
                                      {domain}
                                    </span>
                                    <span className="text-xs text-muted-foreground">
                                      {t('settings.newsCollected24h', '{{count}} in 24h', { count: last24hCount })}
                                    </span>
                                  </div>
                                  <div className="truncate text-sm text-muted-foreground">
                                    {source.url}
                                  </div>
                                  <div className="text-xs text-muted-foreground">
                                    {t('settings.newsIntervalSeconds', 'Interval')}: {source.interval_seconds}s
                                  </div>
                                </div>
                                <div className="flex items-center gap-3 flex-wrap md:flex-nowrap">
                                  <Input
                                    type="number"
                                    min={10}
                                    className="w-24 h-8 text-xs"
                                    value={source.interval_seconds}
                                    onChange={(e) => handleNewsSourceIntervalChange(index, e.target.value)}
                                  />
                                  <span className="text-xs text-muted-foreground">
                                    {source.enabled
                                      ? t('settings.enabled', 'Enabled')
                                      : t('settings.disabled', 'Disabled')}
                                  </span>
                                  <Switch
                                    checked={source.enabled}
                                    onCheckedChange={(checked) => handleToggleNewsSource(index, checked)}
                                  />
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  </>
                )}
              </CardContent>
              <CardFooter className="border-t pt-3 flex items-center gap-3">
                <Button
                  size="sm"
                  onClick={handleSaveNewsSources}
                  disabled={newsLoading || newsSaving || !hasUnsavedNewsSources}
                >
                  {newsSaving ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={fetchNewsSourcesData}
                  disabled={newsLoading || newsSaving}
                >
                  {t('common.refresh', 'Refresh')}
                </Button>
                {newsError && <span className="text-red-500 text-xs">{newsError}</span>}
                {newsSuccess && <span className="text-green-500 text-xs">{newsSuccess}</span>}
              </CardFooter>
            </Card>

            <Card>
              <CardHeader className="shrink-0">
                <CardTitle>{t('settings.addNewsSource', 'Add New Source')}</CardTitle>
                <CardDescription>
                  {t('settings.addNewsSourceDesc', 'Test an RSS feed before adding it to the source list')}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-4">
                  <div className="space-y-2">
                    <div className="text-xs text-muted-foreground">{t('settings.newsSourceType', 'Source Type')}</div>
                    <Select
                      value={newsFormAdapter}
                      onValueChange={(value: 'rss_generic' | 'cryptopanic' | 'finnhub_calendar') => {
                        setNewsFormAdapter(value)
                        setNewsTestResult(null)
                        setNewsTestError(null)
                      }}
                    >
                      <SelectTrigger className="h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="rss_generic">RSS / Atom</SelectItem>
                        <SelectItem value="cryptopanic">CryptoPanic</SelectItem>
                        <SelectItem value="finnhub_calendar">Finnhub Calendar</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <div className="text-xs text-muted-foreground">{t('settings.newsIntervalSeconds', 'Interval')}</div>
                    <Input
                      type="number"
                      min={10}
                      value={newsFormInterval}
                      onChange={(e) => {
                        setNewsFormInterval(e.target.value)
                        setNewsTestResult(null)
                        setNewsTestError(null)
                      }}
                    />
                  </div>
                  {newsFormAdapter === 'cryptopanic' && (
                    <div className="space-y-2 md:col-span-2">
                      <div className="text-xs text-muted-foreground">{t('settings.newsAuthToken', 'Auth Token')}</div>
                      <Input
                        type="password"
                        value={newsFormAuthToken}
                        onChange={(e) => {
                          setNewsFormAuthToken(e.target.value)
                          setNewsTestResult(null)
                          setNewsTestError(null)
                        }}
                      />
                    </div>
                  )}
                  {newsFormAdapter === 'finnhub_calendar' && (
                    <div className="space-y-2 md:col-span-2">
                      <div className="text-xs text-muted-foreground">{t('settings.newsApiKey', 'API Key')}</div>
                      <Input
                        type="password"
                        value={newsFormApiKey}
                        onChange={(e) => {
                          setNewsFormApiKey(e.target.value)
                          setNewsTestResult(null)
                          setNewsTestError(null)
                        }}
                      />
                    </div>
                  )}
                </div>

                <div className="flex flex-col gap-3 md:flex-row">
                  <Input
                    type="url"
                    placeholder={t('settings.newsSourceUrlPlaceholder', 'https://example.com/rss')}
                    value={newsTestUrl}
                    onChange={(e) => {
                      setNewsTestUrl(e.target.value)
                      setNewsTestError(null)
                      setNewsTestResult(null)
                    }}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={handleTestNewsSource}
                    disabled={newsTesting}
                  >
                    {newsTesting ? t('settings.testing', 'Testing...') : t('settings.test', 'Test')}
                  </Button>
                  <Button
                    type="button"
                    onClick={handleAddNewsSource}
                    disabled={!newsTestResult?.success}
                  >
                    {t('settings.add', 'Add')}
                  </Button>
                </div>

                {newsTestError && (
                  <div className="text-sm text-red-500">{newsTestError}</div>
                )}

                {newsTestResult?.success && (
                  <div className="rounded-lg border p-4 space-y-3">
                    <div className="text-sm font-medium">
                      {t('settings.newsSourceTestSuccess', 'Fetched {{count}} articles', {
                        count: newsTestResult.total_fetched ?? newsTestResult.articles.length,
                      })}
                    </div>
                    {newsTestResult.articles.length > 0 && (
                      <div className="space-y-2">
                        <div className="text-xs uppercase tracking-wide text-muted-foreground">
                          {t('settings.newsSampleTitles', 'Sample Titles')}
                        </div>
                        {newsTestResult.articles.slice(0, 5).map((article, index) => (
                          <div key={`${article.source_url}-${index}`} className="text-sm">
                            {article.title || article.source_url}
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="rounded-md bg-muted/50 p-3 space-y-1">
                      <div className="text-sm font-medium">
                        {newsTestResult.validation?.schema_match
                          ? t('settings.newsSchemaMatchYes', 'Schema validation passed')
                          : t('settings.newsSchemaMatchNo', 'Schema validation found issues')}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {t('settings.newsSchemaValidationSummary', 'Valid {{valid}} / Invalid {{invalid}}', {
                          valid: newsTestResult.validation?.valid_articles ?? 0,
                          invalid: newsTestResult.validation?.invalid_articles ?? 0,
                        })}
                      </div>
                      {!!newsTestResult.validation?.issues?.length && (
                        <div className="space-y-1">
                          {newsTestResult.validation.issues.slice(0, 5).map((issue, index) => (
                            <div key={`${issue.source_url}-${index}`} className="text-xs text-amber-600">
                              {issue.issues.join(', ')}: {issue.source_url}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
