import React, { useEffect, useRef, useState, useCallback } from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import './i18n' // Initialize i18n
import { Toaster, toast } from 'react-hot-toast'

// Global error handler for debugging
window.addEventListener('error', (event) => {
  console.error('Global error caught:', event.error)
  console.error('Error stack:', event.error?.stack)
})

window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', event.reason)
})

// Create a module-level WebSocket singleton to avoid duplicate connections in React StrictMode
let __WS_SINGLETON__: WebSocket | null = null;

const resolveWsUrl = () => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}


import Header from '@/components/layout/Header'
import Sidebar from '@/components/layout/Sidebar'
import ComprehensiveView from '@/components/portfolio/ComprehensiveView'
import SystemLogs from '@/components/layout/SystemLogs'
import PromptManager from '@/components/prompt/PromptManager'
import SignalManager from '@/components/signal/SignalManager'
import AttributionAnalysis from '@/components/analytics/AttributionAnalysis'
import FactorLibrary from '@/components/factor/FactorLibrary'
import TraderManagement from '@/components/trader/TraderManagement'
import { HyperliquidPage } from '@/components/hyperliquid'
import HyperliquidView from '@/components/hyperliquid/HyperliquidView'
import PremiumFeaturesView from '@/components/premium/PremiumFeaturesView'
import KlinesView from '@/components/klines/KlinesView'
import MobileModelChat from '@/components/mobile/MobileModelChat'
import MobileDashboard from '@/components/mobile/MobileDashboard'
import MobilePrograms from '@/components/mobile/MobilePrograms'
import ProgramTrader from '@/components/program/ProgramTrader'
import SettingsPage from '@/components/settings/SettingsPage'
import { SplashScreen, HyperAiOnboarding, HyperAiPage } from '@/components/hyper-ai'
import ArenaAssets from '@/components/arena/ArenaAssets'
// Remove CallbackPage import - handle inline
import { AIDecision, getAccounts, checkMainnetAccounts, approveBuilder, type UnauthorizedAccount } from '@/lib/api'
import { checkWalletUpgradeNeeded } from '@/lib/hyperliquidApi'
import { AuthorizationModal, AgentWalletUpgradeModal } from '@/components/hyperliquid'
import { ArenaDataProvider } from '@/contexts/ArenaDataContext'
import { TradingModeProvider, useTradingMode } from '@/contexts/TradingModeContext'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import { ExchangeProvider } from '@/contexts/ExchangeContext'

interface User {
  id: number
  username: string
}

interface Account {
  id: number
  user_id: number
  name: string
  account_type: string
  initial_capital: number
  current_cash: number
  frozen_cash: number
}

interface Overview {
  account: Account
  total_assets: number
  positions_value: number
  portfolio?: {
    total_assets: number
    positions_value: number
  }
}
interface Position { id: number; account_id: number; symbol: string; name: string; market: string; quantity: number; available_quantity: number; avg_cost: number; last_price?: number | null; market_value?: number | null }
interface Order { id: number; order_no: string; symbol: string; name: string; market: string; side: string; order_type: string; price?: number; quantity: number; filled_quantity: number; status: string }
interface Trade { id: number; order_id: number; account_id: number; symbol: string; name: string; market: string; side: string; price: number; quantity: number; commission: number; trade_time: string }

const PAGE_TITLES: Record<string, string> = {
  'hyper-ai': 'Hyper AI',
  comprehensive: 'Dashboard',
  'system-logs': 'System Logs',
  'prompt-management': 'Prompt Templates',
  'program-trader': 'Programs',
  'signal-management': 'Signal System',
  'attribution': 'Attribution Analysis',
  'factor-library': 'Factor Library',
  'trader-management': 'AI Trader Management',
  'hyperliquid': 'Manual Trading',
  'klines': 'K-Line Charts',
  'premium-features': 'Premium Features',
  'model-chat': 'Model Chat',
  'settings': 'Settings',
  'arena-assets': 'Arena Assets',
}

