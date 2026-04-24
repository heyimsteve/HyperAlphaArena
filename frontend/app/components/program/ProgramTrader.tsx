import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Check, X, Code, Play, AlertCircle, CheckCircle2, Copy, Sparkles, BookOpen, LineChart, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { toast } from 'react-hot-toast'
import Editor from '@monaco-editor/react'
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
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { testRunProgram, TestRunResponse, TradingAccount, getAccounts, getProgramDevGuide } from '@/lib/api'
import { copyToClipboard } from '@/lib/utils'
import { STRATEGY_RADAR_URL } from '@/lib/strategyRadar'
import AiProgramChatModal from './AiProgramChatModal'
import { BindingPreviewRunDialog } from './BindingPreviewRunDialog'
import { BacktestModal } from './BacktestModal'

interface Program {
  id: number
  name: string
  description: string | null
  code: string
  params: Record<string, any> | null
  icon: string | null
  binding_count: number
  created_at: string
  updated_at: string
}

interface WalletInfo {
  environment: string
  address: string
}

interface Binding {
  id: number
  account_id: number
  account_name: string
  program_id: number
  program_name: string
  signal_pool_ids: number[]
  signal_pool_names: string[]
  trigger_interval: number
  scheduled_trigger_enabled: boolean
  is_active: boolean
  last_trigger_at: string | null
  params_override: Record<string, any> | null
  exchange: string
  wallets: WalletInfo[]
  created_at: string
  updated_at: string
}

interface SignalPool {
  id: number
  pool_name: string
  symbols: string[]
  enabled: boolean
  exchange: string
  source_type?: string | null
}

interface AITrader {
  id: number
  name: string
  model: string | null
}

interface ValidationResult {
  is_valid: boolean
  errors: string[]
  warnings: string[]
}

const API_BASE = '/api/programs/'

const DEFAULT_CODE = `class MyStrategy:
    def init(self, params):
        self.threshold = params.get("threshold", 30)

    def should_trade(self, data):
        # Access market data
        rsi = data.get_indicator(data.trigger_symbol, "RSI14", "5m")
        price = data.prices.get(data.trigger_symbol, 0)

        # Decision logic
        if rsi.get("value", 50) < self.threshold:
            return Decision(
                action=ActionType.BUY,
                symbol=data.trigger_symbol,
                size_usd=data.available_balance * 0.5,
                leverage=10,
                reason="RSI oversold"
            )

        return Decision(action=ActionType.HOLD, symbol=data.trigger_symbol)
`

