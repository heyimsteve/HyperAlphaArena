'use client'

import { createContext, useContext, useState, useEffect, useRef, type ReactNode } from 'react'
import Cookies from 'js-cookie'
import { getUserInfo, loadAuthConfig, type User, isTokenExpiringSoon, refreshAccessToken, getTokenExpiryTime } from '@/lib/auth'
import { getMembershipInfo, type MembershipInfo } from '@/lib/api'

interface AuthContextType {
  user: User | null
  loading: boolean
  authEnabled: boolean
  membership: MembershipInfo | null
  membershipLoading: boolean
  setUser: (user: User | null) => void
  logout: () => void
  refreshMembership: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [authEnabled, setAuthEnabled] = useState(false)
  const [membership, setMembership] = useState<MembershipInfo | null>(null)
  const [membershipLoading, setMembershipLoading] = useState(false)
  const refreshTimerRef = useRef<NodeJS.Timeout | null>(null)

  const clearAuthState = () => {
    Cookies.remove('arena_token')
    Cookies.remove('arena_refresh_token')
    Cookies.remove('arena_user')
    setUser(null)
    setMembership(null)
  }

  const syncHyperInsightRuntimeToken = async (token: string | null) => {
    try {
      if (!token) {
        await fetch('/api/signals/wallet-tracking/token', { method: 'DELETE' })
        return
      }

      await fetch('/api/signals/wallet-tracking/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token }),
      })
    } catch (error) {
      console.warn('[AuthContext] Failed to sync Hyper Insight runtime token:', error)
    }
  }

  // Function to handle token refresh
  const handleTokenRefresh = async (force: boolean = false): Promise<boolean> => {
    const currentToken = Cookies.get('arena_token')
    const refreshToken = Cookies.get('arena_refresh_token')

    if (!currentToken || !refreshToken) {
      console.log('[AuthContext] No tokens available for refresh')
      return false
    }

    // Check if token needs refresh (within 5 minutes of expiry)
    const expiryTime = getTokenExpiryTime(currentToken)
    const expiringSoon = isTokenExpiringSoon(currentToken, 5)
    console.log(`[AuthContext] Token check: force=${force}, expiringSoon=${expiringSoon}, expiryTime=${expiryTime}, now=${Date.now()}`)

    if (!force && !expiringSoon) {
      if (expiryTime) {
        const minutesLeft = Math.floor((expiryTime - Date.now()) / 1000 / 60)
        console.log(`[AuthContext] Token still valid for ${minutesLeft} minutes, no refresh needed`)
      }
      return false
    }

    console.log(`[AuthContext] Token expiring soon (force=${force}, expiringSoon=${expiringSoon}), refreshing...`)

    try {
      const tokenResponse = await refreshAccessToken(refreshToken)

      if (!tokenResponse || !tokenResponse.access_token) {
        console.error('[AuthContext] Failed to refresh token')
        await syncHyperInsightRuntimeToken(null)
        clearAuthState()
        return false
      }

      // Update cookies with new tokens
      Cookies.set('arena_token', tokenResponse.access_token, { expires: 7 })
      if (tokenResponse.refresh_token) {
        Cookies.set('arena_refresh_token', tokenResponse.refresh_token, { expires: 30 })
      }
      await syncHyperInsightRuntimeToken(tokenResponse.access_token)

      // Update user info with new token
      const userData = await getUserInfo(tokenResponse.access_token)
      if (userData) {
        setUser(userData)
        Cookies.set('arena_user', JSON.stringify(userData), { expires: 7 })
        console.log('[AuthContext] Token refreshed successfully')
        return true
      } else {
        console.error('[AuthContext] Failed to get user info after token refresh')
        await syncHyperInsightRuntimeToken(null)
        clearAuthState()
        return false
      }
    } catch (error) {
      console.error('[AuthContext] Token refresh error:', error)
      return false
    }
  }

  // Schedule next token refresh based on token expiry time
  const scheduleTokenRefresh = () => {
    const currentToken = Cookies.get('arena_token')
    if (!currentToken) return

    const expiryTime = getTokenExpiryTime(currentToken)
    if (!expiryTime) {
      console.warn('[AuthContext] Cannot get token expiry time, skipping scheduled refresh')
      return
    }

    // Calculate when to refresh: 5 minutes before expiry
    const REFRESH_BUFFER_MS = 5 * 60 * 1000 // 5 minutes
    const refreshTime = expiryTime - REFRESH_BUFFER_MS
    const delayMs = refreshTime - Date.now()

    // Clear existing timer
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = null
    }

    // If token already expired or will expire very soon (< 1 minute), refresh immediately
    if (delayMs < 60 * 1000) {
      console.log('[AuthContext] Token expiring very soon, refreshing immediately')
      handleTokenRefresh(true).then(success => {
        if (success) {
          scheduleTokenRefresh()
        }
      })
      return
    }

    // JavaScript setTimeout max delay is ~24.8 days (2147483647ms)
    // If delay exceeds this, skip scheduling - token is valid for a long time
    const MAX_TIMEOUT_MS = 2147483647
    if (delayMs > MAX_TIMEOUT_MS) {
      const daysLeft = Math.floor(delayMs / 1000 / 60 / 60 / 24)
      console.log(`[AuthContext] Token valid for ${daysLeft} days, no refresh scheduling needed`)
      return
    }

    // Schedule refresh at precise time
    const delayMinutes = Math.floor(delayMs / 1000 / 60)
    console.log(`[AuthContext] Token refresh scheduled in ${delayMinutes} minutes (${new Date(refreshTime).toLocaleString()})`)

    refreshTimerRef.current = setTimeout(async () => {
      console.log('[AuthContext] Scheduled token refresh triggered')
      const success = await handleTokenRefresh(true)
      if (success) {
        scheduleTokenRefresh()
      }
    }, delayMs)
  }

  // Function to refresh membership data
  const refreshMembership = async () => {
    if (!authEnabled || !user) {
      setMembership(null)
      return
    }

    setMembershipLoading(true)
    try {
      const result = await getMembershipInfo()
      setMembership(result.membership)

      // Sync membership info to local backend database
      // This keeps the local UserSubscription table in sync with www.akooi.com
      if (result.membership) {
        try {
          await fetch('/api/users/sync-membership', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              username: user.name,
              status: result.membership.status,
              current_period_end: result.membership.currentPeriodEnd,
            }),
          })
          console.log('[AuthContext] Membership synced to local database')
        } catch (syncError) {
          // Don't fail if sync fails - membership is already loaded from www.akooi.com
          console.warn('[AuthContext] Failed to sync membership to local database:', syncError)
        }
      }
    } catch (error) {
      console.error('Failed to refresh membership:', error)
      setMembership(null)
    } finally {
      setMembershipLoading(false)
    }
  }

  useEffect(() => {
    const initAuth = async () => {
      try {
        // Check if auth is configured
        const config = await loadAuthConfig()
        const isAuthEnabled = !!config
        setAuthEnabled(isAuthEnabled)

        if (!isAuthEnabled) {
          // Auth disabled, skip authentication
          setLoading(false)
          return
        }

        // Try to load user from cache first
        const cachedUser = Cookies.get('arena_user')
        if (cachedUser) {
          try {
            setUser(JSON.parse(cachedUser))
          } catch (e) {
            console.error('Failed to parse cached user:', e)
          }
        }

        // Try to refresh user info with token
        const token = Cookies.get('arena_token')
        if (token) {
          console.log('[AuthContext] Page loaded/refreshed, checking token status...')
          const refreshToken = Cookies.get('arena_refresh_token')

          if (refreshToken) {
            console.log('[AuthContext] Refresh token found, forcing token refresh on page load')
            const refreshed = await handleTokenRefresh(true)
            if (!refreshed) {
              console.warn('[AuthContext] Forced refresh failed on page load, user will need to sign in again')
              clearAuthState()
            } else {
              scheduleTokenRefresh()
            }
          } else {
            console.log('[AuthContext] No refresh token available, falling back to existing access token')
            const userData = await getUserInfo(token)
            if (userData) {
              setUser(userData)
              Cookies.set('arena_user', JSON.stringify(userData), { expires: 7 })
              await syncHyperInsightRuntimeToken(token)
              scheduleTokenRefresh()
            } else {
              await syncHyperInsightRuntimeToken(null)
              clearAuthState()
            }
          }
        }
      } catch (error) {
        console.error('Auth initialization error:', error)
      } finally {
        setLoading(false)
      }
    }

    initAuth()
  }, [])

  // Fetch membership info when user is authenticated
  useEffect(() => {
    if (!loading && user && authEnabled) {
      refreshMembership()
    }
  }, [user, loading, authEnabled])

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current)
        refreshTimerRef.current = null
        console.log('[AuthContext] Token refresh timer cleared on unmount')
      }
    }
  }, [])

  const logout = async () => {
    // Clear backend membership subscription first
    try {
      await fetch('/api/users/clear-membership', { method: 'POST' })
      console.log('[AuthContext] Backend membership cleared')
    } catch (e) {
      console.warn('[AuthContext] Failed to clear backend membership:', e)
    }

    // Local logout: clear Arena cookies and state
    // Casdoor session remains active, but next login will show account selection
    // because we use prompt=select_account in getSignInUrl()
    Cookies.remove('arena_token')
    Cookies.remove('arena_refresh_token')
    Cookies.remove('arena_user')
    await syncHyperInsightRuntimeToken(null)
    setUser(null)
    setMembership(null)

    // Clear refresh timer
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = null
    }

    // Refresh page to show logged-out state
    window.location.href = '/'
  }

  return (
    <AuthContext.Provider value={{
      user,
      loading,
      authEnabled,
      membership,
      membershipLoading,
      setUser,
      logout,
      refreshMembership
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
