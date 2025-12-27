import React, { useEffect, useMemo, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  ArenaAccountMeta,
  ArenaModelChatEntry,
  ArenaPositionsAccount,
  ArenaTrade,
  getArenaModelChat,
  getArenaPositions,
  getArenaTrades,
  getAccounts,
  getModelChatSnapshots,
  ModelChatSnapshots,
  getHyperliquidWatchlist,
  updateArenaPnl,
} from '@/lib/api'
import { useArenaData } from '@/contexts/ArenaDataContext'
import { useTradingMode } from '@/contexts/TradingModeContext'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { getModelLogo } from './logoAssets'
import FlipNumber from './FlipNumber'
import HighlightWrapper from './HighlightWrapper'
import { formatDateTime } from '@/lib/dateTime'
import { Loader2 } from 'lucide-react'
import { copyToClipboard } from '@/lib/utils'

interface AlphaArenaFeedProps {
  refreshKey?: number
  autoRefreshInterval?: number
  wsRef?: React.MutableRefObject<WebSocket | null>
  selectedAccount?: number | 'all'
  onSelectedAccountChange?: (accountId: number | 'all') => void
  walletAddress?: string
  onPageChange?: (page: string) => void
}

type FeedTab = 'trades' | 'model-chat' | 'positions'

const DEFAULT_LIMIT = 100
const MODEL_CHAT_LIMIT = 60

type CacheKey = string

// Use formatDateTime from @/lib/dateTime with 'short' style for compact display
const formatDate = (value?: string | null) => formatDateTime(value, { style: 'short' })

function formatPercent(value?: number | null) {
  if (value === undefined || value === null) return '—'
  return `${(value * 100).toFixed(2)}%`
}

function renderSymbolBadge(symbol?: string, size: 'sm' | 'md' = 'md') {
  if (!symbol) return null
  const text = symbol.slice(0, 4).toUpperCase()
  const baseClasses = 'inline-flex items-center justify-center rounded bg-muted text-muted-foreground font-semibold'
  const sizeClasses = size === 'sm' ? 'h-4 w-4 text-[9px]' : 'h-5 w-5 text-[10px]'
  return <span className={`${baseClasses} ${sizeClasses}`}>{text}</span>
}


