import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Loader2 } from 'lucide-react'

interface SignalPool {
  id: number
  pool_name: string
  enabled: boolean
  symbols: string[]
  exchange?: string
  source_type?: string | null
}

interface NotificationConfig {
  ai_trader: boolean
  program_trader: boolean
  signal_pools: Record<string, boolean>
}

interface NotificationConfigModalProps {
  open: boolean
  onClose: () => void
  onConfigChange?: (enabledCount: number) => void
}

export default function NotificationConfigModal({
  open,
  onClose,
  onConfigChange,
}: NotificationConfigModalProps) {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [config, setConfig] = useState<NotificationConfig>({
    ai_trader: true,
    program_trader: true,
    signal_pools: {},
  })
  const [signalPools, setSignalPools] = useState<SignalPool[]>([])

  useEffect(() => {
    if (open) {
      fetchData()
    }
  }, [open])

  const fetchData = async () => {
    setLoading(true)
    try {
      const [configRes, signalsRes] = await Promise.all([
        fetch('/api/bot/notification-config'),
        fetch('/api/signals'),
      ])
      const configData = await configRes.json()
      const signalsData = await signalsRes.json()
      setConfig(configData.config || { ai_trader: true, program_trader: true, signal_pools: {} })
      setSignalPools((signalsData.pools || []).filter((pool: SignalPool) => pool.enabled))
    } catch (err) {
      console.error('Failed to fetch notification config:', err)
    } finally {
      setLoading(false)
    }
  }

  const saveConfig = async (newConfig: NotificationConfig) => {
    setSaving(true)
    try {
      await fetch('/api/bot/notification-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig),
      })
      setConfig(newConfig)
      const enabledCount = countEnabled(newConfig)
      onConfigChange?.(enabledCount)
    } catch (err) {
      console.error('Failed to save notification config:', err)
    } finally {
      setSaving(false)
    }
  }

  const countEnabled = (cfg: NotificationConfig): number => {
    let count = 0
    if (cfg.ai_trader) count++
    if (cfg.program_trader) count++
    count += Object.values(cfg.signal_pools).filter(Boolean).length
    return count
  }

  const handleToggle = (key: 'ai_trader' | 'program_trader', value: boolean) => {
    const newConfig = { ...config, [key]: value }
    saveConfig(newConfig)
  }

  const handlePoolToggle = (poolId: number, value: boolean) => {
    const newConfig = {
      ...config,
      signal_pools: { ...config.signal_pools, [String(poolId)]: value },
    }
    saveConfig(newConfig)
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-3xl" onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <NotificationBellIcon />
            {t('bot.notificationSettings', 'Push Notifications')}
          </DialogTitle>
          <DialogDescription>
            {t('bot.notificationDesc', 'Configure which events trigger push notifications to your connected bots.')}
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="space-y-4 py-2">
            <div className="space-y-3">
              <div className="flex items-center justify-between px-2 py-2 rounded-lg bg-muted/30">
                <div className="flex items-center gap-2">
                  <span className="text-lg">🤖</span>
                  <span className="text-sm">{t('bot.aiTraderNotif', 'AI Trader Decisions')}</span>
                </div>
                <Switch
                  checked={config.ai_trader}
                  onCheckedChange={(v) => handleToggle('ai_trader', v)}
                  disabled={saving}
                />
              </div>

              <div className="flex items-center justify-between px-2 py-2 rounded-lg bg-muted/30">
                <div className="flex items-center gap-2">
                  <span className="text-lg">⚙️</span>
                  <span className="text-sm">{t('bot.programTraderNotif', 'Program Trader Decisions')}</span>
                </div>
                <Switch
                  checked={config.program_trader}
                  onCheckedChange={(v) => handleToggle('program_trader', v)}
                  disabled={saving}
                />
              </div>
            </div>

            <div className="border-t pt-3">
              <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                <span>🔔</span>
                {t('bot.signalPoolsNotif', 'Signal Pools')}
              </h4>
              {signalPools.length === 0 ? (
                <p className="text-xs text-muted-foreground px-2">
                  {t('bot.noSignalPools', 'No signal pools configured.')}
                </p>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 max-h-72 overflow-y-auto">
                  {signalPools.map((pool) => (
                    <div
                      key={pool.id}
                      className={`flex items-center gap-2 px-2 py-1.5 rounded-lg ${
                        pool.enabled ? 'bg-muted/30' : 'bg-muted/10 opacity-50'
                      }`}
                    >
                      <Switch
                        checked={config.signal_pools[String(pool.id)] || false}
                        onCheckedChange={(v) => handlePoolToggle(pool.id, v)}
                        disabled={saving || !pool.enabled}
                        className="scale-75 shrink-0"
                      />
                      <img
                        src={pool.exchange === 'binance' ? '/static/binance_logo.svg' : '/static/hyperliquid_logo.svg'}
                        alt={pool.exchange === 'binance' ? 'Binance' : 'Hyperliquid'}
                        className="w-4 h-4 shrink-0"
                      />
                      <span className="text-xs truncate flex-1" title={pool.pool_name}>{pool.pool_name}</span>
                      {!pool.enabled && (
                        <span className="text-[9px] text-muted-foreground bg-muted px-1 py-0.5 rounded shrink-0">
                          {t('common.off', 'Off')}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function NotificationBellIcon() {
  return (
    <svg className="w-5 h-5" viewBox="0 0 1024 1024" fill="currentColor">
      <path d="M512 0c282.666667 0 512 229.333333 512 512S794.666667 1024 512 1024 0 794.666667 0 512 229.333333 0 512 0z" fill="#2E74EE" opacity=".12" />
      <path d="M505.6 771.2L309.333333 611.2h-29.866666c-19.2 0-34.133333-14.933333-34.133334-34.133333V442.666667c0-19.2 14.933333-33.066667 34.133334-33.066667h36.266666l188.8-155.733333s48-30.933333 48 26.666666v462.933334c0 36.266667-20.266667 38.4-34.133333 34.133333-8.533333-2.133333-12.8-6.4-12.8-6.4z m117.333333-160c-6.4 0-12.8-2.133333-17.066666-7.466667-8.533333-9.6-7.466667-24.533333 2.133333-32 17.066667-14.933333 26.666667-36.266667 26.666667-58.666666s-9.6-43.733333-25.6-58.666667c-9.6-8.533333-9.6-23.466667-2.133334-32 8.533333-9.6 22.4-10.666667 32-2.133333 25.6 23.466667 40.533333 57.6 40.533334 92.8 0 35.2-14.933333 69.333333-41.6 92.8-4.266667 3.2-9.6 5.333333-14.933334 5.333333z m21.333334 88.533333c-8.533333 0-17.066667-5.333333-21.333334-13.866666-4.266667-11.733333 1.066667-24.533333 12.8-28.8 58.666667-23.466667 97.066667-77.866667 97.066667-139.733334s-38.4-116.266667-98.133333-139.733333c-11.733333-4.266667-17.066667-18.133333-12.8-28.8s18.133333-17.066667 29.866666-12.8c37.333333 14.933333 68.266667 39.466667 90.666667 70.4 23.466667 33.066667 35.2 70.4 35.2 110.933333 0 39.466667-11.733333 77.866667-35.2 109.866667-22.4 32-53.333333 56.533333-90.666667 70.4-2.133333 1.066667-4.266667 2.133333-7.466666 2.133333z" fill="#2E74EE" />
    </svg>
  )
}