function formatDeps(deps: string[], t: (key: string) => string): string {
  const keyMap: [RegExp, string][] = [
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

export default function ProgramTrader() {
  const { t, i18n } = useTranslation()
  const [programs, setPrograms] = useState<Program[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedProgram, setSelectedProgram] = useState<Program | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  // Form state
  const [formName, setFormName] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formCode, setFormCode] = useState('')
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [validating, setValidating] = useState(false)
  const [saving, setSaving] = useState(false)

  // Binding state - now global (all bindings)
  const [bindings, setBindings] = useState<Binding[]>([])
  const [loadingBindings, setLoadingBindings] = useState(false)
  const [signalPools, setSignalPools] = useState<SignalPool[]>([])
  const [aiTraders, setAITraders] = useState<AITrader[]>([])
  const [selectedBinding, setSelectedBinding] = useState<Binding | null>(null)
  const [isAddingBinding, setIsAddingBinding] = useState(false)

  // Binding form state - now includes program selection
  const [bindingProgramId, setBindingProgramId] = useState<number | null>(null)
  const [bindingAccountId, setBindingAccountId] = useState<number | null>(null)
  const [bindingPoolIds, setBindingPoolIds] = useState<number[]>([])
  const [bindingInterval, setBindingInterval] = useState(300)
  const [bindingScheduledEnabled, setBindingScheduledEnabled] = useState(false)
  const [bindingActive, setBindingActive] = useState(true)
  const [bindingExchange, setBindingExchange] = useState<string>('hyperliquid')
  const [savingBinding, setSavingBinding] = useState(false)

  // Validate state
  const [testRunning, setTestRunning] = useState(false)
  const [testResult, setTestResult] = useState<TestRunResponse | null>(null)

  // Preview Run state
  const [previewRunOpen, setPreviewRunOpen] = useState(false)
  const [previewRunBinding, setPreviewRunBinding] = useState<Binding | null>(null)

  // Backtest state
  const [backtestOpen, setBacktestOpen] = useState(false)
  const [backtestBinding, setBacktestBinding] = useState<Binding | null>(null)

  // AI Chat state
  const [aiChatOpen, setAiChatOpen] = useState(false)
  const [accounts, setAccounts] = useState<TradingAccount[]>([])
  const [accountsLoading, setAccountsLoading] = useState(false)

  // Dev Guide state
  const [devGuideOpen, setDevGuideOpen] = useState(false)
  const [devGuideContent, setDevGuideContent] = useState('')
  const [devGuideLoading, setDevGuideLoading] = useState(false)
  const [devGuideLang, setDevGuideLang] = useState('')

  // Load all data on mount
  useEffect(() => {
    fetchPrograms()
    fetchSignalPools()
    fetchAITraders()
    fetchAllBindings()
    loadAccounts()

    /**
     * URL parameter support: #page-name?view=ID
     * When navigating from Hyper AI created entity card, auto-select the specific program.
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
          // Will be handled after programs load
          setSelectedProgram({ id: numId } as Program)
        }
        // Clean up URL after handling (keep hash without params)
        window.history.replaceState({}, '', window.location.pathname + hash.slice(0, hashParamIndex))
      }
    }
  }, [])

  // Auto-select first program when programs load (or URL-specified program)
  useEffect(() => {
    if (!loading && programs.length > 0 && !isCreating) {
      // If selectedProgram was set from URL param, find and fully load it
      if (selectedProgram && selectedProgram.id && !selectedProgram.name) {
        const target = programs.find(p => p.id === selectedProgram.id)
        if (target) {
          selectProgram(target)
          return
        }
      }
      // Otherwise select first if nothing selected
      if (!selectedProgram) {
        selectProgram(programs[0])
      }
    }
  }, [loading, programs])

  const fetchPrograms = async () => {
    try {
      const res = await fetch(API_BASE)
      if (res.ok) {
        const data = await res.json()
        setPrograms(data)
      }
    } catch (err) {
      console.error('Failed to fetch programs:', err)
    } finally {
      setLoading(false)
    }
  }

  const fetchSignalPools = async () => {
    try {
      const res = await fetch(`${API_BASE}signal-pools/`)
      if (res.ok) {
        const data = await res.json()
        setSignalPools((data || []).filter((pool: SignalPool) => pool.enabled))
      }
    } catch (err) {
      console.error('Failed to fetch signal pools:', err)
    }
  }

  const fetchAITraders = async () => {
    try {
      const res = await fetch(`${API_BASE}accounts/`)
      if (res.ok) {
        const data = await res.json()
        setAITraders(data)
      }
    } catch (err) {
      console.error('Failed to fetch AI traders:', err)
    }
  }

  const loadAccounts = async () => {
    setAccountsLoading(true)
    try {
      const data = await getAccounts()
      setAccounts(data)
    } catch (err) {
      console.error('Failed to load accounts:', err)
    } finally {
      setAccountsLoading(false)
    }
  }

  const handleOpenDevGuide = async () => {
    setDevGuideOpen(true)
    const currentLang = i18n.language.startsWith('zh') ? 'zh' : 'en'
    if (!devGuideContent || devGuideLang !== currentLang) {
      setDevGuideLoading(true)
      try {
        const data = await getProgramDevGuide(currentLang)
        setDevGuideContent(data.content)
        setDevGuideLang(currentLang)
      } catch (err) {
        console.error(err)
        toast.error(t('programTrader.loadDevGuideFailed', 'Failed to load dev guide'))
      } finally {
        setDevGuideLoading(false)
      }
    }
  }

  const handleAiSaveCode = async (code: string, name: string, description: string): Promise<boolean> => {
    try {
      if (isCreating) {
        // Create new program
        const res = await fetch(API_BASE, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, description, code, params: {} }),
        })
        if (res.ok) {
          const newProgram = await res.json()
          await fetchPrograms()
          selectProgram(newProgram)
          setIsCreating(false)
          toast.success(t('programTrader.created'))
          return true
        }
      } else if (selectedProgram) {
        // Update existing program
        const res = await fetch(`${API_BASE}${selectedProgram.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, description, code }),
        })
        if (res.ok) {
          await fetchPrograms()
          setFormCode(code)
          setFormName(name)
          setFormDescription(description)
          toast.success(t('programTrader.updated'))
          return true
        }
      }
      return false
    } catch (err) {
      console.error('Failed to save code:', err)
      toast.error(t('common.error'))
      return false
    }
  }

  const fetchAllBindings = async () => {
    setLoadingBindings(true)
    try {
      const res = await fetch(`${API_BASE}bindings/`)
      if (res.ok) {
        const data = await res.json()
        setBindings(data)
      }
    } catch (err) {
      console.error('Failed to fetch bindings:', err)
    } finally {
      setLoadingBindings(false)
    }
  }

  const handleCreateBinding = async () => {
    if (!bindingProgramId || !bindingAccountId) {
      toast.error('Please select both Program and AI Trader')
      return
    }
    setSavingBinding(true)
    try {
      const res = await fetch(`${API_BASE}bindings/?account_id=${bindingAccountId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          program_id: bindingProgramId,
          signal_pool_ids: bindingPoolIds,
          trigger_interval: bindingInterval,
          scheduled_trigger_enabled: bindingScheduledEnabled,
          is_active: bindingActive,
          exchange: bindingExchange
        })
      })
      if (res.ok) {
        toast.success('Binding created')
        fetchAllBindings()
        fetchPrograms()
        resetBindingForm()
      } else {
        const err = await res.json()
        toast.error(err.detail || 'Failed to create binding')
      }
    } catch (err) {
      toast.error('Failed to create binding')
    } finally {
      setSavingBinding(false)
    }
  }

  const handleUpdateBinding = async () => {
    if (!selectedBinding) return
    setSavingBinding(true)
    try {
      const res = await fetch(`${API_BASE}bindings/${selectedBinding.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          signal_pool_ids: bindingPoolIds,
          trigger_interval: bindingInterval,
          scheduled_trigger_enabled: bindingScheduledEnabled,
          is_active: bindingActive,
          exchange: bindingExchange
        })
      })
      if (res.ok) {
        toast.success('Binding updated')
        fetchAllBindings()
        setSelectedBinding(null)
        resetBindingForm()
      } else {
        const err = await res.json()
        toast.error(err.detail || 'Failed to update binding')
      }
    } catch (err) {
      toast.error('Failed to update binding')
    } finally {
      setSavingBinding(false)
    }
  }

  const handleDeleteBinding = async (bindingId: number) => {
    if (!confirm('Delete this binding?')) return
    try {
      const res = await fetch(`${API_BASE}bindings/${bindingId}`, { method: 'DELETE' })
      const data = await res.json()
      if (data.deleted) {
        toast.success('Binding deleted')
        fetchAllBindings()
        fetchPrograms()
      } else if (data.dependencies) {
        const msg = formatDeps(data.dependencies as string[], t)
        toast.error(`${t('common.cannotDelete')}: ${msg}`, { duration: 5000 })
      } else {
        toast.error(data.error || 'Failed to delete binding')
      }
    } catch (err) {
      toast.error('Failed to delete binding')
    }
  }

  const resetBindingForm = () => {
    setIsAddingBinding(false)
    setSelectedBinding(null)
    setBindingProgramId(null)
    setBindingAccountId(null)
    setBindingPoolIds([])
    setBindingInterval(300)
    setBindingScheduledEnabled(false)
    setBindingActive(true)
    setBindingExchange('hyperliquid')
  }

  const selectBindingForEdit = (binding: Binding) => {
    setSelectedBinding(binding)
    setIsAddingBinding(false)
    setBindingProgramId(binding.program_id)
    setBindingAccountId(binding.account_id)
    setBindingPoolIds(binding.signal_pool_ids || [])
    setBindingInterval(binding.trigger_interval)
    setBindingScheduledEnabled(binding.scheduled_trigger_enabled)
    setBindingActive(binding.is_active)
    setBindingExchange(binding.exchange || 'hyperliquid')
  }

  const togglePoolId = (poolId: number) => {
    setBindingPoolIds(prev =>
      prev.includes(poolId)
        ? prev.filter(id => id !== poolId)
        : [...prev, poolId]
    )
  }

  const usesWalletPool = (poolIds: number[]) =>
    poolIds.some((id) => (signalPools.find((pool) => pool.id === id)?.source_type || 'market_signals') === 'wallet_tracking')

  const validateCode = async (code: string) => {
    setValidating(true)
    try {
      const res = await fetch(`${API_BASE}validate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      })
      if (res.ok) {
        const result = await res.json()
        setValidation(result)
        return result.is_valid
      }
    } catch (err) {
      console.error('Validation failed:', err)
    } finally {
      setValidating(false)
    }
    return false
  }

  const handleTestRun = async () => {
    if (!formCode.trim()) {
      toast.error(t('programTrader.codeRequired', 'Code is required'))
      return
    }
    setTestRunning(true)
    setTestResult(null)
    try {
      const result = await testRunProgram({
        code: formCode,
        symbol: 'BTC',  // Fixed symbol for validation
        period: '1h'
      })
      setTestResult(result)
      if (result.success) {
        toast.success(t('programTrader.validateSuccess', 'Validation successful'))
      }
    } catch (err) {
      console.error('Validation failed:', err)
      toast.error(t('programTrader.validateFailed', 'Validation failed'))
    } finally {
      setTestRunning(false)
    }
  }

  const copyTestResult = async () => {
    if (!testResult) return
    const content = testResult.success
      ? `Decision: ${testResult.decision?.action}\nSymbol: ${testResult.decision?.symbol || 'N/A'}\nSize: ${testResult.decision?.size_usd ? `$${testResult.decision.size_usd}` : 'N/A'}\nLeverage: ${testResult.decision?.leverage || 'N/A'}x\nReason: ${testResult.decision?.reason || 'N/A'}`
      : `Error Type: ${testResult.error_type}\nError: ${testResult.error_message}\n\nLocation: Line ${testResult.error_location?.line || 'unknown'}\nCode: ${testResult.error_location?.code_context || 'N/A'}\n\nTraceback:\n${testResult.error_traceback || 'N/A'}\n\nSuggestions:\n${testResult.suggestions?.join('\n') || 'None'}`
    const success = await copyToClipboard(content)
    if (success) toast.success(t('feed.copied', 'Copied'))
  }

  const handleCreate = async () => {
    if (!formName.trim() || !formCode.trim()) {
      toast.error(t('programTrader.nameCodeRequired', 'Name and code are required'))
      return
    }
    const isValid = await validateCode(formCode)
    if (!isValid) {
      toast.error(t('programTrader.validationFailed'))
      return
    }
    setSaving(true)
    try {
      const res = await fetch(API_BASE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: formName,
          description: formDescription,
          code: formCode,
          params: {}
        })
      })
      if (res.ok) {
        const newProgram = await res.json()
        toast.success(t('common.save') + ' OK')
        fetchPrograms()
        setIsCreating(false)
        setSelectedProgram(newProgram)
      }
    } catch (err) {
      toast.error('Failed to create program')
    } finally {
      setSaving(false)
    }
  }

  const handleUpdate = async () => {
    if (!selectedProgram) return
    const isValid = await validateCode(formCode)
    if (!isValid) {
      toast.error(t('programTrader.validationFailed'))
      return
    }
    setSaving(true)
    try {
      const res = await fetch(`${API_BASE}${selectedProgram.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: formName,
          description: formDescription,
          code: formCode
        })
      })
      if (res.ok) {
        toast.success(t('common.save') + ' OK')
        fetchPrograms()
      }
    } catch (err) {
      toast.error('Failed to update program')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm(t('programTrader.confirmDelete'))) return
    try {
      const res = await fetch(`${API_BASE}${id}`, { method: 'DELETE' })
      const data = await res.json()
      if (data.deleted) {
        toast.success(t('common.delete') + ' OK')
        fetchPrograms()
        if (selectedProgram?.id === id) {
          setSelectedProgram(null)
          resetForm()
        }
      } else if (data.dependencies) {
        const msg = formatDeps(data.dependencies as string[], t)
        toast.error(`${t('common.cannotDelete')}: ${msg}`, { duration: 5000 })
      } else {
        toast.error(data.error || data.detail || 'Failed to delete')
      }
    } catch (err) {
      toast.error('Failed to delete program')
    }
  }

  const resetForm = () => {
    // When canceling new creation, select first program if available
    if (programs.length > 0) {
      selectProgram(programs[0])
    } else {
      setFormName('')
      setFormDescription('')
      setFormCode(DEFAULT_CODE)
      setValidation(null)
      setIsCreating(false)
    }
  }

  const selectProgram = (program: Program) => {
    setSelectedProgram(program)
    setFormName(program.name)
    setFormDescription(program.description || '')
    setFormCode(program.code)
    setValidation(null)
    setIsCreating(false)
    resetBindingForm()
  }

  const startCreate = () => {
    setSelectedProgram(null)
    setFormName('')
    setFormDescription('')
    setFormCode(DEFAULT_CODE)
    setValidation(null)
    setIsCreating(true)
  }

  return (
    <div className="flex h-full gap-4 overflow-hidden">
      {/* LEFT: Program Editor (like PromptManager) */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <Card className="flex-1 flex flex-col overflow-hidden">
          <CardHeader className="pb-2 flex-shrink-0">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Code className="h-4 w-4" />
                  {t('programTrader.title')}
                </CardTitle>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleOpenDevGuide}
                >
                  <BookOpen className="h-4 w-4 mr-1" />
                  {t('programTrader.devGuide', 'Dev Guide')}
                </Button>
                <Button size="sm" variant="outline" asChild>
                  <a href={STRATEGY_RADAR_URL} target="_blank" rel="noopener noreferrer">
                    {t('programTrader.strategyIdeasLink', 'Need strategy ideas?')}
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </Button>
              </div>
              <div className="flex gap-2">
                {/* New Button - hidden when creating */}
                {!isCreating && (
                  <Button size="sm" variant="outline" onClick={startCreate}>
                    <Plus className="h-4 w-4 mr-1" /> {t('prompt.new')}
                  </Button>
                )}
                {selectedProgram && (
                  <Button size="sm" variant="outline" className="text-destructive" onClick={() => handleDelete(selectedProgram.id)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col overflow-hidden gap-3 pt-0">
            {/* Program Selection Dropdown with AI Button */}
            <div>
              <label className="text-xs uppercase text-muted-foreground">{t('programTrader.programName')}</label>
              <div className="flex gap-2 items-center">
                <Select
                  value={selectedProgram ? String(selectedProgram.id) : (isCreating ? 'new' : '')}
                  onValueChange={(val) => {
                    if (val === 'new') {
                      startCreate()
                    } else {
                      const prog = programs.find(p => p.id === Number(val))
                      if (prog) selectProgram(prog)
                    }
                  }}
                  disabled={loading}
                >
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder={loading ? t('common.loading') : t('programTrader.emptyEditor')} />
                  </SelectTrigger>
                  <SelectContent>
                    {programs.map(p => (
                      <SelectItem key={p.id} value={String(p.id)}>
                        {p.name} {p.binding_count > 0 && `(${p.binding_count})`}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {/* AI Coding Button - next to dropdown */}
                <Button
                  size="sm"
                  onClick={() => setAiChatOpen(true)}
                  className="bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white border-0 shadow-lg hover:shadow-xl transition-all whitespace-nowrap"
                >
                  <Sparkles className="h-4 w-4 mr-1" />
                  {isCreating ? t('program.aiChat.developWithAi') : t('program.aiChat.optimizeWithAi')}
                </Button>
              </div>
            </div>

            {(selectedProgram || isCreating) && (
              <>
                {/* Name & Description */}
                <div className="grid grid-cols-2 gap-4 flex-shrink-0">
                  <div>
                    <label className="text-xs font-medium">{t('common.name')}</label>
                    <Input
                      value={formName}
                      onChange={e => setFormName(e.target.value)}
                      placeholder="Strategy name"
                      className="h-8"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium">{t('common.description')}</label>
                    <Input
                      value={formDescription}
                      onChange={e => setFormDescription(e.target.value)}
                      placeholder="Brief description"
                      className="h-8"
                    />
                  </div>
                </div>

                {/* Code Editor */}
                <div className="flex-1 flex flex-col min-h-0">
                  <div className="mb-1 flex flex-wrap items-center gap-2 flex-shrink-0">
                    <label className="text-xs font-medium">{t('programTrader.strategyCode')}</label>
                    {isCreating && (
                      <span className="text-xs text-muted-foreground">
                        {t('programTrader.testTemplateHint', 'Default code is a test template for learning the workflow, not a profit guarantee.')}
                      </span>
                    )}
                  </div>
                  <div className="flex-1 border rounded-md overflow-hidden">
                    <Editor
                      defaultLanguage="python"
                      value={formCode}
                      onChange={(value) => setFormCode(value || '')}
                      theme="vs-dark"
                      options={{
                        minimap: { enabled: false },
                        fontSize: 13,
                        lineNumbers: 'on',
                        scrollBeyondLastLine: false,
                        automaticLayout: true,
                        tabSize: 4,
                        wordWrap: 'on',
                      }}
                    />
                  </div>
                </div>

                {/* Validate & Actions */}
                <div className="flex-shrink-0 space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    {/* Validate Button */}
                    <Button size="sm" variant="outline" onClick={handleTestRun} disabled={testRunning} className="h-7">
                      <Play className="h-3 w-3 mr-1" />
                      {testRunning ? t('programTrader.validating', 'Validating...') : t('programTrader.validate', 'Validate')}
                    </Button>
                    {/* Save/Create Actions */}
                    <div className="flex gap-2">
                      {isCreating ? (
                        <>
                          <Button size="sm" variant="outline" onClick={resetForm}>{t('common.cancel')}</Button>
                          <Button size="sm" onClick={handleCreate} disabled={saving}>{t('common.create')}</Button>
                        </>
                      ) : (
                        <Button size="sm" onClick={handleUpdate} disabled={saving}>{t('common.save')}</Button>
                      )}
                    </div>
                  </div>

                  {/* Test Result Display */}
                  {testResult && (
                    <div className={`p-3 rounded-md text-xs ${testResult.success ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800' : 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800'}`}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                          {testResult.success ? (
                            <CheckCircle2 className="h-4 w-4 text-green-600 flex-shrink-0" />
                          ) : (
                            <AlertCircle className="h-4 w-4 text-red-600 flex-shrink-0" />
                          )}
                          <span className="font-medium">
                            {testResult.success ? t('programTrader.testSuccess', 'Test Passed') : `${testResult.error_type}: ${testResult.error_message}`}
                          </span>
                        </div>
                        <div className="flex items-center gap-1">
                          <Button size="sm" variant="ghost" className="h-6 px-2" onClick={copyTestResult}>
                            <Copy className="h-3 w-3" />
                          </Button>
                          <Button size="sm" variant="ghost" className="h-6 px-2" onClick={() => setTestResult(null)}>
                            <X className="h-3 w-3" />
                          </Button>
                        </div>
                      </div>
                      {testResult.success && testResult.decision && (
                        <div className="mt-2 pl-6 space-y-1 text-muted-foreground">
                          <div><span className="font-medium text-foreground">{t('programTrader.action', 'Action')}:</span> {testResult.decision.action}</div>
                          {testResult.decision.symbol && <div><span className="font-medium text-foreground">{t('common.symbol', 'Symbol')}:</span> {testResult.decision.symbol}</div>}
                          {testResult.decision.size_usd && <div><span className="font-medium text-foreground">{t('programTrader.size', 'Size')}:</span> ${testResult.decision.size_usd.toFixed(2)}</div>}
                          {testResult.decision.leverage && <div><span className="font-medium text-foreground">{t('programTrader.leverage', 'Leverage')}:</span> {testResult.decision.leverage}x</div>}
                          {testResult.decision.reason && <div><span className="font-medium text-foreground">{t('programTrader.reason', 'Reason')}:</span> {testResult.decision.reason}</div>}
                        </div>
                      )}
                      {!testResult.success && (
                        <div className="mt-2 pl-6 space-y-1">
                          {testResult.error_location?.line && (
                            <div className="text-muted-foreground">
                              <span className="font-medium text-foreground">{t('programTrader.errorLine', 'Line')} {testResult.error_location.line}:</span> {testResult.error_location.code_context}
                            </div>
                          )}
                          {testResult.suggestions && testResult.suggestions.length > 0 && (
                            <div className="mt-2">
                              <span className="font-medium text-foreground">{t('programTrader.suggestions', 'Suggestions')}:</span>
                              <ul className="list-disc list-inside text-muted-foreground">
                                {testResult.suggestions.map((s, i) => <li key={i}>{s}</li>)}
                              </ul>
                            </div>
                          )}
                        </div>
                      )}
                      {testResult.market_data && (
                        <div className="mt-2 pl-6 text-[10px] text-muted-foreground">
                          {t('programTrader.marketData', 'Market')}: {testResult.market_data.symbol} @ ${testResult.market_data.current_price?.toFixed(2)} | {testResult.market_data.klines_count} klines | {testResult.execution_time_ms.toFixed(0)}ms
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* RIGHT: Global Bindings Panel */}
      <Card className="w-[50rem] flex-shrink-0 flex flex-col overflow-hidden">
        <CardHeader className="pb-2 flex-shrink-0">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">{t('programTrader.bindings')}</CardTitle>
            <Button size="sm" variant="outline" onClick={() => { resetBindingForm(); setIsAddingBinding(true) }}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="flex-1 flex flex-col overflow-hidden gap-4 pt-0">
          {/* Bindings Table */}
          <div className="flex-1 overflow-auto">
            {loadingBindings ? (
              <div className="text-center text-muted-foreground py-8">{t('common.loading')}</div>
            ) : bindings.length === 0 ? (
              <div className="text-center text-muted-foreground py-8">{t('programTrader.noBindings')}</div>
            ) : (
              <div className="space-y-3">
                {bindings.map(b => (
                  <div key={b.id} className="border rounded-lg p-4 hover:bg-muted/30 transition-colors">
                    {/* Header: Program Name + Status */}
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <Code className="h-4 w-4 text-muted-foreground" />
                        <span className="font-medium">{b.program_name}</span>
                      </div>
                      <span className={`text-xs px-2 py-1 rounded ${b.is_active ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
                        {b.is_active ? t('common.enabled') : t('common.disabled')}
                      </span>
                    </div>

                    {/* Info Grid */}
                    <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm mb-3">
                      <div>
                        <span className="text-muted-foreground">{t('programTrader.aiTrader')}:</span>{' '}
                        <span className="font-medium">{b.account_name}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">{t('programTrader.exchange')}:</span>{' '}
                        <span className="font-medium capitalize">{b.exchange || 'hyperliquid'}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">
                          {(b.exchange || 'hyperliquid') === 'binance' ? t('programTrader.apiKey', 'API Key') : t('programTrader.wallets')}:
                        </span>{' '}
                        {b.wallets.length > 0 ? b.wallets.map((w, idx) => (
                          <span key={w.address + idx} className="font-mono text-xs" title={w.address}>
                            {w.environment}: {(b.exchange || 'hyperliquid') === 'binance' ? w.address : `${w.address.slice(0, 6)}...${w.address.slice(-4)}`}
                          </span>
                        )) : <span className="text-muted-foreground">{t('programTrader.noWallet')}</span>}
                      </div>
                      <div>
                        <span className="text-muted-foreground">{t('programTrader.signalPools')}:</span>{' '}
                        <span>{b.signal_pool_names && b.signal_pool_names.length > 0
                          ? b.signal_pool_names.join(', ')
                          : '-'}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">{t('programTrader.scheduled')}:</span>{' '}
                        <span>{b.scheduled_trigger_enabled ? `${b.trigger_interval}s` : '-'}</span>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2 pt-2 border-t">
                      <Button size="sm" variant="outline" onClick={() => { setPreviewRunBinding(b); setPreviewRunOpen(true) }}>
                        <Play className="h-3 w-3 mr-1" />
                        {t('programTrader.preview', 'Preview')}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={usesWalletPool(b.signal_pool_ids || [])}
                        title={usesWalletPool(b.signal_pool_ids || [])
                          ? t('programTrader.walletBacktestUnavailable', 'Wallet signal bindings use external real-time events and are not available for backtest.')
                          : undefined}
                        onClick={() => { setBacktestBinding(b); setBacktestOpen(true) }}
                      >
                        <LineChart className="h-3 w-3 mr-1" />
                        {t('programTrader.backtest', 'Backtest')}
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => selectBindingForEdit(b)}>
                        {t('common.edit')}
                      </Button>
                      <Button size="sm" variant="outline" className="text-destructive hover:text-destructive" onClick={() => handleDeleteBinding(b.id)}>
                        {t('common.delete')}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Binding Form */}
          {(isAddingBinding || selectedBinding) && (
            <div className="border-t pt-4 space-y-3 overflow-auto min-h-0">
              {/* Program Selection (only for new bindings) */}
              <div>
                <label className="text-xs uppercase text-muted-foreground">Program</label>
                <Select
                  value={bindingProgramId ? String(bindingProgramId) : ''}
                  onValueChange={val => setBindingProgramId(Number(val))}
                  disabled={!!selectedBinding}
                >
                  <SelectTrigger className="h-8">
                    <SelectValue placeholder="Select program" />
                  </SelectTrigger>
                  <SelectContent>
                    {programs.map(p => (
                      <SelectItem key={p.id} value={String(p.id)}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* AI Trader Selection */}
              <div>
                <label className="text-xs uppercase text-muted-foreground">{t('programTrader.aiTrader')}</label>
                <Select
                  value={bindingAccountId ? String(bindingAccountId) : ''}
                  onValueChange={val => setBindingAccountId(Number(val))}
                  disabled={!!selectedBinding}
                >
                  <SelectTrigger className="h-8">
                    <SelectValue placeholder="Select AI Trader" />
                  </SelectTrigger>
                  <SelectContent>
                    {aiTraders.map(a => (
                      <SelectItem key={a.id} value={String(a.id)}>{a.name} {a.model && `(${a.model})`}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Exchange Selection */}
              <div>
                <label className="text-xs uppercase text-muted-foreground">{t('programTrader.exchange')}</label>
                <Select
                  value={bindingExchange}
                  onValueChange={v => {
                    // Clear signal pools that don't match the new exchange
                    const matchingPoolIds = bindingPoolIds.filter(id => {
                      const pool = signalPools.find(p => p.id === id)
                      return (pool?.exchange || 'hyperliquid') === v
                    })
                    setBindingPoolIds(matchingPoolIds)
                    setBindingExchange(v)
                  }}
                >
                  <SelectTrigger className="h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="hyperliquid">{t('programTrader.exchangeHyperliquid')}</SelectItem>
                    <SelectItem value="binance">{t('programTrader.exchangeBinance')}</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground mt-1">{t('programTrader.exchangeHint')}</p>
              </div>

              {/* Signal Pools */}
              <div>
                <label className="text-xs uppercase text-muted-foreground">{t('programTrader.signalPools')}</label>
                <div className="flex flex-wrap gap-1 mt-1">
                  {signalPools.length === 0 ? (
                    <span className="text-xs text-muted-foreground">{t('programTrader.noSignalPools')}</span>
                  ) : signalPools.map(pool => {
                    const poolExchange = pool.exchange || 'hyperliquid'
                    const isMatchingExchange = poolExchange === bindingExchange
                    const isDisabled = !isMatchingExchange
                    const isWalletPool = (pool.source_type || 'market_signals') === 'wallet_tracking'
                    return (
                      <button
                        key={pool.id}
                        type="button"
                        onClick={() => !isDisabled && togglePoolId(pool.id)}
                        disabled={isDisabled}
                        className={`text-xs px-2 py-1 rounded border flex items-center gap-1 ${
                          isDisabled ? 'opacity-50 cursor-not-allowed' :
                          bindingPoolIds.includes(pool.id) ? 'bg-primary text-primary-foreground' : 'bg-muted'
                        }`}
                        title={isDisabled ? `${t('programTrader.poolExchangeMismatch', 'Exchange mismatch')}: ${poolExchange}` : pool.pool_name}
                      >
                        <span className={`px-1 py-0.5 rounded text-[9px] font-medium ${
                          poolExchange === 'hyperliquid' ? 'bg-emerald-500/20 text-emerald-500' : 'bg-yellow-500/20 text-yellow-500'
                        }`}>
                          {poolExchange === 'hyperliquid' ? 'HL' : 'BN'}
                        </span>
                        {isWalletPool && (
                          <span className="px-1 py-0.5 rounded text-[9px] font-medium bg-blue-500/15 text-blue-500">
                            {t('programTrader.walletBadge', 'Wallet')}
                          </span>
                        )}
                        {pool.pool_name}
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* Scheduled Trigger */}
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <Switch checked={bindingScheduledEnabled} onCheckedChange={setBindingScheduledEnabled} />
                  <span className="text-xs">{t('programTrader.scheduled')}</span>
                </div>
                {bindingScheduledEnabled && (
                  <div className="flex items-center gap-2">
                    <Input
                      type="number"
                      value={bindingInterval}
                      onChange={e => setBindingInterval(Number(e.target.value))}
                      className="w-20 h-8"
                    />
                    <span className="text-xs text-muted-foreground">sec</span>
                  </div>
                )}
              </div>

              {/* Active Status */}
              <div className="flex items-center gap-2">
                <Switch checked={bindingActive} onCheckedChange={setBindingActive} />
                <span className="text-xs">{t('programTrader.active')}</span>
              </div>

              {/* Actions */}
              <div className="flex gap-2 pt-2">
                <Button size="sm" variant="outline" onClick={resetBindingForm}>{t('common.cancel')}</Button>
                <Button
                  size="sm"
                  onClick={selectedBinding ? handleUpdateBinding : handleCreateBinding}
                  disabled={savingBinding}
                >
                  {t('programTrader.saveBinding')}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Dev Guide Modal */}
      <Dialog open={devGuideOpen} onOpenChange={setDevGuideOpen}>
        <DialogContent className="max-w-5xl max-h-[80vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>{t('programTrader.devGuideTitle', 'Program Development Guide')}</DialogTitle>
            <DialogDescription>
              {t('programTrader.devGuideDesc', 'Available APIs and data structures for writing trading strategies')}
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 flex gap-4 overflow-hidden">
            {/* Left Sidebar - AI Coding CTA */}
            <div className="w-56 flex-shrink-0">
              <div className="bg-gradient-to-b from-purple-50 to-pink-50 dark:from-purple-950/30 dark:to-pink-950/30 border border-purple-200 dark:border-purple-800 rounded-lg p-4 h-full flex flex-col">
                <div className="text-2xl mb-3">✨</div>
                <p className="text-sm font-medium text-foreground mb-3">
                  {t('programTrader.needHelpWriting', 'Need help writing code?')}
                </p>
                <p className="text-xs text-muted-foreground mb-3">
                  {t('programTrader.tryAiCodeGeneration', 'Try')} <span className="font-semibold text-purple-600 dark:text-purple-400">{t('programTrader.aiCodeGeneration', 'AI Code Generation')}</span> {t('programTrader.toCreateStrategies', 'to create professional trading strategies.')}
                </p>
                <div className="text-xs text-muted-foreground space-y-1 mb-4">
                  <p className="font-medium">{t('programTrader.aiWillHelp', 'AI will help you:')}</p>
                  <ul className="list-disc list-inside space-y-0.5 text-[11px]">
                    <li>{t('programTrader.generateOptimized', 'Generate optimized code')}</li>
                    <li>{t('programTrader.selectApis', 'Select appropriate APIs')}</li>
                    <li>{t('programTrader.addRiskManagement', 'Add risk management')}</li>
                    <li>{t('programTrader.refineViaConversation', 'Refine via conversation')}</li>
                  </ul>
                </div>
                <Button
                  size="sm"
                  onClick={() => {
                    setDevGuideOpen(false)
                    setAiChatOpen(true)
                  }}
                  className="w-full bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white border-0 shadow-md hover:shadow-lg transition-all text-xs"
                >
                  ✨ {t('programTrader.tryAiCoding', 'Try AI Coding')}
                </Button>
                <p className="text-[10px] text-muted-foreground mt-2 text-center">
                  {t('programTrader.premiumFeature', 'Premium feature')}
                </p>
              </div>
            </div>

            {/* Right Content - Dev Guide Documentation */}
            <ScrollArea className="flex-1 pr-4">
              {devGuideLoading ? (
                <div className="flex items-center justify-center py-8">
                  <span className="text-muted-foreground">{t('common.loading', 'Loading...')}</span>
                </div>
              ) : (
                <div className="prose prose-sm dark:prose-invert max-w-none prose-headings:text-foreground prose-p:text-foreground prose-strong:text-foreground prose-code:text-primary prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-pre:bg-muted prose-hr:border-border">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      table: ({ children }) => (
                        <div className="overflow-x-auto my-4">
                          <table className="min-w-full border-collapse border border-border text-sm">
                            {children}
                          </table>
                        </div>
                      ),
                      thead: ({ children }) => (
                        <thead className="bg-muted">{children}</thead>
                      ),
                      th: ({ children }) => (
                        <th className="border border-border px-3 py-2 text-left font-semibold">
                          {children}
                        </th>
                      ),
                      td: ({ children }) => (
                        <td className="border border-border px-3 py-2">{children}</td>
                      ),
                    }}
                  >
                    {devGuideContent}
                  </ReactMarkdown>
                </div>
              )}
            </ScrollArea>
          </div>
        </DialogContent>
      </Dialog>

      {/* AI Program Chat Modal */}
      <AiProgramChatModal
        open={aiChatOpen}
        onOpenChange={setAiChatOpen}
        onSaveCode={handleAiSaveCode}
        accounts={accounts}
        accountsLoading={accountsLoading}
        programId={selectedProgram?.id}
        programName={selectedProgram?.name}
        programDescription={selectedProgram?.description}
        currentCode={formCode}
        isNewProgram={isCreating}
      />

      {/* Preview Run Dialog */}
      {previewRunBinding && (
        <BindingPreviewRunDialog
          open={previewRunOpen}
          onOpenChange={setPreviewRunOpen}
          bindingId={previewRunBinding.id}
          programName={previewRunBinding.program_name}
          accountName={previewRunBinding.account_name}
        />
      )}

      {/* Backtest Modal */}
      {backtestBinding && (
        <BacktestModal
          open={backtestOpen}
          onOpenChange={setBacktestOpen}
          binding={backtestBinding}
        />
      )}
    </div>
  )
}