export default function AlphaArenaFeed({
  refreshKey,
  autoRefreshInterval = 60_000,
  wsRef,
  selectedAccount: selectedAccountProp,
  onSelectedAccountChange,
  walletAddress,
  onPageChange,
}: AlphaArenaFeedProps) {
  const { t } = useTranslation()
  const { getData, updateData } = useArenaData()
  const { tradingMode } = useTradingMode()
  const [activeTab, setActiveTab] = useState<FeedTab>('trades')
  const [allTraderOptions, setAllTraderOptions] = useState<ArenaAccountMeta[]>([])
  const [loadingAccounts, setLoadingAccounts] = useState(false)
  const [internalSelectedAccount, setInternalSelectedAccount] = useState<number | 'all'>(
    selectedAccountProp ?? 'all',
  )
  const [expandedChat, setExpandedChat] = useState<number | null>(null)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({})
  const [copiedSections, setCopiedSections] = useState<Record<string, boolean>>({})
  const [manualRefreshKey, setManualRefreshKey] = useState(0)
  const [loadingTrades, setLoadingTrades] = useState(false)
  const [loadingModelChat, setLoadingModelChat] = useState(false)
  const [loadingPositions, setLoadingPositions] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [updatingPnl, setUpdatingPnl] = useState(false)
  const [pnlUpdateResult, setPnlUpdateResult] = useState<string | null>(null)
  const [showPnlConfirm, setShowPnlConfirm] = useState(false)

  const [trades, setTrades] = useState<ArenaTrade[]>([])
  const [modelChat, setModelChat] = useState<ArenaModelChatEntry[]>([])
  const [positions, setPositions] = useState<ArenaPositionsAccount[]>([])
  const [accountsMeta, setAccountsMeta] = useState<ArenaAccountMeta[]>([])

  // Lazy loading states for ModelChat
  const [hasMoreModelChat, setHasMoreModelChat] = useState(true)
  const [isLoadingMoreModelChat, setIsLoadingMoreModelChat] = useState(false)

  // Snapshot lazy loading cache and states
  const snapshotCache = useRef<Map<number, ModelChatSnapshots>>(new Map())
  const [loadingSnapshots, setLoadingSnapshots] = useState<Set<number>>(new Set())

  // New states for symbol selection
  const [symbolOptions, setSymbolOptions] = useState<string[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)

  // Track seen items for highlight animation
  const seenTradeIds = useRef<Set<number>>(new Set())
  const seenDecisionIds = useRef<Set<number>>(new Set())
  const prevManualRefreshKey = useRef(manualRefreshKey)
  const prevRefreshKey = useRef(refreshKey)
  const prevTradingMode = useRef(tradingMode)

  // Sync external account selection with internal state
  useEffect(() => {
    if (selectedAccountProp !== undefined) {
      setInternalSelectedAccount(selectedAccountProp)
    }
  }, [selectedAccountProp])

  // Compute active account and cache key
  const activeAccount = useMemo(() => selectedAccountProp ?? internalSelectedAccount, [selectedAccountProp, internalSelectedAccount])
  const prevActiveAccount = useRef<number | 'all'>(activeAccount)
  const cacheKey: CacheKey = useMemo(() => {
    const accountKey = activeAccount === 'all' ? 'all' : String(activeAccount)
    const walletKey = walletAddress ? walletAddress.toLowerCase() : 'nowallet'
    return `${accountKey}_${tradingMode}_${walletKey}`
  }, [activeAccount, tradingMode, walletAddress])

  // Initialize from global state on mount or account change
  useEffect(() => {
    const globalData = getData(cacheKey)
    if (globalData) {
      setTrades(globalData.trades)
      setModelChat(globalData.modelChat)
      setPositions(globalData.positions)
      setAccountsMeta(globalData.accountsMeta)
      setLoadingTrades(false)
      setLoadingModelChat(false)
      setLoadingPositions(false)
    }
  }, [cacheKey, getData])

  const writeCache = useCallback(
    (key: CacheKey, data: Partial<{ trades: ArenaTrade[]; modelChat: ArenaModelChatEntry[]; positions: ArenaPositionsAccount[] }>) => {
      updateData(key, data)
    },
    [updateData],
  )

  // Listen for real-time WebSocket updates
  useEffect(() => {
    if (!wsRef?.current) return

    const handleMessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data)

        // Filter by trading mode/environment first
        const msgEnvironment = msg.trade?.environment || msg.decision?.environment || msg.trading_mode
        if (msgEnvironment && msgEnvironment !== tradingMode) {
          // Ignore messages from different trading environments
          return
        }

        // Only process messages for the active account or all accounts
        const msgAccountId = msg.trade?.account_id || msg.decision?.account_id
        const shouldProcess = activeAccount === 'all' || !msgAccountId || msgAccountId === activeAccount

        if (!shouldProcess) return

        const messageWallet: string | undefined =
          msg.trade?.wallet_address || msg.decision?.wallet_address || undefined
        if (walletAddress) {
          if (!messageWallet) return
          if (messageWallet.toLowerCase() !== walletAddress.toLowerCase()) return
        }

        if (msg.type === 'trade_update' && msg.trade) {
          // Prepend new trade to the list
          setTrades((prev) => {
            // Check if trade already exists to prevent duplicates
            const exists = prev.some((t) => t.trade_id === msg.trade.trade_id)
            if (exists) return prev
            const next = [msg.trade, ...prev].slice(0, DEFAULT_LIMIT)
            writeCache(cacheKey, { trades: next })
            return next
          })
        }

        if (msg.type === 'position_update' && msg.positions) {
          // Update positions for the relevant account
          setPositions((prev) => {
            // If no account_id specified in message, this is a full update for one account
            const accountId = msg.positions[0]?.account_id
            if (!accountId) return msg.positions

            // Replace positions for this specific account
            const otherAccounts = prev.filter((acc) => acc.account_id !== accountId)
            // Find if we have position data in the message
            const newAccountPositions = msg.positions.filter((p: any) => p.account_id === accountId)

            if (newAccountPositions.length > 0) {
              // Construct account snapshot from positions
            const previousMeta = prev.find((acc) => acc.account_id === accountId)
            const accountSnapshot = {
                account_id: accountId,
                account_name: previousMeta?.account_name || '',
                environment: previousMeta?.environment || null,
                available_cash: 0, // Will be updated by next snapshot
                used_margin: previousMeta?.used_margin ?? 0,
                positions_value: previousMeta?.positions_value ?? 0,
                total_unrealized_pnl: 0,
                total_assets: previousMeta?.total_assets ?? 0,
                initial_capital: previousMeta?.initial_capital ?? 0,
                total_return: previousMeta?.total_return ?? null,
                margin_usage_percent: previousMeta?.margin_usage_percent ?? null,
                margin_mode: previousMeta?.margin_mode ?? null,
                positions: newAccountPositions,
              }
              const next = [...otherAccounts, accountSnapshot]
              writeCache(cacheKey, { positions: next })
              return next
            }

            return prev
          })
        }

        if (msg.type === 'model_chat_update' && msg.decision) {
          // Prepend new AI decision to the list
          setModelChat((prev) => {
            // Check if decision already exists to prevent duplicates
            const exists = prev.some((entry) => entry.id === msg.decision.id)
            if (exists) return prev
            const next = [msg.decision, ...prev].slice(0, MODEL_CHAT_LIMIT)
            writeCache(cacheKey, { modelChat: next })
            return next
          })
        }
      } catch (err) {
        console.error('Failed to parse AlphaArenaFeed WebSocket message:', err)
      }
    }

    wsRef.current.addEventListener('message', handleMessage)

    return () => {
      wsRef.current?.removeEventListener('message', handleMessage)
    }
  }, [wsRef, activeAccount, cacheKey, walletAddress, writeCache])

  // Load accounts for dropdown - use dedicated API instead of positions data
  const loadAccounts = useCallback(async () => {
    try {
      setLoadingAccounts(true)
      const accounts = await getAccounts()
      const accountMetas = accounts.map(acc => ({
        account_id: acc.id,
        name: acc.name,
        model: acc.model ?? null,
      }))
      setAllTraderOptions(accountMetas)
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load accounts:', err)
    } finally {
      setLoadingAccounts(false)
    }
  }, [])

  // Load accounts immediately on mount
  useEffect(() => {
    if (allTraderOptions.length === 0 && !loadingAccounts) {
      loadAccounts()
    }
  }, [])

  // Individual loaders for each data type
  const loadTradesData = useCallback(async () => {
    try {
      setLoadingTrades(true)
      const accountId = activeAccount === 'all' ? undefined : activeAccount
      const symbol = selectedSymbol || undefined
      const tradeRes = await getArenaTrades({
        limit: DEFAULT_LIMIT,
        account_id: accountId,
        trading_mode: tradingMode,
        wallet_address: walletAddress,
        symbol: symbol,
      })
      const newTrades = tradeRes.trades || []
      setTrades(newTrades)
      updateData(cacheKey, { trades: newTrades })

      // Extract metadata from trades
      if (tradeRes.accounts) {
        const metas = tradeRes.accounts
        setAccountsMeta(prev => {
          const metaMap = new Map(prev.map(m => [m.account_id, m]))
          metas.forEach(m => metaMap.set(m.account_id, m))
          return Array.from(metaMap.values())
        })
        updateData(cacheKey, { accountsMeta: Array.from(new Map(tradeRes.accounts.map(m => [m.account_id, m])).values()) })
      }

      setLoadingTrades(false)
      return tradeRes
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load trades:', err)
      setLoadingTrades(false)
      return null
    }
  }, [activeAccount, cacheKey, updateData, tradingMode, walletAddress, selectedSymbol])

  // Helper function to merge and deduplicate model chat entries
  const mergeModelChatData = useCallback((existing: ArenaModelChatEntry[], newData: ArenaModelChatEntry[]) => {
    // Create a Map for fast lookup by id
    const idMap = new Map(existing.map(item => [item.id, item]))

    // Add new data, skip duplicates
    newData.forEach(item => {
      if (!idMap.has(item.id)) {
        idMap.set(item.id, item)
      }
    })

    // Convert back to array and sort by decision_time descending
    return Array.from(idMap.values()).sort((a, b) => {
      const timeA = a.decision_time ? new Date(a.decision_time).getTime() : 0
      const timeB = b.decision_time ? new Date(b.decision_time).getTime() : 0
      return timeB - timeA
    })
  }, [])

  const loadModelChatData = useCallback(async (isBackgroundRefresh: boolean = false) => {
    try {
      setLoadingModelChat(true)
      const accountId = activeAccount === 'all' ? undefined : activeAccount
      const symbol = selectedSymbol || undefined
      const chatRes = await getArenaModelChat({
        limit: MODEL_CHAT_LIMIT,
        account_id: accountId,
        trading_mode: tradingMode,
        wallet_address: walletAddress,
        symbol: symbol,
      })
      const newModelChat = chatRes.entries || []

      // If this is a background refresh and user has loaded more history, merge instead of replace
      if (isBackgroundRefresh && modelChat.length > MODEL_CHAT_LIMIT) {
        // Merge new data with existing data, preserving user's loaded history
        const merged = mergeModelChatData(modelChat, newModelChat)
        setModelChat(merged)
        updateData(cacheKey, { modelChat: merged })
        // Keep hasMoreModelChat state unchanged during background refresh
      } else {
        // Initial load or manual refresh: replace all data
        setModelChat(newModelChat)
        updateData(cacheKey, { modelChat: newModelChat })
        // Reset lazy loading state when loading fresh data
        setHasMoreModelChat(newModelChat.length === MODEL_CHAT_LIMIT)
      }

      // Extract metadata from modelchat
      if (chatRes.entries && chatRes.entries.length > 0) {
        const metas = chatRes.entries.map(entry => ({
          account_id: entry.account_id,
          name: entry.account_name,
          model: entry.model ?? null,
        }))
        setAccountsMeta(prev => {
          const metaMap = new Map(prev.map(m => [m.account_id, m]))
          metas.forEach(m => metaMap.set(m.account_id, m))
          return Array.from(metaMap.values())
        })
      }

      setLoadingModelChat(false)
      return chatRes
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load model chat:', err)
      setLoadingModelChat(false)
      return null
    }

  }, [activeAccount, cacheKey, updateData, tradingMode, walletAddress, modelChat, mergeModelChatData, selectedSymbol])

  // Load more model chat entries (lazy loading)
  const loadMoreModelChat = useCallback(async () => {
    if (isLoadingMoreModelChat || !hasMoreModelChat || modelChat.length === 0) return

    try {
      setIsLoadingMoreModelChat(true)

      // Get the oldest decision_time from current list
      const oldestEntry = modelChat[modelChat.length - 1]
      const beforeTime = oldestEntry?.decision_time

      if (!beforeTime) {
        setHasMoreModelChat(false)
        setIsLoadingMoreModelChat(false)
        return
      }

      const accountId = activeAccount === 'all' ? undefined : activeAccount
      const chatRes = await getArenaModelChat({
        limit: MODEL_CHAT_LIMIT,
        account_id: accountId,
        trading_mode: tradingMode,
        wallet_address: walletAddress,
        before_time: beforeTime,
      })

      const newEntries = chatRes.entries || []

      // Merge and deduplicate
      const merged = mergeModelChatData(modelChat, newEntries)
      setModelChat(merged)
      updateData(cacheKey, { modelChat: merged })

      // If we got fewer entries than requested, there's no more data
      setHasMoreModelChat(newEntries.length === MODEL_CHAT_LIMIT)

      setIsLoadingMoreModelChat(false)
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load more model chat:', err)
      setIsLoadingMoreModelChat(false)
    }
  }, [activeAccount, cacheKey, updateData, tradingMode, walletAddress, modelChat, hasMoreModelChat, isLoadingMoreModelChat, mergeModelChatData, selectedSymbol])

  const loadPositionsData = useCallback(async () => {
    try {
      setLoadingPositions(true)
      const accountId = activeAccount === 'all' ? undefined : activeAccount
      const positionRes = await getArenaPositions({ account_id: accountId, trading_mode: tradingMode })
      const newPositions = positionRes.accounts || []
      setPositions(newPositions)
      updateData(cacheKey, { positions: newPositions })

      // Extract metadata from positions
      if (positionRes.accounts) {
        const metas = positionRes.accounts.map(account => ({
          account_id: account.account_id,
          name: account.account_name,
          model: account.model ?? null,
        }))
        setAccountsMeta(prev => {
          const metaMap = new Map(prev.map(m => [m.account_id, m]))
          metas.forEach(m => metaMap.set(m.account_id, m))
          return Array.from(metaMap.values())
        })
        updateData(cacheKey, { accountsMeta: Array.from(new Map(metas.map(m => [m.account_id, m])).values()) })
      }

      setLoadingPositions(false)
      return positionRes
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load positions:', err)
      setLoadingPositions(false)
      return null
    }
  }, [activeAccount, cacheKey, updateData, tradingMode])

  // Lazy load data when tab becomes active
  useEffect(() => {
    const cached = getData(cacheKey)

    if (activeTab === 'trades' && trades.length === 0 && !loadingTrades) {
      if (cached?.trades && cached.trades.length > 0) {
        setTrades(cached.trades)
      } else {
        loadTradesData()
      }
    }

    if (activeTab === 'model-chat' && modelChat.length === 0 && !loadingModelChat) {
      if (cached?.modelChat && cached.modelChat.length > 0) {
        setModelChat(cached.modelChat)
      } else {
        loadModelChatData(false) // false = initial load, not background refresh
      }
    }

    if (activeTab === 'positions' && positions.length === 0 && !loadingPositions) {
      if (cached?.positions && cached.positions.length > 0) {
        setPositions(cached.positions)
      } else {
        loadPositionsData()
      }
    }
  }, [activeTab, cacheKey])

  // Background polling - refresh all data regardless of active tab
  useEffect(() => {
    if (autoRefreshInterval <= 0) return

    const pollAllData = async () => {
      // Load all three APIs in background, independent of active tab
      // For ModelChat, use background refresh mode to preserve loaded history
      await Promise.allSettled([
        loadTradesData(),
        loadModelChatData(true), // true = background refresh, preserve loaded history
        loadPositionsData()
      ])
    }

    const intervalId = setInterval(pollAllData, autoRefreshInterval)

    return () => clearInterval(intervalId)
  }, [autoRefreshInterval, loadTradesData, loadModelChatData, loadPositionsData])

  // Manual refresh trigger handler
  useEffect(() => {
    const shouldForce =
      manualRefreshKey !== prevManualRefreshKey.current ||
      refreshKey !== prevRefreshKey.current

    if (shouldForce) {
      prevManualRefreshKey.current = manualRefreshKey
      prevRefreshKey.current = refreshKey

      // Force refresh all data (manual refresh = full reload, not background refresh)
      Promise.allSettled([
        loadTradesData(),
        loadModelChatData(false), // false = full reload, reset to initial 60 entries
        loadPositionsData()
      ])
    }
  }, [manualRefreshKey, refreshKey, loadTradesData, loadModelChatData, loadPositionsData])

  // Reload data when account filter changes
  useEffect(() => {
    // Skip initial mount
    if (prevActiveAccount.current !== activeAccount) {
      prevActiveAccount.current = activeAccount

      // Reset lazy loading state when account changes
      setHasMoreModelChat(true)

      // Reload all data with new account filter (full reload, not background refresh)
      Promise.allSettled([
        loadTradesData(),
        loadModelChatData(false), // false = full reload when switching accounts
        loadPositionsData()
      ])
    }
  }, [activeAccount, loadTradesData, loadModelChatData, loadPositionsData])

  // Fetch watchlist symbols and filter by current positions
  useEffect(() => {
    const fetchWatchlist = async () => {
      try {
        const response = await getHyperliquidWatchlist();
        const allSymbols = response.symbols || [];

        setSymbolOptions(allSymbols);
        if (selectedSymbol && !allSymbols.includes(selectedSymbol)) {
          setSelectedSymbol(null);
        }
      } catch (err) {
        console.error('Failed to fetch watchlist:', err);
        setSelectedSymbol(null);
      }
    };
    
    fetchWatchlist();
  }, [positions, activeAccount]);



  const accountOptions = useMemo(() => {
    return allTraderOptions.sort((a, b) => a.name.localeCompare(b.name))
  }, [allTraderOptions])

  const handleRefreshClick = () => {
    setManualRefreshKey((key) => key + 1)
  }

  const handleAccountFilterChange = (value: number | 'all') => {
    if (selectedAccountProp === undefined) {
      setInternalSelectedAccount(value)
    }
    onSelectedAccountChange?.(value)
    setExpandedChat(null)
    setExpandedSections({})

    // Data reload will be triggered by useEffect when activeAccount updates
  }

  const toggleSection = (entryId: number, section: 'prompt' | 'reasoning' | 'decision') => {
    const key = `${entryId}-${section}`
    setExpandedSections((prev) => ({
      ...prev,
      [key]: !prev[key],
    }))
  }

  const isSectionExpanded = (entryId: number, section: 'prompt' | 'reasoning' | 'decision') =>
    !!expandedSections[`${entryId}-${section}`]

  const handleCopySection = async (entryId: number, section: 'prompt' | 'reasoning' | 'decision', content: string) => {
    const key = `${entryId}-${section}`
    const success = await copyToClipboard(content)
    if (success) {
      setCopiedSections((prev) => ({ ...prev, [key]: true }))
      setTimeout(() => {
        setCopiedSections((prev) => ({ ...prev, [key]: false }))
      }, 2000)
    } else {
      console.error('Failed to copy')
    }
  }

  const isSectionCopied = (entryId: number, section: 'prompt' | 'reasoning' | 'decision') =>
    !!copiedSections[`${entryId}-${section}`]

  // Handle PnL data update
  const handleUpdatePnl = async () => {
    setUpdatingPnl(true)
    setPnlUpdateResult(null)
    try {
      const result = await updateArenaPnl()
      if (result.success) {
        // Calculate total updates across all environments
        let totalTrades = 0
        let totalDecisions = 0
        Object.values(result.environments).forEach((env) => {
          totalTrades += env.trades_updated
          totalDecisions += env.decisions_updated
        })
        setPnlUpdateResult(
          t('feed.pnlUpdateSuccess', 'Updated {{trades}} trades, {{decisions}} decisions', {
            trades: totalTrades,
            decisions: totalDecisions,
          })
        )
        // Refresh trades data to show updated values
        setManualRefreshKey((key) => key + 1)
      } else {
        setPnlUpdateResult(result.message || t('feed.pnlUpdateFailed', 'Update failed'))
      }
    } catch (err) {
      console.error('Failed to update PnL:', err)
      setPnlUpdateResult(t('feed.pnlUpdateError', 'Error updating PnL data'))
    } finally {
      setUpdatingPnl(false)
      // Clear result message after 5 seconds
      setTimeout(() => setPnlUpdateResult(null), 5000)
    }
  }

  // Load snapshots for a specific entry when expanded
  const loadSnapshots = useCallback(async (entryId: number) => {
    // Skip if already cached or loading
    if (snapshotCache.current.has(entryId) || loadingSnapshots.has(entryId)) {
      return
    }

    setLoadingSnapshots((prev) => new Set(prev).add(entryId))

    try {
      const snapshots = await getModelChatSnapshots(entryId)
      snapshotCache.current.set(entryId, snapshots)

      // Update the modelChat entry with snapshot data
      setModelChat((prev) =>
        prev.map((entry) =>
          entry.id === entryId
            ? {
                ...entry,
                prompt_snapshot: snapshots.prompt_snapshot,
                reasoning_snapshot: snapshots.reasoning_snapshot,
                decision_snapshot: snapshots.decision_snapshot,
              }
            : entry
        )
      )
    } catch (err) {
      console.error(`[AlphaArenaFeed] Failed to load snapshots for entry ${entryId}:`, err)
    } finally {
      setLoadingSnapshots((prev) => {
        const next = new Set(prev)
        next.delete(entryId)
        return next
      })
    }
  }, [loadingSnapshots])

  // Get snapshot data for an entry (from cache or entry itself)
  const getSnapshotData = useCallback((entry: ArenaModelChatEntry) => {
    const cached = snapshotCache.current.get(entry.id)
    return {
      prompt_snapshot: cached?.prompt_snapshot ?? entry.prompt_snapshot,
      reasoning_snapshot: cached?.reasoning_snapshot ?? entry.reasoning_snapshot,
      decision_snapshot: cached?.decision_snapshot ?? entry.decision_snapshot,
    }
  }, [])

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t('feed.filter', 'Filter')}</span>
          <select
            value={activeAccount === 'all' ? '' : activeAccount}
            onChange={(e) => {
              const value = e.target.value
              handleAccountFilterChange(value ? Number(value) : 'all')
            }}
            className="h-8 rounded border border-border bg-muted px-2 text-xs uppercase tracking-wide text-foreground"
          >
            <option value="">{t('feed.allTraders', 'All Traders')}</option>
            {accountOptions.map((meta) => (
              <option key={meta.account_id} value={meta.account_id}>
                {meta.name}{meta.model ? ` (${meta.model})` : ''}
              </option>
            ))}
          </select>
          <select
            value={selectedSymbol || ''}
            onChange={(e) => setSelectedSymbol(e.target.value || null)}
            className="h-8 rounded border border-border bg-muted px-2 text-xs uppercase tracking-wide text-foreground"
            disabled={symbolOptions.length === 0}
          >
            <option value="">{t('feed.allSymbols', 'All Symbols')}</option>
            {symbolOptions.map((sym) => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={handleRefreshClick} disabled={loadingTrades || loadingModelChat || loadingPositions}>
            {t('common.refresh', 'Refresh')}
          </Button>
        </div>
      </div>

      <Tabs
        value={activeTab}
        onValueChange={(value: FeedTab) => setActiveTab(value)}
        className="flex-1 flex flex-col min-h-0"
      >
        <TabsList className="grid grid-cols-3 gap-0 border border-border bg-muted text-foreground">
          <TabsTrigger value="trades" className="data-[state=active]:bg-background data-[state=active]:text-foreground border-r border-border text-[10px] md:text-xs">
            {t('feed.completedTrades', 'COMPLETED TRADES')}
          </TabsTrigger>
          <TabsTrigger value="model-chat" className="data-[state=active]:bg-background data-[state=active]:text-foreground border-r border-border text-[10px] md:text-xs">
            {t('feed.modelChat', 'MODELCHAT')}
          </TabsTrigger>
          <TabsTrigger value="positions" className="data-[state=active]:bg-background data-[state=active]:text-foreground text-[10px] md:text-xs">
            {t('feed.positions', 'POSITIONS')}
          </TabsTrigger>
        </TabsList>

        <div className="flex-1 border border-t-0 border-border bg-card min-h-0 flex flex-col overflow-hidden">
          {error && (
            <div className="p-4 text-sm text-red-500">
              {error}
            </div>
          )}

          {!error && (
            <>
              <TabsContent value="trades" className="flex-1 h-0 overflow-y-auto mt-0 p-4 space-y-4">
                {/* Action Buttons */}
                <div className="flex items-center justify-between gap-2 pb-2 border-b border-border">
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        if (onPageChange) {
                          onPageChange('attribution')
                          window.location.hash = 'attribution'
                        }
                      }}
                      className="text-xs"
                    >
                      {t('feed.attributionAnalysis', 'Attribution Analysis')}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setShowPnlConfirm(true)}
                      disabled={updatingPnl}
                      className="text-xs"
                    >
                      {updatingPnl ? (
                        <>
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          {t('feed.updatingPnl', 'Updating...')}
                        </>
                      ) : (
                        t('feed.updatePnl', 'Update PnL Data')
                      )}
                    </Button>
                  </div>
                  {pnlUpdateResult && (
                    <span className="text-xs text-muted-foreground">{pnlUpdateResult}</span>
                  )}
                </div>

                {/* PnL Update Confirmation Dialog */}
                <Dialog open={showPnlConfirm} onOpenChange={setShowPnlConfirm}>
                  <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                      <DialogTitle>{t('feed.confirmUpdatePnl', 'Confirm Update PnL Data')}</DialogTitle>
                      <DialogDescription>
                        {t('feed.confirmUpdatePnlDesc', 'This will fetch the latest fee and PnL data from Hyperliquid API, consuming 2 API calls (testnet + mainnet). Continue?')}
                      </DialogDescription>
                    </DialogHeader>
                    <DialogFooter className="gap-2 sm:gap-0">
                      <Button variant="outline" onClick={() => setShowPnlConfirm(false)}>
                        {t('common.cancel', 'Cancel')}
                      </Button>
                      <Button onClick={() => { setShowPnlConfirm(false); handleUpdatePnl(); }}>
                        {t('common.confirm', 'Confirm')}
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>

                {loadingTrades && trades.length === 0 ? (
                  <div className="text-xs text-muted-foreground">{t('feed.loadingTrades', 'Loading trades...')}</div>
                ) : trades.length === 0 ? (
                  <div className="text-xs text-muted-foreground">{t('feed.noTrades', 'No recent trades found.')}</div>
                ) : (
                  trades.map((trade) => {
                    const modelLogo = getModelLogo(trade.account_name || trade.model)
                    const isNew = !seenTradeIds.current.has(trade.trade_id)
                    if (!seenTradeIds.current.has(trade.trade_id)) {
                      seenTradeIds.current.add(trade.trade_id)
                    }
                    return (
                      <HighlightWrapper key={`${trade.trade_id}-${trade.trade_time}`} isNew={isNew}>
                        <div className="border border-border bg-muted/40 rounded px-4 py-3 space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wide text-muted-foreground">
                          <div className="flex items-center gap-2">
                            {modelLogo && (
                              <img
                                src={modelLogo.src}
                                alt={modelLogo.alt}
                                className="h-5 w-5 rounded-full object-contain bg-background"
                                loading="lazy"
                              />
                            )}
                            <span className="font-semibold text-foreground">{trade.account_name}</span>
                          </div>
                          <span>{formatDate(trade.trade_time)}</span>
                        </div>
                        <div className="text-sm text-foreground flex flex-wrap items-center gap-2">
                          <span className="font-semibold">{trade.account_name}</span>
                          <span>{t('feed.completedA', 'completed a')}</span>
                          <span className={`px-2 py-1 rounded text-xs font-bold ${
                            trade.side === 'BUY'
                              ? 'bg-emerald-100 text-emerald-800'
                              : trade.side === 'SELL'
                              ? 'bg-red-100 text-red-800'
                              : trade.side === 'CLOSE'
                              ? 'bg-blue-100 text-blue-800'
                              : trade.side === 'HOLD'
                              ? 'bg-gray-200 text-gray-800'
                              : 'bg-orange-100 text-orange-800'
                          }`}>
                            {trade.side}
                          </span>
                          <span>{t('feed.tradeOn', 'trade on')}</span>
                          <span className="font-semibold">{trade.symbol}</span>
                          <span>!</span>
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-muted-foreground">
                          <div>
                            <span className="block text-[10px] uppercase tracking-wide">{t('feed.price', 'Price')}</span>
                            <span className="font-medium text-foreground">
                              <FlipNumber value={trade.price} prefix="$" decimals={2} />
                            </span>
                          </div>
                          <div>
                            <span className="block text-[10px] uppercase tracking-wide">{t('feed.quantity', 'Quantity')}</span>
                            <span className="font-medium text-foreground">
                              <FlipNumber value={trade.quantity} decimals={4} />
                            </span>
                          </div>
                          <div>
                            <span className="block text-[10px] uppercase tracking-wide">{t('feed.notional', 'Notional')}</span>
                            <span className="font-medium text-foreground">
                              <FlipNumber value={trade.notional} prefix="$" decimals={2} />
                            </span>
                          </div>
                          <div>
                            <span className="block text-[10px] uppercase tracking-wide">{t('feed.commission', 'Commission')}</span>
                            <span className="font-medium text-foreground">
                              <FlipNumber value={trade.commission} prefix="$" decimals={2} />
                            </span>
                          </div>
                        </div>
                        {(trade.signal_trigger_id || trade.prompt_template_name) && (
                          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground pt-1 border-t border-border/50">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                              trade.signal_trigger_id
                                ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400'
                                : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
                            }`}>
                              {trade.signal_trigger_id
                                ? t('feed.signalPoolTrigger', 'Signal Pool')
                                : t('feed.scheduledTrigger', 'Scheduled')}
                            </span>
                            {trade.prompt_template_name && (
                              <span className="px-2 py-0.5 rounded bg-muted text-foreground font-medium">
                                {trade.prompt_template_name}
                              </span>
                            )}
                          </div>
                        )}
                        </div>
                      </HighlightWrapper>
                    )
                  })
                )}
              </TabsContent>

              <TabsContent value="model-chat" className="flex-1 h-0 overflow-y-auto mt-0 p-4 space-y-3">
                {loadingModelChat && modelChat.length === 0 ? (
                  <div className="text-xs text-muted-foreground">{t('feed.loadingModelChat', 'Loading model chat...')}</div>
                ) : modelChat.length === 0 ? (
                  <div className="text-xs text-muted-foreground">{t('feed.noModelChat', 'No recent AI commentary.')}</div>
                ) : (
                  <>
                  {modelChat.map((entry) => {
                    const isExpanded = expandedChat === entry.id
                    const modelLogo = getModelLogo(entry.account_name || entry.model)
                    const isNew = !seenDecisionIds.current.has(entry.id)
                    if (!seenDecisionIds.current.has(entry.id)) {
                      seenDecisionIds.current.add(entry.id)
                    }

                    return (
                      <HighlightWrapper key={entry.id} isNew={isNew}>
                        <button
                          type="button"
                          className="w-full text-left border border-border rounded bg-muted/30 p-4 space-y-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                          onClick={() =>
                            setExpandedChat((current) => {
                              const next = current === entry.id ? null : entry.id
                              if (current === entry.id) {
                                setExpandedSections((prev) => {
                                  const nextState = { ...prev }
                                  Object.keys(nextState).forEach((key) => {
                                    if (key.startsWith(`${entry.id}-`)) {
                                      delete nextState[key]
                                    }
                                  })
                                  return nextState
                                })
                              } else {
                                // Load snapshots when expanding
                                loadSnapshots(entry.id)
                              }
                              return next
                            })
                          }
                        >
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wide text-muted-foreground">
                          <div className="flex items-center gap-2">
                            {modelLogo && (
                              <img
                                src={modelLogo.src}
                                alt={modelLogo.alt}
                                className="h-5 w-5 rounded-full object-contain bg-background"
                                loading="lazy"
                              />
                            )}
                            <span className="font-semibold text-foreground">{entry.account_name}</span>
                          </div>
                          <span>{formatDate(entry.decision_time)}</span>
                        </div>
                        <div className="text-sm font-medium text-foreground flex items-center gap-2">
                          <span className={`px-2 py-1 rounded text-xs font-bold ${
                            entry.operation?.toUpperCase() === 'BUY'
                              ? 'bg-emerald-100 text-emerald-800'
                              : entry.operation?.toUpperCase() === 'SELL'
                              ? 'bg-red-100 text-red-800'
                              : entry.operation?.toUpperCase() === 'CLOSE'
                              ? 'bg-blue-100 text-blue-800'
                              : entry.operation?.toUpperCase() === 'HOLD'
                              ? 'bg-gray-200 text-gray-800'
                              : 'bg-orange-100 text-orange-800'
                          }`}>
                            {(entry.operation || 'UNKNOWN').toUpperCase()}
                          </span>
                          {entry.symbol && (
                            <span className="font-semibold">{entry.symbol}</span>
                          )}
                          <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                            entry.signal_trigger_id
                              ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400'
                              : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
                          }`}>
                            {entry.signal_trigger_id
                              ? t('feed.signalPoolTrigger', 'Signal Pool')
                              : t('feed.scheduledTrigger', 'Scheduled')}
                          </span>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {isExpanded ? entry.reason : `${entry.reason.slice(0, 160)}${entry.reason.length > 160 ? '…' : ''}`}
                        </div>
                        {isExpanded && (
                          <div className="space-y-2 pt-3">
                            {entry.prompt_template_name && (
                              <div className="flex items-center gap-2 text-xs text-muted-foreground pb-1">
                                <span className="font-medium">{t('feed.promptTemplate', 'Prompt Template')}:</span>
                                <span className="px-2 py-0.5 rounded bg-muted text-foreground font-medium">{entry.prompt_template_name}</span>
                              </div>
                            )}
                            {(() => {
                              const snapshots = getSnapshotData(entry)
                              const isLoadingEntry = loadingSnapshots.has(entry.id)
                              return [{
                                label: t('feed.userPrompt', 'USER PROMPT'),
                                section: 'prompt' as const,
                                content: snapshots.prompt_snapshot,
                                empty: t('feed.noPrompt', 'No prompt available'),
                              }, {
                                label: t('feed.chainOfThought', 'CHAIN OF THOUGHT'),
                                section: 'reasoning' as const,
                                content: snapshots.reasoning_snapshot,
                                empty: t('feed.noReasoning', 'No reasoning available'),
                              }, {
                                label: t('feed.tradingDecisions', 'TRADING DECISIONS'),
                                section: 'decision' as const,
                                content: snapshots.decision_snapshot,
                                empty: t('feed.noDecision', 'No decision payload available'),
                              }].map(({ label, section, content, empty }) => {
                              const open = isSectionExpanded(entry.id, section)
                              const displayContent = content?.trim()
                              const copied = isSectionCopied(entry.id, section)
                              const showLoading = isLoadingEntry && !displayContent
                              
                              return (
                                <div key={section} className="border border-border/60 rounded-md bg-background/60">
                                  <button
                                    type="button"
                                    className="flex w-full items-center justify-between px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
                                    onClick={(event) => {
                                      event.stopPropagation()
                                      toggleSection(entry.id, section)
                                    }}
                                  >
                                    <span className="flex items-center gap-2">
                                      <span className="text-xs">{open ? '▼' : '▶'}</span>
                                      {label}
                                    </span>
                                    <span className="text-[10px] text-muted-foreground/80">{open ? t('feed.hideDetails', 'Hide details') : t('feed.showDetails', 'Show details')}</span>
                                  </button>
                                  {open && (
                                    <div
                                      className="border-t border-border/40 bg-muted/40 px-3 py-3 text-xs text-muted-foreground"
                                      onClick={(event) => event.stopPropagation()}
                                    >
                                      {showLoading ? (
                                        <div className="flex items-center gap-2 text-muted-foreground/70">
                                          <Loader2 className="w-3 h-3 animate-spin" />
                                          <span>{t('feed.loading', 'Loading...')}</span>
                                        </div>
                                      ) : displayContent ? (
                                        <>
                                          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-foreground/90">
                                            {displayContent}
                                          </pre>
                                          <div className="mt-3 flex justify-end">
                                            <button
                                              type="button"
                                              onClick={(e) => {
                                                e.stopPropagation()
                                                if (displayContent) {
                                                  handleCopySection(entry.id, section, displayContent)
                                                }
                                              }}
                                              className={`px-3 py-1.5 text-[10px] font-medium rounded transition-all ${
                                                copied
                                                  ? 'bg-emerald-500/20 text-emerald-600 border border-emerald-500/30'
                                                  : 'bg-muted/60 text-muted-foreground hover:bg-muted hover:text-foreground border border-border/60'
                                              }`}
                                            >
                                              {copied ? `✓ ${t('feed.copied', 'Copied')}` : t('feed.copy', 'Copy')}
                                            </button>
                                          </div>
                                        </>
                                      ) : (
                                        <span className="text-muted-foreground/70">{empty}</span>
                                      )}
                                    </div>
                                  )}
                                </div>
                              )
                            })
                            })()}
                          </div>
                        )}
                        <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground uppercase tracking-wide">
                          <span>{t('feed.prevPortion', 'Prev Portion')}: <span className="font-semibold text-foreground">{(entry.prev_portion * 100).toFixed(1)}%</span></span>
                          <span>{t('feed.targetPortion', 'Target Portion')}: <span className="font-semibold text-foreground">{(entry.target_portion * 100).toFixed(1)}%</span></span>
                          <span>{t('feed.totalBalance', 'Total Balance')}: <span className="font-semibold text-foreground">
                            <FlipNumber value={entry.total_balance} prefix="$" decimals={2} />
                          </span></span>
                          <span>{t('feed.executed', 'Executed')}: <span className={`font-semibold ${entry.executed ? 'text-emerald-600' : 'text-amber-600'}`}>{entry.executed ? 'YES' : 'NO'}</span></span>
                        </div>
                        <div className="mt-2 text-[11px] text-primary underline">
                          {isExpanded ? t('feed.clickCollapse', 'Click to collapse') : t('feed.clickExpand', 'Click to expand')}
                        </div>
                        </button>
                      </HighlightWrapper>
                    )
                  })}

                  {/* Load More Button */}
                  {hasMoreModelChat && (
                    <div className="flex justify-center pt-4">
                      <Button
                        onClick={loadMoreModelChat}
                        disabled={isLoadingMoreModelChat}
                        variant="outline"
                        size="sm"
                        className="text-xs"
                      >
                        {isLoadingMoreModelChat ? (
                          <>
                            <Loader2 className="w-3 h-3 mr-2 animate-spin" />
                            {t('feed.loading', 'Loading...')}
                          </>
                        ) : (
                          t('feed.loadMore', 'Load More History')
                        )}
                      </Button>
                    </div>
                  )}

                  {!hasMoreModelChat && modelChat.length > 0 && (
                    <div className="flex justify-center pt-4 text-xs text-muted-foreground">
                      {t('feed.allLoaded', 'All history loaded')}
                    </div>
                  )}
                  </>
                )}
              </TabsContent>

              <TabsContent value="positions" className="flex-1 h-0 overflow-y-auto mt-0 p-4 space-y-4">
                {loadingPositions && positions.length === 0 ? (
                  <div className="text-xs text-muted-foreground">{t('feed.loadingPositions', 'Loading positions...')}</div>
                ) : positions.length === 0 ? (
                  <div className="text-xs text-muted-foreground">{t('feed.noPositions', 'No active positions currently.')}</div>
                ) : (
                  positions.map((snapshot) => {
                    const marginUsageClass =
                      snapshot.margin_usage_percent !== undefined && snapshot.margin_usage_percent !== null
                        ? snapshot.margin_usage_percent >= 75
                          ? 'text-red-600'
                          : snapshot.margin_usage_percent >= 50
                            ? 'text-amber-600'
                            : 'text-emerald-600'
                        : 'text-muted-foreground'
                    return (
                      <div key={snapshot.account_id} className="border border-border rounded bg-muted/40">
                        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
                          <div className="flex items-center gap-3">
                            <div className="text-sm font-semibold uppercase tracking-wide text-foreground">
                              {snapshot.account_name}
                            </div>
                            {snapshot.environment && (
                              <span className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                                {snapshot.environment}
                              </span>
                            )}
                          </div>
                          <div className="flex flex-wrap items-center gap-4 text-xs uppercase tracking-wide text-muted-foreground">
                            <div>
                              <span className="block text-[10px] text-muted-foreground">{t('feed.totalEquity', 'Total Equity')}</span>
                              <span className="font-semibold text-foreground">
                                <FlipNumber value={snapshot.total_assets} prefix="$" decimals={2} />
                              </span>
                            </div>
                            <div>
                              <span className="block text-[10px] text-muted-foreground">{t('feed.availableCash', 'Available Cash')}</span>
                              <span className="font-semibold text-foreground">
                                <FlipNumber value={snapshot.available_cash} prefix="$" decimals={2} />
                              </span>
                            </div>
                            <div>
                              <span className="block text-[10px] text-muted-foreground">{t('feed.usedMargin', 'Used Margin')}</span>
                              <span className="font-semibold text-foreground">
                                <FlipNumber value={snapshot.used_margin ?? 0} prefix="$" decimals={2} />
                              </span>
                            </div>
                            <div>
                              <span className="block text-[10px] text-muted-foreground">{t('feed.marginUsage', 'Margin Usage')}</span>
                              <span className={`font-semibold ${marginUsageClass}`}>
                                {snapshot.margin_usage_percent !== undefined && snapshot.margin_usage_percent !== null
                                  ? `${snapshot.margin_usage_percent.toFixed(2)}%`
                                  : '—'}
                              </span>
                            </div>
                            <div>
                              <span className="block text-[10px] text-muted-foreground">{t('feed.unrealizedPnl', 'Unrealized P&L')}</span>
                              <span className={`font-semibold ${snapshot.total_unrealized_pnl >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                <FlipNumber value={snapshot.total_unrealized_pnl} prefix="$" decimals={2} />
                              </span>
                            </div>
                            <div>
                              <span className="block text-[10px] text-muted-foreground">{t('feed.totalReturn', 'Total Return')}</span>
                              <span className={`font-semibold ${snapshot.total_return && snapshot.total_return >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                {formatPercent(snapshot.total_return)}
                              </span>
                            </div>
                          </div>
                        </div>
                        <div className="overflow-x-auto">
                          <table className="min-w-[980px] divide-y divide-border">
                            <thead className="bg-muted/50">
                              <tr className="text-[11px] uppercase tracking-wide text-muted-foreground">
                                <th className="px-4 py-2 text-left">{t('feed.side', 'Side')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.coin', 'Coin')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.size', 'Size')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.entryCurrent', 'Entry / Current')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.leverage', 'Leverage')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.marginUsedCol', 'Margin Used')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.notional', 'Notional')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.currentValue', 'Current Value')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.unrealizedPnl', 'Unreal P&L')}</th>
                                <th className="px-4 py-2 text-left">{t('feed.portfolioPercent', 'Portfolio %')}</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-border text-xs text-muted-foreground">
                              {snapshot.positions.map((position, idx) => {
                                const leverageLabel =
                                  position.leverage && position.leverage > 0
                                    ? `${position.leverage.toFixed(2)}x`
                                    : '—'
                                const marginUsed = position.margin_used ?? 0
                                const roePercent =
                                  position.return_on_equity !== undefined && position.return_on_equity !== null
                                    ? position.return_on_equity * 100
                                    : null
                                const portfolioPercent =
                                  position.percentage !== undefined && position.percentage !== null
                                    ? position.percentage * 100
                                    : null
                                const unrealizedDecimals =
                                  Math.abs(position.unrealized_pnl) < 1 ? 4 : 2
                                return (
                                  <tr key={`${position.symbol}-${idx}`}>
                                    <td className="px-4 py-2 font-semibold text-foreground">{position.side}</td>
                                    <td className="px-4 py-2">
                                      <div className="font-semibold text-foreground">
                                        {position.symbol}
                                      </div>
                                      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{position.market}</div>
                                    </td>
                                    <td className="px-4 py-2">
                                      <FlipNumber value={position.quantity} decimals={4} />
                                    </td>
                                    <td className="px-4 py-2">
                                      <div className="text-foreground font-semibold">
                                        <FlipNumber value={position.avg_cost} prefix="$" decimals={2} />
                                      </div>
                                      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                                        <FlipNumber value={position.current_price} prefix="$" decimals={2} />
                                      </div>
                                    </td>
                                    <td className="px-4 py-2">{leverageLabel}</td>
                                    <td className="px-4 py-2">
                                      <FlipNumber value={marginUsed} prefix="$" decimals={2} />
                                    </td>
                                    <td className="px-4 py-2">
                                      <FlipNumber value={position.notional} prefix="$" decimals={2} />
                                    </td>
                                    <td className="px-4 py-2">
                                      <FlipNumber value={position.current_value} prefix="$" decimals={2} />
                                    </td>
                                    <td className={`px-4 py-2 font-semibold ${position.unrealized_pnl >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                      <div>
                                        <FlipNumber value={position.unrealized_pnl} prefix="$" decimals={unrealizedDecimals} />
                                      </div>
                                      {roePercent !== null && (
                                        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                                          {roePercent.toFixed(2)}%
                                        </div>
                                      )}
                                    </td>
                                    <td className="px-4 py-2">
                                      {portfolioPercent !== null ? `${portfolioPercent.toFixed(2)}%` : '—'}
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )
                  })
                )}
              </TabsContent>
            </>
          )}
        </div>
      </Tabs>
    </div>
  )
}