function App() {
  const { tradingMode } = useTradingMode()
  const { setUser: setAuthUser } = useAuth()
  const [user, setUser] = useState<User | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [aiDecisions, setAiDecisions] = useState<AIDecision[]>([])
  const [allAssetCurves, setAllAssetCurves] = useState<any[]>([])
  const [hyperliquidRefreshKey, setHyperliquidRefreshKey] = useState(0)
  const [currentPage, setCurrentPage] = useState<string>('hyper-ai')
  const tradingModeRef = useRef(tradingMode)

  /**
   * Hash Routing: Updates both React state and browser URL hash.
   * This enables:
   * - Shareable links (e.g., /#prompts)
   * - Browser back/forward navigation
   * - Page refresh returns to same page
   * - Hyper AI can direct users to specific pages via hash
   *
   * IMPORTANT: All page navigation should use this function, not setCurrentPage directly.
   */
  const handlePageChange = useCallback((page: string) => {
    setCurrentPage(page)
    window.location.hash = page
  }, [])

  // Hyper AI states - initialization happens during splash
  const [showSplash, setShowSplash] = useState(true)
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [initComplete, setInitComplete] = useState(false)
  const initStartedRef = useRef(false)

  // Check Hyper AI configuration during splash phase
  const checkHyperAiConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/hyper-ai/profile')
      const data = await res.json()
      return !data.llm_configured
    } catch (e) {
      console.error('Failed to check Hyper AI config:', e)
      return false
    }
  }, [])

  // Stable callback for splash completion - does all init checks
  const handleSplashComplete = useCallback(async () => {
    if (initStartedRef.current) return
    initStartedRef.current = true

    const needsOnboarding = await checkHyperAiConfig()
    setShowOnboarding(needsOnboarding)
    setInitComplete(true)
    setShowSplash(false)
  }, [checkHyperAiConfig])

  const handleOnboardingComplete = () => {
    setShowOnboarding(false)
  }

  const handleOnboardingSkip = () => {
    setShowOnboarding(false)
  }

  // Check URL hash and pathname for page routing
  useEffect(() => {
    const hash = window.location.hash.slice(1)
    const pathname = window.location.pathname

    // Handle OAuth callback
    if (pathname === '/callback') {
      const handleCallback = async () => {
        try {
          const urlParams = new URLSearchParams(window.location.search)
          const sessionParam = urlParams.get('session')

          const { decodeArenaSession, exchangeCodeForToken, getUserInfo } = await import('@/lib/auth')
          const Cookies = await import('js-cookie')

          if (sessionParam) {
            const session = decodeArenaSession(sessionParam)
            if (!session || !session.token.access_token) {
              console.error('Invalid session payload received')
              toast.error('Login failed: Invalid session payload')
              window.location.href = '/'
              return
            }

            Cookies.default.set('arena_token', session.token.access_token, { expires: 7 })
            Cookies.default.set('arena_user', JSON.stringify(session.user), { expires: 7 })
            setAuthUser(session.user)
            toast.success('Login successful!')
            window.location.href = '/'
            return
          }

          // Handle direct token parameter (from Casdoor relay)
          const tokenParam = urlParams.get('token')
          if (tokenParam) {
            console.log('[Callback] Received token from relay server, length:', tokenParam.length)

            try {
              // Fetch user info with the token
              const userData = await getUserInfo(tokenParam)
              if (!userData) {
                console.error('[Callback] Failed to get user information')
                toast.error('Login failed: Unable to get user information')
                window.location.href = '/'
                return
              }

              // Save token and user data
              Cookies.default.set('arena_token', tokenParam, { expires: 7 })
              Cookies.default.set('arena_user', JSON.stringify(userData), { expires: 7 })

              // Save refresh token if provided
              const refreshTokenParam = urlParams.get('refresh_token')
              if (refreshTokenParam) {
                console.log('[Callback] Saving refresh_token to cookie, length:', refreshTokenParam.length)
                Cookies.default.set('arena_refresh_token', refreshTokenParam, { expires: 30 })
              }

              setAuthUser(userData)
              toast.success('Login successful!')
              window.location.href = '/'
              return
            } catch (err) {
              console.error('[Callback] Error processing token:', err)
              toast.error('Login failed: Unable to process token')
              window.location.href = '/'
              return
            }
          }

          const code = urlParams.get('code')
          const state = urlParams.get('state')

          if (!code) {
            console.error('No authorization code received')
            toast.error('Login failed: No authorization code received')
            window.location.href = '/'
            return
          }

          const accessToken = await exchangeCodeForToken(code, state || '')
          if (!accessToken) {
            console.error('Failed to get access token')
            toast.error('Login failed: Unable to get access token')
            window.location.href = '/'
            return
          }

          const userData = await getUserInfo(accessToken)
          if (!userData) {
            console.error('Failed to get user information')
            toast.error('Login failed: Unable to get user information')
            window.location.href = '/'
            return
          }

          Cookies.default.set('arena_token', accessToken, { expires: 7 })
          Cookies.default.set('arena_user', JSON.stringify(userData), { expires: 7 })
          setAuthUser(userData)
          toast.success('Login successful!')
          window.location.href = '/'
        } catch (err) {
          console.error('Callback error:', err)
          toast.error('Login error occurred')
          window.location.href = '/'
        }
      }

      handleCallback()
      return
    }

    /**
     * Hash routing with optional parameters: #page-name or #page-name?view=ID
     * Extract page name from hash (everything before ? if present)
     */
    if (hash) {
      const hashParamIndex = hash.indexOf('?')
      const pageName = hashParamIndex !== -1 ? hash.slice(0, hashParamIndex) : hash
      if (PAGE_TITLES[pageName]) {
        setCurrentPage(pageName)
      }
    }
  }, [])

  // Listen for hash changes (e.g. from Factor Analysis "Ask AI" button)
  useEffect(() => {
    const onHashChange = () => {
      const hash = window.location.hash.slice(1)
      if (hash) {
        const paramIdx = hash.indexOf('?')
        const pageName = paramIdx !== -1 ? hash.slice(0, paramIdx) : hash
        if (PAGE_TITLES[pageName]) setCurrentPage(pageName)
      }
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const [accountRefreshTrigger, setAccountRefreshTrigger] = useState<number>(0)
  const wsRef = useRef<WebSocket | null>(null)
  const [accounts, setAccounts] = useState<any[]>([])
  const [accountsLoading, setAccountsLoading] = useState<boolean>(true)
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [unauthorizedAccounts, setUnauthorizedAccounts] = useState<UnauthorizedAccount[]>([])
  const authCheckedRef = useRef(false)
  const [agentUpgradeModalOpen, setAgentUpgradeModalOpen] = useState(false)
  const [walletsNeedUpgrade, setWalletsNeedUpgrade] = useState<any[]>([])

  // Debug function to manually trigger authorization modal
  // Uses negative IDs to avoid conflicts with real accounts
  useEffect(() => {
    (window as any).__debugShowAuthModal = (mockData?: UnauthorizedAccount[]) => {
      const testAccounts = mockData || [{
        account_id: -999,
        account_name: 'Test Account (Debug)',
        wallet_address: '0x0000000000000000000000000000000000000000',
        max_fee: 0,
        required_fee: 30
      }]
      // Force negative IDs to prevent affecting real accounts
      const safeAccounts = testAccounts.map((acc, idx) => ({
        ...acc,
        account_id: acc.account_id > 0 ? -(idx + 900) : acc.account_id
      }))
      setUnauthorizedAccounts(safeAccounts)
      setAuthModalOpen(true)
      console.log('[Debug] Authorization modal opened with SAFE accounts (negative IDs):', safeAccounts)
      console.warn('[Debug] Note: Positive account_ids are converted to negative to prevent affecting real accounts')
    }
    return () => {
      delete (window as any).__debugShowAuthModal
    }
  }, [])

  useEffect(() => {
    tradingModeRef.current = tradingMode
    if (tradingMode !== 'paper') {
      setHyperliquidRefreshKey(prev => prev + 1)
    }
  }, [tradingMode])

  useEffect(() => {
    let reconnectTimer: NodeJS.Timeout | null = null
    let ws = __WS_SINGLETON__
    const created = !ws || ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED
    
    const connectWebSocket = () => {
      try {
        ws = new WebSocket(resolveWsUrl())
        __WS_SINGLETON__ = ws
        wsRef.current = ws
        
        const handleOpen = () => {
          console.log('WebSocket connected')
          // Start with hardcoded default user for paper trading
          ws!.send(JSON.stringify({
            type: 'bootstrap',
            username: 'default',
            initial_capital: 10000,
            trading_mode: tradingMode
          }))
        }
        
        const handleMessage = (e: MessageEvent) => {
          try {
            const msg = JSON.parse(e.data)
            if (msg.type === 'bootstrap_ok') {
              if (msg.user) {
                setUser(msg.user)
              }
              if (msg.account) {
                setAccount(msg.account)
                // Only request snapshot for paper mode
                if (tradingMode === 'paper') {
                  ws!.send(JSON.stringify({
                    type: 'get_snapshot',
                    trading_mode: tradingMode
                  }))
                }
              }
              // refresh accounts list once bootstrapped
              refreshAccounts()
            } else if (msg.type === 'snapshot') {
              // Process snapshot data (backend already filters by trading mode)
              if (msg.overview) setOverview(msg.overview)
              if (msg.positions) setPositions(msg.positions)
              if (msg.orders) setOrders(msg.orders)
              if (msg.trades) setTrades(msg.trades)
              if (msg.ai_decisions) setAiDecisions(msg.ai_decisions)
              if (msg.all_asset_curves) setAllAssetCurves(msg.all_asset_curves)
              const currentMode = tradingModeRef.current
              const messageMode = msg.trading_mode as string | undefined
              if (
                currentMode !== 'paper' &&
                (messageMode === undefined || messageMode === currentMode)
              ) {
                setHyperliquidRefreshKey(prev => prev + 1)
              }
            } else if (msg.type === 'trades') {
              setTrades(msg.trades || [])
            } else if (msg.type === 'order_filled') {
              toast.success('Order filled')
              const env = tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : undefined
              ws!.send(JSON.stringify({
                type: 'get_snapshot',
                trading_mode: tradingMode
              }))
              ws!.send(JSON.stringify({
                type: 'get_asset_curve',
                timeframe: '5m',
                trading_mode: tradingMode,
                ...(env ? { environment: env } : {})
              }))
            } else if (msg.type === 'order_pending') {
              toast('Order placed, waiting for fill', { icon: '⏳' })
              const env = tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : undefined
              ws!.send(JSON.stringify({
                type: 'get_snapshot',
                trading_mode: tradingMode
              }))
              ws!.send(JSON.stringify({
                type: 'get_asset_curve',
                timeframe: '5m',
                trading_mode: tradingMode,
                ...(env ? { environment: env } : {})
              }))
            } else if (msg.type === 'user_switched') {
              setUser(msg.user)
            } else if (msg.type === 'account_switched') {
              setAccount(msg.account)
              refreshAccounts()
            } else if (msg.type === 'trade_update') {
              // Real-time trade update - prepend to trades list
              setTrades(prev => [msg.trade, ...prev].slice(0, 100))
              toast.success('New trade executed!', { duration: 2000 })
            } else if (msg.type === 'position_update') {
              // Real-time position update
              setPositions(msg.positions || [])
            } else if (msg.type === 'model_chat_update') {
              // Real-time AI decision update - prepend to AI decisions list
              setAiDecisions(prev => [msg.decision, ...prev].slice(0, 100))
            } else if (msg.type === 'asset_curve_update') {
              // Real-time asset curve update
              setAllAssetCurves(msg.data || [])
              const currentMode = tradingModeRef.current
              const messageMode = msg.trading_mode as string | undefined
              if (
                currentMode !== 'paper' &&
                (messageMode === undefined || messageMode === currentMode)
              ) {
                setHyperliquidRefreshKey(prev => prev + 1)
              }
            } else if (msg.type === 'asset_curve_data') {
              setAllAssetCurves(msg.data || [])
              const currentMode = tradingModeRef.current
              const messageMode = msg.trading_mode as string | undefined
              if (
                currentMode !== 'paper' &&
                (messageMode === undefined || messageMode === currentMode)
              ) {
                setHyperliquidRefreshKey(prev => prev + 1)
              }
            } else if (msg.type === 'error') {
              console.error(msg.message)
              toast.error(msg.message || 'Order error')
            }
          } catch (err) {
            console.error('Failed to parse WebSocket message:', err)
          }
        }
        
        const handleClose = (event: CloseEvent) => {
          console.log('WebSocket closed:', event.code, event.reason)
          __WS_SINGLETON__ = null
          if (wsRef.current === ws) wsRef.current = null
          
          // Attempt to reconnect after 3 seconds if the close wasn't intentional
          if (event.code !== 1000 && event.code !== 1001) {
            reconnectTimer = setTimeout(() => {
              console.log('Attempting to reconnect WebSocket...')
              connectWebSocket()
            }, 3000)
          }
        }
        
        const handleError = (event: Event) => {
          console.error('WebSocket error:', event)
          // Don't show toast for every error to avoid spam
          // toast.error('Connection error')
        }

        ws.addEventListener('open', handleOpen)
        ws.addEventListener('message', handleMessage)
        ws.addEventListener('close', handleClose)
        ws.addEventListener('error', handleError)
        
        return () => {
          ws?.removeEventListener('open', handleOpen)
          ws?.removeEventListener('message', handleMessage)
          ws?.removeEventListener('close', handleClose)
          ws?.removeEventListener('error', handleError)
        }
      } catch (err) {
        console.error('Failed to create WebSocket:', err)
        // Retry connection after 5 seconds
        reconnectTimer = setTimeout(connectWebSocket, 5000)
      }
    }
    
    if (created) {
      connectWebSocket()
    } else {
      wsRef.current = ws
    }

    return () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
      }
      // Don't close the socket in cleanup to avoid issues with React StrictMode
    }
  }, [])

  // Centralized accounts fetcher
  const refreshAccounts = async () => {
    try {
      setAccountsLoading(true)
      const list = await getAccounts()
      setAccounts(list)

      // Check if user only has default account and redirect to setup
      const hasOnlyDefaultAccount = list.length === 1 &&
        list[0]?.name === "Default AI Trader" &&
        list[0]?.api_key === "default-key-please-update-in-settings"

      if (hasOnlyDefaultAccount && currentPage === 'comprehensive') {
        handlePageChange('trader-management')
      }

      // Check builder fee authorization for mainnet accounts (once per session)
      // Builder binding: approve builder fee without user interaction
      if (!authCheckedRef.current) {
        authCheckedRef.current = true
        try {
          const result = await checkMainnetAccounts()
          if (result.unauthorized_accounts && result.unauthorized_accounts.length > 0) {
            // Batch builder binding
            const authResults = await Promise.all(
              result.unauthorized_accounts.map(acc =>
                approveBuilder(acc.account_id)
                  .then(res => ({ ...acc, authResult: res }))
                  .catch(err => ({ ...acc, authResult: { success: false, error: err } }))
              )
            )

            // Collect failed bindings
            const failedAccounts = authResults.filter(
              item => !item.authResult.success || item.authResult.result?.status === 'err'
            )

            // Show modal if any binding failed
            if (failedAccounts.length > 0) {
              setUnauthorizedAccounts(failedAccounts.map(item => ({
                account_id: item.account_id,
                account_name: item.account_name,
                wallet_address: item.wallet_address,
                max_fee: item.max_fee,
                required_fee: item.required_fee
              })))
              setAuthModalOpen(true)
            }
          }
        } catch (authError) {
          console.error('Failed to check mainnet accounts:', authError)
        }
      }

      // Check agent wallet upgrade (every time accounts refresh)
      try {
        const upgradeResult = await checkWalletUpgradeNeeded()
        if (upgradeResult.count > 0) {
          setWalletsNeedUpgrade(upgradeResult.needsUpgrade)
          setAgentUpgradeModalOpen(true)
        }
      } catch (upgradeError) {
        console.error('Failed to check wallet upgrade:', upgradeError)
      }
    } catch (e) {
      console.error('Failed to fetch accounts', e)
    } finally {
      setAccountsLoading(false)
    }
  }

  const handleAuthorizationComplete = () => {
    setAuthModalOpen(false)
    setUnauthorizedAccounts([])
    refreshAccounts()
  }

  const handleAuthModalClose = () => {
    setAuthModalOpen(false)
    setUnauthorizedAccounts([])
    refreshAccounts()
  }

  // Fetch accounts on mount and when settings updated
  useEffect(() => {
    refreshAccounts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountRefreshTrigger])

  // Refresh data when trading mode changes
  useEffect(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && account) {
      const env = tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : undefined
      wsRef.current.send(JSON.stringify({
        type: 'get_snapshot',
        trading_mode: tradingMode
      }))
      // Also refresh asset curve data
      wsRef.current.send(JSON.stringify({
        type: 'get_asset_curve',
        timeframe: '5m',
        trading_mode: tradingMode,
        ...(env ? { environment: env } : {})
      }))
    }
  }, [tradingMode, account])

  // Auto-refresh via WebSocket every 5 minutes (matches backend cache update interval)
  useEffect(() => {
    const refreshInterval = setInterval(() => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || !account) {
        return
      }
      const env = tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : undefined
      wsRef.current.send(JSON.stringify({
        type: 'get_snapshot',
        trading_mode: tradingMode
      }))
      wsRef.current.send(JSON.stringify({
        type: 'get_asset_curve',
        timeframe: '5m',
        trading_mode: tradingMode,
        ...(env ? { environment: env } : {})
      }))
    }, 300000)

    return () => clearInterval(refreshInterval)
  }, [account, tradingMode])

  const placeOrder = (payload: any) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('WS not connected, cannot place order')
      toast.error('Not connected to server')
      return
    }
    try {
      wsRef.current.send(JSON.stringify({ type: 'place_order', ...payload }))
      toast('Placing order...', { icon: '📝' })
    } catch (e) {
      console.error(e)
      toast.error('Failed to send order')
    }
  }

  const switchUser = (username: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('WS not connected, cannot switch user')
      toast.error('Not connected to server')
      return
    }
    try {
      wsRef.current.send(JSON.stringify({ type: 'switch_user', username }))
    } catch (e) {
      console.error(e)
      toast.error('Failed to switch user')
    }
  }

  const switchAccount = (accountId: number) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('WS not connected, cannot switch account')
      toast.error('Not connected to server')
      return
    }
    try {
      wsRef.current.send(JSON.stringify({ type: 'switch_account', account_id: accountId }))
    } catch (e) {
      console.error(e)
      toast.error('Failed to switch AI trader')
    }
  }

  const handleAccountUpdated = () => {
    // Increment refresh trigger to force AccountSelector to refresh
    setAccountRefreshTrigger(prev => prev + 1)

    // Also refresh the current data snapshot
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'get_snapshot',
        trading_mode: tradingMode
      }))
    }
  }

  // For non-paper modes, create minimal state to avoid loading screen
  const effectiveOverview = overview || (tradingMode !== 'paper' ? {
    account: { id: 1, user_id: 1, name: 'Hyperliquid Account', account_type: 'AI', initial_capital: 0, current_cash: 0, frozen_cash: 0 },
    total_assets: 0,
    positions_value: 0
  } : null)

  // Data ready when user and account are loaded (or non-paper mode with effectiveOverview)
  const isDataReady = !!(user && account && (effectiveOverview || tradingMode !== 'paper'))

  // Show splash screen first (waits for both animation AND data ready)
  if (showSplash) {
    return <SplashScreen onComplete={handleSplashComplete} isReady={isDataReady} />
  }

  // Show onboarding if Hyper AI not configured
  if (showOnboarding) {
    return (
      <HyperAiOnboarding
        onComplete={handleOnboardingComplete}
        onSkip={handleOnboardingSkip}
      />
    )
  }

  const renderMainContent = () => {
    const refreshData = () => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
          type: 'get_snapshot',
          trading_mode: tradingMode
        }))
      }
    }

    return (
      <main className={`flex-1 overflow-hidden flex flex-col min-h-0 min-w-0 ${currentPage === 'hyper-ai' ? '' : 'p-4'}`}>

        {currentPage === 'hyper-ai' && (
          <HyperAiPage />
        )}

        {currentPage === 'comprehensive' && (
          tradingMode === 'paper' ? (
            <div className="flex flex-col flex-1 min-h-0 overflow-hidden pr-1">
              <ComprehensiveView
                overview={effectiveOverview}
                positions={positions}
                orders={orders}
                trades={trades}
                aiDecisions={aiDecisions}
                allAssetCurves={allAssetCurves}
                wsRef={wsRef}
                onSwitchUser={switchUser}
                onSwitchAccount={switchAccount}
                onRefreshData={refreshData}
                accountRefreshTrigger={accountRefreshTrigger}
                accounts={accounts}
                loadingAccounts={accountsLoading}
                onPageChange={handlePageChange}
              />
            </div>
          ) : (
            <>
              {/* Mobile: MobileDashboard, Desktop: HyperliquidView */}
              <div className="md:hidden flex flex-col flex-1 min-h-0">
                <MobileDashboard />
              </div>
              <div className="hidden md:flex flex-col flex-1 min-h-0">
                <HyperliquidView
                  wsRef={wsRef}
                  refreshKey={hyperliquidRefreshKey}
                  onPageChange={handlePageChange}
                />
              </div>
            </>
          )
        )}

        {currentPage === 'system-logs' && (
          <SystemLogs />
        )}

        {currentPage === 'prompt-management' && (
          <PromptManager />
        )}

        {currentPage === 'program-trader' && (
          <>
            {/* Mobile: MobilePrograms, Desktop: ProgramTrader */}
            <div className="md:hidden flex flex-col flex-1 min-h-0">
              <MobilePrograms />
            </div>
            <div className="hidden md:flex flex-col flex-1 min-h-0">
              <ProgramTrader />
            </div>
          </>
        )}

        {currentPage === 'signal-management' && (
          <SignalManager />
        )}

        {currentPage === 'attribution' && (
          <AttributionAnalysis />
        )}

        {currentPage === 'factor-library' && (
          <FactorLibrary />
        )}

        {currentPage === 'trader-management' && (
          <TraderManagement />
        )}

        {currentPage === 'hyperliquid' && (
          <HyperliquidPage accountId={account?.id || 1} />
        )}

        {currentPage === 'klines' && (
          <KlinesView onAccountUpdated={handleAccountUpdated} />
        )}

        {currentPage === 'premium-features' && (
          <PremiumFeaturesView onAccountUpdated={handleAccountUpdated} onPageChange={handlePageChange} />
        )}

        {currentPage === 'model-chat' && (
          <MobileModelChat />
        )}

        {currentPage === 'settings' && (
          <SettingsPage />
        )}

        {currentPage === 'arena-assets' && (
          <ArenaAssets />
        )}
      </main>
    )
  }

  const pageTitle = PAGE_TITLES[currentPage] ?? PAGE_TITLES.comprehensive

  return (
    <>
      <div className="h-screen flex overflow-hidden">
        <Sidebar
          currentPage={currentPage}
          onPageChange={handlePageChange}
          onAccountUpdated={handleAccountUpdated}
        />
        <div className="flex-1 flex flex-col min-w-0">
          <Header
            title={pageTitle}
            currentAccount={account}
            showAccountSelector={currentPage === 'comprehensive'}
          />
          {renderMainContent()}
        </div>
      </div>
      <AuthorizationModal
        isOpen={authModalOpen}
        onClose={handleAuthModalClose}
        unauthorizedAccounts={unauthorizedAccounts}
        onAuthorizationComplete={handleAuthorizationComplete}
      />
      <AgentWalletUpgradeModal
        isOpen={agentUpgradeModalOpen}
        onClose={() => setAgentUpgradeModalOpen(false)}
        walletsToUpgrade={walletsNeedUpgrade}
        onUpgradeComplete={() => {
          setAgentUpgradeModalOpen(false)
          setWalletsNeedUpgrade([])
          refreshAccounts()
        }}
      />
    </>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <ExchangeProvider>
        <TradingModeProvider>
          <ArenaDataProvider>
            <Toaster position="top-right" />
            <App />
          </ArenaDataProvider>
        </TradingModeProvider>
      </ExchangeProvider>
    </AuthProvider>
  </React.StrictMode>,
)
