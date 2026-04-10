import { useState, useEffect, useRef } from 'react'
import toast from 'react-hot-toast'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Trash2, Plus, Pencil, Download, Upload, Loader2 } from 'lucide-react'
import {
  getAccounts as getAccounts,
  createAccount as createAccount,
  updateAccount as updateAccount,
  testLLMConnection,
  exportTraderData,
  type TradingAccount,
  type TradingAccountCreate,
  type TradingAccountUpdate,
  type UnauthorizedAccount,
  type TraderExportData
} from '@/lib/api'
import {
  connectBrowserWallet,
  checkBuilderFeeAuthorized,
  approveBuilderFee,
} from '@/lib/hyperliquidWalletSetup'
import ExchangeWalletsPanel from '@/components/trader/ExchangeWalletsPanel'
import { AuthorizationModal } from '@/components/hyperliquid'
import TraderDataImportDialog from '@/components/trader/TraderDataImportDialog'
import { useTranslation } from 'react-i18next'
import { Switch } from '@/components/ui/switch'

interface SettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAccountUpdated?: () => void  // Add callback for when account is updated
  embedded?: boolean  // Add embedded mode support
}

interface AIAccount extends TradingAccount {
  model?: string
  base_url?: string
  api_key?: string
}

interface AIAccountCreate extends TradingAccountCreate {
  model?: string
  base_url?: string
  api_key?: string
}

function formatDependencies(deps: string[], t: (key: string) => string): string {
  const keyMap: [RegExp, string][] = [
    [/Prompt binding/i, 'common.dependencyPromptBinding'],
    [/Program binding/i, 'common.dependencyProgramBinding'],
    [/Open position/i, 'common.dependencyOpenPosition'],
    [/Signal Pool/i, 'common.dependencySignalPool'],
    [/Bound to.*Trader/i, 'common.dependencyActiveBinding'],
    [/is currently active/i, 'common.dependencyBindingActive'],
  ]
  const messages = new Set<string>()
  for (const dep of deps) {
    const match = keyMap.find(([re]) => re.test(dep))
    messages.add(match ? t(match[1]) : dep)
  }
  return Array.from(messages).join(' ')
}

export default function SettingsDialog({ open, onOpenChange, onAccountUpdated, embedded = false }: SettingsDialogProps) {
  const { t } = useTranslation()
  const [accounts, setAccounts] = useState<AIAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [toggleLoadingId, setToggleLoadingId] = useState<number | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [unauthorizedAccounts, setUnauthorizedAccounts] = useState<UnauthorizedAccount[]>([])
  const [importDialogOpen, setImportDialogOpen] = useState(false)
  const [importTargetAccount, setImportTargetAccount] = useState<AIAccount | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [newAccount, setNewAccount] = useState<AIAccountCreate>({
    name: '',
    model: '',
    base_url: '',
    api_key: 'default-key-please-update-in-settings',
    auto_trading_enabled: true,
  })
  const [editAccount, setEditAccount] = useState<AIAccountCreate>({
    name: '',
    model: '',
    base_url: '',
    api_key: 'default-key-please-update-in-settings',
    auto_trading_enabled: true,
  })

  const loadAccounts = async () => {
    try {
      setLoading(true)
      const data = await getAccounts()
      setAccounts(data)
    } catch (error) {
      console.error('Failed to load accounts:', error)
      toast.error('Failed to load AI traders')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      loadAccounts()
      setError(null)
      setTestResult(null)
      setShowAddForm(false)
      setEditingId(null)
    }
  }, [open])

  const handleCreateAccount = async () => {
    try {
      setLoading(true)
      setTesting(true)
      setError(null)
      setTestResult(null)

      if (!newAccount.name || !newAccount.name.trim()) {
        setError('Trader name is required')
        setLoading(false)
        setTesting(false)
        return
      }

      // If AI fields are provided, test LLM connection first
      if (newAccount.model || newAccount.base_url || newAccount.api_key) {
        setTestResult('Testing LLM connection...')
        try {
          const testResponse = await testLLMConnection({
            model: newAccount.model,
            base_url: newAccount.base_url,
            api_key: newAccount.api_key,
          })
          if (!testResponse.success) {
            const message = testResponse.message || 'LLM connection test failed'
            setError(`LLM Test Failed: ${message}`)
            setTestResult(`❌ Test failed: ${message}`)
            setLoading(false)
            setTesting(false)
            return
          }
          setTestResult('✅ LLM connection test passed! Creating AI trader...')
        } catch (testError) {
          const message = testError instanceof Error ? testError.message : 'LLM connection test failed'
          setError(`LLM Test Failed: ${message}`)
          setTestResult(`❌ Test failed: ${message}`)
          setLoading(false)
          setTesting(false)
          return
        }
      }

      console.log('Creating account with data:', newAccount)
      await createAccount(newAccount)
      setNewAccount({ name: '', model: '', base_url: '', api_key: 'default-key-please-update-in-settings', auto_trading_enabled: true })
      setShowAddForm(false)
      await loadAccounts()

      toast.success('AI trader created successfully!')

      // Notify parent component that account was created
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to create account:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to create AI trader'
      setError(errorMessage)
      toast.error(`Failed to create AI trader: ${errorMessage}`)
    } finally {
      setLoading(false)
      setTesting(false)
      setTestResult(null)
    }
  }

  const handleUpdateAccount = async () => {
    if (!editingId) return
    try {
      setLoading(true)
      setTesting(true)
      setError(null)
      setTestResult(null)
      
      if (!editAccount.name || !editAccount.name.trim()) {
        setError('Trader name is required')
        setLoading(false)
        setTesting(false)
        return
      }
      
      // Test LLM connection first if AI model data is provided
      if (editAccount.model || editAccount.base_url || editAccount.api_key) {
        setTestResult('Testing LLM connection...')
        
        try {
          const testResponse = await testLLMConnection({
            model: editAccount.model,
            base_url: editAccount.base_url,
            api_key: editAccount.api_key
          })
          
          if (!testResponse.success) {
            setError(`LLM Test Failed: ${testResponse.message}`)
            setTestResult(`❌ Test failed: ${testResponse.message}`)
            setLoading(false)
            setTesting(false)
            return
          }
          
          setTestResult('✅ LLM connection test passed!')
        } catch (testError) {
          const errorMessage = testError instanceof Error ? testError.message : 'LLM connection test failed'
          setError(`LLM Test Failed: ${errorMessage}`)
          setTestResult(`❌ Test failed: ${errorMessage}`)
          setLoading(false)
          setTesting(false)
          return
        }
      }
      
      setTesting(false)
      setTestResult('Test passed! Saving AI trader...')

      console.log('Updating account with data:', editAccount)
      await updateAccount(editingId, editAccount)
      setEditingId(null)
      setEditAccount({ name: '', model: '', base_url: '', api_key: '', auto_trading_enabled: true })
      setTestResult(null)
      await loadAccounts()
      
      toast.success('AI trader updated successfully!')
      
      // Notify parent component that account was updated
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to update account:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to update AI trader'
      setError(errorMessage)
      setTestResult(null)
      toast.error(`Failed to update AI trader: ${errorMessage}`)
    } finally {
      setLoading(false)
      setTesting(false)
    }
  }

  const startEdit = (account: AIAccount) => {
    setEditingId(account.id)
    setEditAccount({
      name: account.name,
      model: account.model || '',
      base_url: account.base_url || '',
      api_key: account.api_key || '',
      auto_trading_enabled: account.auto_trading_enabled ?? true,
    })
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditAccount({ name: '', model: '', base_url: '', api_key: 'default-key-please-update-in-settings', auto_trading_enabled: true })
    setTestResult(null)
    setError(null)
  }

  const handleToggleAutoTrading = async (account: AIAccount, nextValue: boolean) => {
    try {
      setToggleLoadingId(account.id)

      // If enabling trading and account has mainnet wallet, check builder fee via browser wallet
      if (nextValue && account.has_mainnet_wallet) {
        try {
          const masterAddress = await connectBrowserWallet()
          const authorized = await checkBuilderFeeAuthorized(masterAddress, 'mainnet')
          if (!authorized) {
            toast.loading(t('wallet.builder.signing', 'Please approve trading authorization in your wallet...'), { id: 'builder-auth' })
            await approveBuilderFee(masterAddress, 'mainnet')
            toast.dismiss('builder-auth')
            toast.success(t('wallet.builder.success', 'Trading authorization approved!'))
          }
        } catch (err: any) {
          toast.dismiss('builder-auth')
          console.error('Builder fee authorization failed:', err)
          if (err?.errorCode === 'NO_BROWSER_WALLET') {
            toast.error(t('wallet.error.noWallet', 'No browser wallet detected. Please install MetaMask or Rabby.'))
          } else if (err?.errorCode === 'NO_ACCOUNT_SELECTED') {
            toast.error(t('wallet.error.noAccount', 'No account selected in wallet. Please unlock your wallet and try again.'))
          } else if (err?.code === 4001) {
            toast.error(t('wallet.error.userRejected', 'Authorization rejected by user.'))
          } else {
            toast.error(t('wallet.error.authorizationFailedWrongAccount', 'Authorization failed. Please check that your wallet is switched to the correct account, then try again.'))
          }
          setToggleLoadingId(null)
          return
        }
      }

      await updateAccount(account.id, { auto_trading_enabled: nextValue })
      setAccounts((prev) =>
        prev.map((acc) => (acc.id === account.id ? { ...acc, auto_trading_enabled: nextValue } : acc))
      )
      toast.success(nextValue ? `Auto trading enabled for ${account.name}` : `Auto trading paused for ${account.name}`)
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to toggle auto trading:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to update trading status'
      toast.error(errorMessage)
    } finally {
      setToggleLoadingId(null)
    }
  }

  const handleAuthorizationComplete = async () => {
    setAuthModalOpen(false)
    // After authorization complete, enable trading for the authorized accounts
    for (const account of unauthorizedAccounts) {
      try {
        await updateAccount(account.account_id, { auto_trading_enabled: true })
        setAccounts((prev) =>
          prev.map((acc) => (acc.id === account.account_id ? { ...acc, auto_trading_enabled: true } : acc))
        )
        toast.success(`Auto trading enabled for ${account.account_name}`)
      } catch (error) {
        console.error(`Failed to enable trading for ${account.account_name}:`, error)
      }
    }
    setUnauthorizedAccounts([])
    onAccountUpdated?.()
  }

  const handleAuthModalClose = () => {
    setAuthModalOpen(false)
    setUnauthorizedAccounts([])
    loadAccounts() // Reload to get updated trading status
  }

  const handleExport = async (account: AIAccount) => {
    try {
      const data = await exportTraderData(account.id)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `trader-${account.name}-${new Date().toISOString().split('T')[0]}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      toast.success(t('traderData.exportSuccess', { count: data.decision_logs.length }))
    } catch (error) {
      console.error('Export failed:', error)
      toast.error(t('traderData.exportFailed'))
    }
  }

  const handleImportClick = (account: AIAccount) => {
    setImportTargetAccount(account)
    setImportDialogOpen(true)
  }

  const handleImportComplete = () => {
    setImportDialogOpen(false)
    setImportTargetAccount(null)
    loadAccounts()
    onAccountUpdated?.()
  }

  const handleDeleteTrader = async (account: AIAccount) => {
    if (!confirm(t('trader.confirmDeleteDesc'))) return
    try {
      const res = await fetch(`/api/account/${account.id}`, { method: 'DELETE' })
      const data = await res.json()
      if (res.ok && data.deleted) {
        toast.success(t('common.delete') + ' OK')
        loadAccounts()
        onAccountUpdated?.()
      } else if (data.dependencies) {
        const msg = formatDependencies(data.dependencies, t)
        toast.error(`${t('common.cannotDelete')}: ${msg}`, { duration: 5000 })
      } else {
        toast.error(data.error || data.detail || 'Failed to delete')
      }
    } catch {
      toast.error('Failed to delete trader')
    }
  }

  const content = (
    <>
      {!embedded && (
        <DialogHeader>
          <DialogTitle>AI Trader Management</DialogTitle>
          <DialogDescription>
            Manage your AI traders and their configurations
          </DialogDescription>
        </DialogHeader>
      )}

        <div className="space-y-6">
          {/* Existing Accounts */}
          <div className="space-y-4 flex-1 flex flex-col overflow-hidden"  style={{maxHeight: 'calc(100vh - 300px)'}}>
            {error && (
            <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded">
              {error}
            </div>
          )}
            <div className="flex items-center justify-between">
              <Button
                onClick={() => setShowAddForm(!showAddForm)}
                size="sm"
                className="flex items-center gap-2"
              >
                <Plus className="h-4 w-4" />
                Add AI Trader
              </Button>
            </div>

            {loading && accounts.length === 0 ? (
              <div>Loading AI traders...</div>
            ) : (
              <div className="space-y-3 overflow-y-auto">
                {/* Add New Account Form */}
                {showAddForm && (
                  <div className="space-y-4 border rounded-lg p-4 bg-muted/50">
                    <h3 className="text-lg font-medium">Add New AI Trader</h3>
                    <div className="space-y-3">
                      <div className="grid grid-cols-2 gap-3">
                        <Input
                          placeholder="Trader name"
                          value={newAccount.name || ''}
                          onChange={(e) => setNewAccount({ ...newAccount, name: e.target.value })}
                        />
                        <Input
                          placeholder="Model (e.g., gpt-4)"
                          value={newAccount.model || ''}
                          onChange={(e) => setNewAccount({ ...newAccount, model: e.target.value })}
                        />
                      </div>
                      <Input
                        placeholder="Base URL (e.g., https://api.openai.com/v1)"
                        value={newAccount.base_url || ''}
                        onChange={(e) => setNewAccount({ ...newAccount, base_url: e.target.value })}
                      />
                      <Input
                        placeholder="API Key"
                        type="password"
                        value={newAccount.api_key || ''}
                        onChange={(e) => setNewAccount({ ...newAccount, api_key: e.target.value })}
                      />
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Switch
                          checked={newAccount.auto_trading_enabled ?? true}
                          onCheckedChange={(checked) => setNewAccount({ ...newAccount, auto_trading_enabled: checked })}
                        />
                        <span>Start Trading</span>
                      </div>
                      <div className="flex gap-2">
                        <Button onClick={handleCreateAccount} disabled={loading}>
                          Test and Create
                        </Button>
                        <Button variant="outline" onClick={() => setShowAddForm(false)}>
                          Cancel
                        </Button>
                      </div>
                      {testResult && (
                        <div className="text-sm text-muted-foreground">
                          {testResult}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {accounts.map((account) => (
                  <div key={account.id} className="border rounded-lg p-4 space-y-4">
                    {editingId === account.id ? (
                      <div className="space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                          <Input
                            placeholder="Trader name"
                            value={editAccount.name || ''}
                            onChange={(e) => setEditAccount({ ...editAccount, name: e.target.value })}
                          />
                          <Input
                            placeholder="Model"
                            value={editAccount.model || ''}
                            onChange={(e) => setEditAccount({ ...editAccount, model: e.target.value })}
                          />
                        </div>
                        <Input
                          placeholder="Base URL"
                          value={editAccount.base_url || ''}
                          onChange={(e) => setEditAccount({ ...editAccount, base_url: e.target.value })}
                        />
                        <Input
                          placeholder="API Key"
                          type="password"
                          value={editAccount.api_key || ''}
                          onChange={(e) => setEditAccount({ ...editAccount, api_key: e.target.value })}
                        />
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                          <Switch
                            checked={editAccount.auto_trading_enabled ?? true}
                            onCheckedChange={(checked) => setEditAccount({ ...editAccount, auto_trading_enabled: checked })}
                          />
                          <span>Start Trading</span>
                        </div>
                        {testResult && (
                          <div className={`text-xs p-2 rounded ${
                            testResult.includes('❌')
                              ? 'bg-red-50 text-red-700 border border-red-200'
                              : 'bg-green-50 text-green-700 border border-green-200'
                          }`}>
                            {testResult}
                          </div>
                        )}
                        <div className="flex gap-2">
                          <Button onClick={handleUpdateAccount} disabled={loading || testing} size="sm">
                            {testing ? 'Testing...' : 'Test and Save'}
                          </Button>
                          <Button onClick={cancelEdit} variant="outline" size="sm" disabled={loading || testing}>
                            Cancel
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="flex items-center justify-between gap-4">
                          <div className="space-y-1 flex-1">
                            <div className="font-medium">{account.name}</div>
                            <div className="text-xs text-muted-foreground">
                              {account.model ? `Model: ${account.model}` : 'No model configured'}
                            </div>
                            {account.base_url && (
                              <div className="text-xs text-muted-foreground truncate">
                                Base URL: {account.base_url}
                              </div>
                            )}
                            {account.api_key && (
                              <div className="text-xs text-muted-foreground truncate max-w-full">
                                API Key: {'*'.repeat(Math.min(20, Math.max(0, (account.api_key?.length || 0) - 4)))}{account.api_key?.slice(-4) || '****'}
                              </div>
                            )}
                          </div>
                          <div className="flex items-center gap-3 shrink-0">
                            <div className="flex items-center gap-2 text-xs text-muted-foreground whitespace-nowrap">
                              {toggleLoadingId === account.id && (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              )}
                              <span>Start Trading</span>
                              <Switch
                                checked={account.auto_trading_enabled ?? true}
                                disabled={toggleLoadingId === account.id || loading}
                                onCheckedChange={(checked) => handleToggleAutoTrading(account, checked)}
                              />
                            </div>
                            <div className="flex items-center gap-1">
                              <Button
                                onClick={() => handleExport(account)}
                                variant="outline"
                                size="sm"
                                title={t('traderData.export')}
                              >
                                <Download className="h-4 w-4" />
                              </Button>
                              <Button
                                onClick={() => handleImportClick(account)}
                                variant="outline"
                                size="sm"
                                title={t('traderData.import')}
                              >
                                <Upload className="h-4 w-4" />
                              </Button>
                              <Button
                                onClick={() => startEdit(account)}
                                variant="outline"
                                size="sm"
                              >
                                <Pencil className="h-4 w-4" />
                              </Button>
                              <Button
                                onClick={() => handleDeleteTrader(account)}
                                variant="outline"
                                size="sm"
                                className="text-destructive hover:text-destructive"
                                title={t('trader.deleteTrader')}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </div>
                          </div>
                        </div>

                        {/* Exchange Wallets Panel */}
                        <ExchangeWalletsPanel
                          accountId={account.id}
                          accountName={account.name}
                          onWalletConfigured={loadAccounts}
                        />
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

        </div>
    </>
  )

  if (embedded) {
    return (
      <>
        {content}
        <AuthorizationModal
          isOpen={authModalOpen}
          onClose={handleAuthModalClose}
          unauthorizedAccounts={unauthorizedAccounts}
          onAuthorizationComplete={handleAuthorizationComplete}
        />
        {importTargetAccount && (
          <TraderDataImportDialog
            open={importDialogOpen}
            onOpenChange={setImportDialogOpen}
            account={importTargetAccount}
            onImportComplete={handleImportComplete}
          />
        )}
      </>
    )
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[600px]">
          {content}
        </DialogContent>
      </Dialog>
      <AuthorizationModal
        isOpen={authModalOpen}
        onClose={handleAuthModalClose}
        unauthorizedAccounts={unauthorizedAccounts}
        onAuthorizationComplete={handleAuthorizationComplete}
      />
      {importTargetAccount && (
        <TraderDataImportDialog
          open={importDialogOpen}
          onOpenChange={setImportDialogOpen}
          account={importTargetAccount}
          onImportComplete={handleImportComplete}
        />
      )}
    </>
  )
}

export { SettingsDialog }
