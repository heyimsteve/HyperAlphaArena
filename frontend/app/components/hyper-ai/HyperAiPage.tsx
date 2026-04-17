/**
 * HyperAiPage - Independent page for Hyper AI (three-column layout)
 * Left: Conversation list
 * Center: Chat area
 * Right: Config panel
 */
import { useState, useEffect, useRef, memo } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import PacmanLoader from '@/components/ui/pacman-loader'
import {
  Plus,
  Send,
  Settings,
  MessageSquare,
  ChevronDown,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
  Loader2,
  Bot,
  Pencil,
  X,
  CheckCircle2,
  AlertCircle,
  User,
  Wrench,
  Play,
  Brain,
  MessageCircle,
  Blocks,
  Search as SearchIcon
} from 'lucide-react'
import { pollAiStream } from '@/lib/pollAiStream'
import BotIntegrationModal from './BotIntegrationModal'
import NotificationConfigModal from './NotificationConfigModal'
import ToolConfigModal, { type ToolInfo } from './ToolConfigModal'

interface Conversation {
  id: number
  title: string
  message_count: number
  is_bot_conversation?: boolean
  updated_at: string
}

interface ToolCallEntry {
  type: 'tool_call' | 'tool_result' | 'reasoning' | 'subagent_progress' | 'confirmation_required' | 'tool_error'
  name?: string
  tool?: string
  args?: Record<string, unknown>
  result?: string
  content?: string
  subagent?: string
  step?: string
  round?: number
  max_rounds?: number
  taskId?: string
  confirmationId?: string
  description?: string
  status?: 'pending' | 'confirmed' | 'cancelled' | 'failed'
  message?: string
  severity?: string
}

// API format for tool_calls_log from database
interface ToolCallLogEntry {
  tool: string
  args: Record<string, unknown>
  result: string
}

/**
 * Represents a successfully created entity that should be displayed as a card.
 * Extracted from tool_calls_log when save_xxx tools return success: true.
 */
interface CreatedEntityCard {
  type: 'prompt' | 'program' | 'signal_pool' | 'ai_trader' | 'factor'
  id: number
  name: string
  content?: string  // template_text for prompt, code for program, JSON for signal_pool
  viewUrl: string
}

interface Message {
  id?: number
  role: 'user' | 'assistant'
  content: string
  reasoning_snapshot?: string
  tool_calls_log?: string
  is_complete?: boolean
  interrupt_reason?: string
  created_at?: string
  // Streaming state
  isStreaming?: boolean
  statusText?: string
  toolCalls?: ToolCallEntry[]
  isInterrupted?: boolean
  interruptedRound?: number
}

interface CompressionPoint {
  message_id: number
  summary: string
  compressed_at: string
}

interface SkillInfo {
  name: string
  description: string
  description_zh: string
  command: string
  enabled: boolean
}

interface TokenUsage {
  current_tokens: number
  max_tokens: number
  usage_ratio: number
  show_warning: boolean
}

interface LLMProvider {
  id: string
  name: string
  models: string[]
  base_url?: string
}

// Memory category icons and colors
const MEMORY_CATEGORY_STYLES: Record<string, { icon: string; color: string }> = {
  preference: { icon: '🎯', color: 'text-blue-500' },
  decision: { icon: '⚡', color: 'text-amber-500' },
  lesson: { icon: '📖', color: 'text-green-500' },
  insight: { icon: '💡', color: 'text-purple-500' },
  context: { icon: '📌', color: 'text-gray-500' },
}

// Memory Modal component - read-only view of AI memories
function MemoryModal({
  open,
  onClose
}: {
  open: boolean
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [memories, setMemories] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (open) {
      setLoading(true)
      fetch('/api/hyper-ai/memories?limit=50')
        .then(res => res.json())
        .then(data => setMemories(data.memories || []))
        .catch(() => setMemories([]))
        .finally(() => setLoading(false))
    }
  }, [open])

  if (!open) return null

  // Group memories by category
  const grouped: Record<string, any[]> = {}
  for (const m of memories) {
    const cat = m.category || 'context'
    if (!grouped[cat]) grouped[cat] = []
    grouped[cat].push(m)
  }

  const categoryOrder = ['preference', 'decision', 'lesson', 'insight', 'context']

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="w-full max-w-3xl bg-background rounded-lg shadow-xl flex flex-col"
           style={{ height: '600px' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b shrink-0">
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-primary" />
            <h2 className="text-lg font-semibold">
              {t('hyperAi.memory.title', 'What Hyper AI Remembered')}
            </h2>
            {memories.length > 0 && (
              <span className="text-xs text-muted-foreground ml-2">
                {t('hyperAi.memory.items', '{{count}} memories', { count: memories.length })}
              </span>
            )}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : memories.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <Brain className="w-12 h-12 text-muted-foreground/30 mb-3" />
              <p className="text-sm text-muted-foreground max-w-sm">
                {t('hyperAi.memory.empty')}
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {categoryOrder.map(cat => {
                const items = grouped[cat]
                if (!items || items.length === 0) return null
                const style = MEMORY_CATEGORY_STYLES[cat] || MEMORY_CATEGORY_STYLES.context
                const label = t(`hyperAi.memory.category.${cat}`, cat)
                return (
                  <div key={cat}>
                    <div className="flex items-center gap-2 mb-2">
                      <span>{style.icon}</span>
                      <span className={`text-sm font-medium ${style.color}`}>{label}</span>
                      <span className="text-xs text-muted-foreground">({items.length})</span>
                    </div>
                    <div className="space-y-2 ml-6">
                      {items.map((m: any) => (
                        <MemoryItem key={m.id} memory={m} />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function MemoryItem({ memory }: { memory: any }) {
  const importance = memory.importance || 0.5
  const stars = Math.round(importance * 5)
  const date = memory.created_at
    ? new Date(memory.created_at).toLocaleDateString()
    : ''

  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
      <p className="leading-relaxed">{memory.content}</p>
      <div className="flex items-center gap-3 mt-1.5 text-xs text-muted-foreground">
        <span>{'★'.repeat(stars)}{'☆'.repeat(5 - stars)}</span>
        {date && <span>{date}</span>}
        {memory.source && <span className="capitalize">{memory.source}</span>}
      </div>
    </div>
  )
}

// LLM Config Modal component
function LLMConfigModal({
  open,
  onClose,
  providers,
  currentProfile,
  onSaved
}: {
  open: boolean
  onClose: () => void
  providers: LLMProvider[]
  currentProfile: any
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const [selectedProvider, setSelectedProvider] = useState(currentProfile?.llm_provider || '')
  const [apiKey, setApiKey] = useState('')
  const [modelInput, setModelInput] = useState(currentProfile?.llm_model || '')
  const [customBaseUrl, setCustomBaseUrl] = useState(currentProfile?.llm_base_url || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)

  const currentProvider = providers.find(p => p.id === selectedProvider)

  useEffect(() => {
    if (open) {
      setSelectedProvider(currentProfile?.llm_provider || '')
      setModelInput(currentProfile?.llm_model || '')
      setCustomBaseUrl(currentProfile?.llm_base_url || '')
      setApiKey('')
      setError('')
      setSuccess(false)
    }
  }, [open, currentProfile])

  // When provider changes, set default model if current model is empty
  useEffect(() => {
    if (selectedProvider && !modelInput) {
      const provider = providers.find(p => p.id === selectedProvider)
      if (provider && provider.models.length > 0) {
        setModelInput(provider.models[0])
      }
    }
  }, [selectedProvider])

  const handleSave = async () => {
    if (!selectedProvider || !apiKey) {
      setError(t('hyperAi.onboarding.fillRequired', 'Please fill in all required fields'))
      return
    }

    if (selectedProvider === 'custom' && !customBaseUrl) {
      setError(t('hyperAi.onboarding.baseUrlRequired', 'Base URL is required for custom provider'))
      return
    }

    setSaving(true)
    setError('')

    try {
      const res = await fetch('/api/hyper-ai/profile/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: selectedProvider,
          api_key: apiKey,
          model: modelInput,
          base_url: selectedProvider === 'custom' ? customBaseUrl : undefined
        })
      })

      if (!res.ok) {
        const errData = await res.json()
        throw new Error(errData.detail || 'Connection test failed')
      }

      setSuccess(true)
      setTimeout(() => {
        onSaved()
        onClose()
      }, 800)
    } catch (e: any) {
      setError(e.message || 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="w-full max-w-md bg-background rounded-lg shadow-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">{t('hyperAi.configTitle', 'Hyper AI Config')}</h2>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="w-4 h-4" />
          </Button>
        </div>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label>{t('hyperAi.onboarding.provider', 'AI Provider')}</Label>
            <Select value={selectedProvider} onValueChange={(v) => { setSelectedProvider(v); setModelInput('') }}>
              <SelectTrigger>
                <SelectValue placeholder={t('hyperAi.onboarding.selectProvider', 'Select provider')} />
              </SelectTrigger>
              <SelectContent>
                {providers.map(p => (
                  <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {selectedProvider === 'custom' && (
            <div className="space-y-2">
              <Label>{t('hyperAi.onboarding.baseUrl', 'Base URL')}</Label>
              <Input
                value={customBaseUrl}
                onChange={e => setCustomBaseUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
              />
            </div>
          )}

          <div className="space-y-2">
            <Label>{t('hyperAi.onboarding.apiKey', 'API Key')}</Label>
            <Input
              type="password"
              value={apiKey}
              onChange={e => setApiKey(e.target.value)}
              placeholder={currentProfile?.llm_configured ? t('hyperAi.onboarding.apiKeyConfigured', 'Enter new API key to update') : 'sk-...'}
            />
          </div>

          {selectedProvider && (
            <div className="space-y-2">
              <Label>{t('hyperAi.onboarding.model', 'Model')}</Label>
              <div className="flex gap-1">
                <Input
                  value={modelInput}
                  onChange={e => setModelInput(e.target.value)}
                  placeholder={t('hyperAi.onboarding.modelPlaceholder', 'Enter or select model')}
                  className="flex-1"
                />
                {currentProvider && currentProvider.models.length > 0 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="icon" className="shrink-0">
                        <ChevronDown className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="max-h-60 overflow-y-auto">
                      {currentProvider.models.map(m => (
                        <DropdownMenuItem key={m} onClick={() => setModelInput(m)}>
                          {m}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="flex items-center gap-2 text-destructive text-sm">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        )}

        {success && (
          <div className="flex items-center gap-2 text-green-600 text-sm">
            <CheckCircle2 className="w-4 h-4" />
            {t('hyperAi.onboarding.connectionSuccess', 'Connection successful!')}
          </div>
        )}

        <div className="flex gap-3 pt-2">
          <Button variant="outline" onClick={onClose} className="flex-1">
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button onClick={handleSave} disabled={!selectedProvider || !apiKey || saving} className="flex-1">
            {saving && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
            {saving ? t('hyperAi.onboarding.testing', 'Testing...') : t('common.save', 'Save')}
          </Button>
        </div>
      </div>
    </div>
  )
}

// Welcome message component
function BotConvIcon() {
  return (
    <svg viewBox="0 0 1024 1024" className="w-4 h-4 flex-shrink-0" fill="currentColor">
      <path d="M0 0m128 0l768 0q128 0 128 128l0 768q0 128-128 128l-768 0q-128 0-128-128l0-768q0-128 128-128Z" fill="#E1EBFF"/>
      <path d="M640.704 213.12A75.136 75.136 0 0 0 588.544 192c-18.944 0-37.44 7.552-50.688 21.12a89.536 89.536 0 0 0-20.032 72.32v14.336A97.152 97.152 0 0 1 479.616 364.8c-26.88 26.048-61.632 43.52-98.688 48.384-6.4 0-17.728-2.304-19.648-2.304a124.672 124.672 0 0 0-140.672 46.528 33.088 33.088 0 0 0 4.544 39.68L328.384 600.32l-131.584 182.272a32.192 32.192 0 0 0 10.944 44.608c10.624 6.4 23.488 6.4 34.048 0l179.968-133.504 104.768 104.768a32 32 0 0 0 38.592 4.928 127.168 127.168 0 0 0 46.848-143.68v-5.312l-3.392-11.712c1.92-32.896 16.256-63.936 39.68-86.976a122.24 122.24 0 0 1 77.184-50.304h14.72c25.344 3.84 51.072-3.392 70.72-19.648a71.424 71.424 0 0 0 4.928-96L640.64 213.12z" fill="#3478FF"/>
    </svg>
  )
}

function TelegramSmallIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 text-[#26A5E4]" fill="currentColor">
      <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/>
    </svg>
  )
}

function DiscordSmallIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 text-[#5865F2]" fill="currentColor">
      <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.947 2.418-2.157 2.418z"/>
    </svg>
  )
}

function NotificationBellSmallIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 1024 1024" fill="currentColor">
      <path d="M512 0c282.666667 0 512 229.333333 512 512S794.666667 1024 512 1024 0 794.666667 0 512 229.333333 0 512 0z" fill="#2E74EE" opacity=".12" />
      <path d="M505.6 771.2L309.333333 611.2h-29.866666c-19.2 0-34.133333-14.933333-34.133334-34.133333V442.666667c0-19.2 14.933333-33.066667 34.133334-33.066667h36.266666l188.8-155.733333s48-30.933333 48 26.666666v462.933334c0 36.266667-20.266667 38.4-34.133333 34.133333-8.533333-2.133333-12.8-6.4-12.8-6.4z m117.333333-160c-6.4 0-12.8-2.133333-17.066666-7.466667-8.533333-9.6-7.466667-24.533333 2.133333-32 17.066667-14.933333 26.666667-36.266667 26.666667-58.666666s-9.6-43.733333-25.6-58.666667c-9.6-8.533333-9.6-23.466667-2.133334-32 8.533333-9.6 22.4-10.666667 32-2.133333 25.6 23.466667 40.533333 57.6 40.533334 92.8 0 35.2-14.933333 69.333333-41.6 92.8-4.266667 3.2-9.6 5.333333-14.933334 5.333333z m21.333334 88.533333c-8.533333 0-17.066667-5.333333-21.333334-13.866666-4.266667-11.733333 1.066667-24.533333 12.8-28.8 58.666667-23.466667 97.066667-77.866667 97.066667-139.733334s-38.4-116.266667-98.133333-139.733333c-11.733333-4.266667-17.066667-18.133333-12.8-28.8s18.133333-17.066667 29.866666-12.8c37.333333 14.933333 68.266667 39.466667 90.666667 70.4 23.466667 33.066667 35.2 70.4 35.2 110.933333 0 39.466667-11.733333 77.866667-35.2 109.866667-22.4 32-53.333333 56.533333-90.666667 70.4-2.133333 1.066667-4.266667 2.133333-7.466666 2.133333z" fill="#2E74EE" />
    </svg>
  )
}

function WelcomeMessage({
  nickname,
  t,
  onSuggestionClick
}: {
  nickname?: string
  t: any
  onSuggestionClick: (question: string) => void
}) {
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [isNewUser, setIsNewUser] = useState(true)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/hyper-ai/suggestions')
      .then(res => res.json())
      .then(data => {
        setSuggestions(data.suggestions || [])
        setIsNewUser(data.is_new_user ?? true)
      })
      .catch(() => {
        setSuggestions([])
        setIsNewUser(true)
      })
      .finally(() => setLoading(false))
  }, [])

  const greeting = nickname
    ? t('hyperAi.welcomeWithName', { name: nickname, defaultValue: `你好，${nickname}！我是 Hyper AI，你的专属交易助手。` })
    : t('hyperAi.welcomeNoName', '你好！我是 Hyper AI，Hyper Alpha Arena 的智能助手。')

  // Default suggestions for new users (follows i18n)
  const defaultSuggestions = [
    t('hyperAi.defaultSuggestions.intro', 'What can you help me with?'),
    t('hyperAi.defaultSuggestions.setup', 'Guide me through the initial setup'),
    t('hyperAi.defaultSuggestions.first', 'I want to create my first trading strategy'),
    t('hyperAi.defaultSuggestions.walletSignals', 'How do I connect Hyper Insight wallet signals?'),
  ]

  const displaySuggestions = (isNewUser || suggestions.length === 0) ? defaultSuggestions : suggestions

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-4">
      <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center mb-4">
        <Bot className="w-8 h-8 text-primary" />
      </div>
      <p className="text-lg mb-4">{greeting}</p>
      <div className="text-sm text-muted-foreground space-y-1 max-w-md">
        <p>{t('hyperAi.welcomeCapabilities', '我可以帮你：')}</p>
        <ul className="text-left list-disc list-inside space-y-1 mt-2">
          <li>{t('hyperAi.capability1', '了解系统功能和使用方法')}</li>
          <li>{t('hyperAi.capability2', '生成和优化 AI 交易策略')}</li>
          <li>{t('hyperAi.capability3', '管理 AI 交易员和钱包配置')}</li>
          <li>{t('hyperAi.capability4', '分析市场数据和交易表现')}</li>
        </ul>
        <p className="mt-4">{t('hyperAi.welcomePrompt', '有什么想了解的，直接问我就行。')}</p>
      </div>

      {/* Suggestion buttons */}
      {!loading && displaySuggestions.length > 0 && (
        <div className="mt-6 space-y-2 w-full max-w-md">
          {displaySuggestions.map((question, idx) => (
            <button
              key={idx}
              onClick={() => onSuggestionClick(question)}
              className="w-full px-4 py-3 text-left text-sm rounded-lg border border-border bg-card hover:bg-accent hover:border-primary/50 transition-colors"
            >
              {question}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function HyperAiPage() {
  const { t, i18n } = useTranslation()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [currentConvId, setCurrentConvId] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [compressionPoints, setCompressionPoints] = useState<CompressionPoint[]>([])
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null)
  const [inputValue, setInputValue] = useState('')
  const [sending, setSending] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [providers, setProviders] = useState<LLMProvider[]>([])
  const [profile, setProfile] = useState<any>(null)
  const [nickname, setNickname] = useState<string>('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [showConfig, setShowConfig] = useState(true)
  const [showConfigModal, setShowConfigModal] = useState(false)
  const [showMemoryModal, setShowMemoryModal] = useState(false)
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [activeSkill, setActiveSkill] = useState<string | null>(null)
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [skillsEditMode, setSkillsEditMode] = useState(false)
  const [pendingSkillToggles, setPendingSkillToggles] = useState<Record<string, boolean>>({})
  const [showBotModal, setShowBotModal] = useState(false)
  const [showDiscordBotModal, setShowDiscordBotModal] = useState(false)
  const [botConfig, setBotConfig] = useState<{ platform: string; bot_username: string | null; status: string } | null>(null)
  const [discordBotConfig, setDiscordBotConfig] = useState<{ platform: string; bot_username: string | null; bot_app_id?: string; status: string } | null>(null)
  const [showNotificationModal, setShowNotificationModal] = useState(false)
  const [notificationCount, setNotificationCount] = useState(0)
  const [externalTools, setExternalTools] = useState<ToolInfo[]>([])
  const [showToolModal, setShowToolModal] = useState(false)
  const [selectedTool, setSelectedTool] = useState<ToolInfo | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Get current language
  const currentLang = i18n.language?.startsWith('zh') ? 'zh' : 'en'

  useEffect(() => {
    fetchConversations()
    fetchProviders()
    fetchProfile()
    fetchSkills()
    fetchBotConfig()
    fetchDiscordBotConfig()
    fetchNotificationConfig()
    fetchExternalTools()
  }, [])

  useEffect(() => {
    // Don't fetch messages while sending - it would overwrite the streaming message
    if (currentConvId && !sending) {
      fetchMessages(currentConvId)
    }
  }, [currentConvId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  // Check for pending prompt from other pages (e.g. Factor Analysis "Ask AI")
  useEffect(() => {
    const pending = localStorage.getItem('hyper-ai-pending-prompt')
    if (pending) {
      localStorage.removeItem('hyper-ai-pending-prompt')
      setInputValue(pending)
      // Focus the textarea after a brief delay
      setTimeout(() => textareaRef.current?.focus(), 200)
    }
  }, [])

  const fetchBotConfig = async () => {
    try {
      const res = await fetch('/api/bot/config/telegram')
      const data = await res.json()
      setBotConfig(data.config || null)
    } catch (e) {
      console.error('Failed to fetch bot config:', e)
    }
  }

  const fetchDiscordBotConfig = async () => {
    try {
      const res = await fetch('/api/bot/config/discord')
      const data = await res.json()
      setDiscordBotConfig(data.config || null)
    } catch (e) {
      console.error('Failed to fetch discord bot config:', e)
    }
  }

  const fetchNotificationConfig = async () => {
    try {
      const res = await fetch('/api/bot/notification-config')
      const data = await res.json()
      const cfg = data.config || { ai_trader: true, program_trader: true, signal_pools: {} }
      let count = 0
      if (cfg.ai_trader) count++
      if (cfg.program_trader) count++
      count += Object.values(cfg.signal_pools as Record<string, boolean>).filter(Boolean).length
      setNotificationCount(count)
    } catch (e) {
      console.error('Failed to fetch notification config:', e)
    }
  }

  const fetchExternalTools = async () => {
    try {
      const res = await fetch('/api/hyper-ai/tools')
      const data = await res.json()
      setExternalTools(data.tools || [])
    } catch (e) {
      console.error('Failed to fetch external tools:', e)
    }
  }

  const fetchConversations = async () => {
    try {
      const res = await fetch('/api/hyper-ai/conversations')
      const data = await res.json()
      setConversations(data.conversations || [])
    } catch (e) {
      console.error('Failed to fetch conversations:', e)
    }
  }

  const fetchMessages = async (convId: number) => {
    try {
      const res = await fetch(`/api/hyper-ai/conversations/${convId}/messages`)
      const data = await res.json()
      setMessages(data.messages || [])
      setCompressionPoints(data.compression_points || [])
      setTokenUsage(data.token_usage || null)
    } catch (e) {
      console.error('Failed to fetch messages:', e)
    }
  }

  const fetchProviders = async () => {
    try {
      const res = await fetch('/api/hyper-ai/providers')
      const data = await res.json()
      setProviders(data.providers || [])
    } catch (e) {
      console.error('Failed to fetch providers:', e)
    }
  }

  const fetchProfile = async () => {
    try {
      const res = await fetch('/api/hyper-ai/profile')
      const data = await res.json()
      setProfile(data)
      if (data.nickname) {
        setNickname(data.nickname)
      }
    } catch (e) {
      console.error('Failed to fetch profile:', e)
    }
  }

  const fetchSkills = async () => {
    try {
      const res = await fetch('/api/hyper-ai/skills')
      const data = await res.json()
      setSkills(data.skills || [])
    } catch (e) {
      console.error('Failed to fetch skills:', e)
    }
  }

  const toggleSkill = async (skillName: string, enabled: boolean) => {
    setSkillsLoading(true)
    try {
      await fetch(`/api/hyper-ai/skills/${skillName}/toggle`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled })
      })
      setSkills(prev => prev.map(s =>
        s.name === skillName ? { ...s, enabled } : s
      ))
    } catch (e) {
      console.error('Failed to toggle skill:', e)
    } finally {
      setSkillsLoading(false)
    }
  }

  const handleSkillsEditSave = async () => {
    setSkillsLoading(true)
    try {
      for (const [name, enabled] of Object.entries(pendingSkillToggles)) {
        await fetch(`/api/hyper-ai/skills/${name}/toggle`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled })
        })
      }
      setSkills(prev => prev.map(s =>
        pendingSkillToggles[s.name] !== undefined
          ? { ...s, enabled: pendingSkillToggles[s.name] }
          : s
      ))
    } catch (e) {
      console.error('Failed to save skill toggles:', e)
    } finally {
      setSkillsLoading(false)
      setSkillsEditMode(false)
      setPendingSkillToggles({})
    }
  }

  const handleSkillsEditCancel = () => {
    setSkillsEditMode(false)
    setPendingSkillToggles({})
  }

  const handleNewConversation = () => {
    // Lazy creation: just clear current state, don't create in DB yet
    setCurrentConvId(null)
    setMessages([])
    setCompressionPoints([])
    setTokenUsage(null)
    setActiveSkill(null)
  }

  const handleSend = async () => {
    if (!inputValue.trim() || sending) return

    const userMessage = inputValue.trim()
    setInputValue('')
    setSending(true)
    setStreamingContent('')

    // Add user message and placeholder assistant message
    const tempAssistantId = Date.now()
    setMessages(prev => [
      ...prev,
      { role: 'user', content: userMessage },
      {
        role: 'assistant',
        content: '',
        isStreaming: true,
        statusText: t('hyperAi.connecting', 'Connecting...'),
        toolCalls: []
      }
    ])

    try {
      const res = await fetch('/api/hyper-ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMessage,
          conversation_id: currentConvId,
          lang: currentLang
        })
      })

      const data = await res.json()
      if (data.task_id) {
        // Poll for streaming response
        pollTaskResponse(data.task_id, data.conversation_id)
        if (!currentConvId) {
          setCurrentConvId(data.conversation_id)
        }
      }
    } catch (e) {
      console.error('Failed to send message:', e)
      // Remove placeholder on error
      setMessages(prev => prev.slice(0, -1))
      setSending(false)
    }
  }

  const handleToolConfirmation = async (taskId: string, confirmationId: string, confirmed: boolean) => {
    const nextStatus = confirmed ? 'confirmed' : 'cancelled'
    setMessages(prev => prev.map(message => ({
      ...message,
      toolCalls: message.toolCalls?.map(entry =>
        entry.type === 'confirmation_required' && entry.confirmationId === confirmationId
          ? { ...entry, status: nextStatus }
          : entry
      )
    })))

    try {
      const res = await fetch('/api/hyper-ai/confirm-tool', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          confirmation_id: confirmationId,
          confirmed,
        }),
      })
      if (!res.ok) {
        throw new Error(`Confirmation failed: ${res.status}`)
      }
    } catch (e) {
      console.error('Failed to submit tool confirmation:', e)
      setMessages(prev => prev.map(message => ({
        ...message,
        toolCalls: message.toolCalls?.map(entry =>
          entry.type === 'confirmation_required' && entry.confirmationId === confirmationId
            ? { ...entry, status: 'failed' }
            : entry
        )
      })))
    }
  }

  const pollTaskResponse = async (taskId: string, convId: number) => {
    let content = ''
    let reasoning = ''
    let toolCalls: ToolCallEntry[] = []
    let doneToolCallsLog: ToolCallLogEntry[] | null = null
    let doneReasoningSnapshot: string | null = null
    let isInterrupted = false
    let interruptedRound = 0

    // Update currentConvId immediately if not set
    if (!currentConvId && convId) {
      setCurrentConvId(convId)
    }

    try {
      const pollResult = await pollAiStream(taskId, {
        interval: 300,
        onChunk: (chunk) => {
          const eventType = chunk.event_type
          const data = chunk.data

          if (eventType === 'content' && data.text) {
            content += data.text
            setStreamingContent(content)
            setMessages(prev => prev.map((m, idx) =>
              idx === prev.length - 1 && m.isStreaming
                ? { ...m, content, statusText: '' }
                : m
            ))
          } else if (eventType === 'reasoning' && data.content) {
            reasoning += data.content
            const reasoningText = data.content as string
            setMessages(prev => prev.map((m, idx) =>
              idx === prev.length - 1 && m.isStreaming
                ? {
                    ...m,
                    statusText: `Thinking: ${reasoningText.slice(0, 80)}...`,
                    toolCalls: [...(m.toolCalls || []), { type: 'reasoning', content: reasoningText }],
                  }
                : m
            ))
          } else if (eventType === 'tool_call' && data.name) {
            toolCalls.push({ type: 'tool_call', name: data.name, args: data.args || {} })
            setMessages(prev => prev.map((m, idx) =>
              idx === prev.length - 1 && m.isStreaming
                ? {
                    ...m,
                    statusText: `${t('hyperAi.calling', 'Calling')} ${data.name}...`,
                    toolCalls: [...(m.toolCalls || []), { type: 'tool_call', name: data.name, args: data.args }]
                  }
                : m
            ))
          } else if (eventType === 'tool_result' && data.name) {
            toolCalls.push({ type: 'tool_result', name: data.name, result: data.result })
            setMessages(prev => prev.map((m, idx) =>
              idx === prev.length - 1 && m.isStreaming
                ? {
                    ...m,
                    statusText: '',
                    toolCalls: [...(m.toolCalls || []), { type: 'tool_result', name: data.name, result: data.result }]
                  }
                : m
            ))
          } else if (eventType === 'skill_loaded' && data.skill_name) {
            setActiveSkill(data.skill_name as string)
          } else if (eventType === 'subagent_progress') {
            const agent = data.subagent || 'Agent'
            let statusMsg = ''
            const progressEntry: any = { type: 'subagent_progress', subagent: agent, step: data.step }

            if (data.step === 'reasoning') {
              statusMsg = `${agent}: ${t('hyperAi.subagentProcessing', 'processing')}...`
              progressEntry.content = data.content || ''
            } else if (data.step === 'tool_call') {
              statusMsg = `${agent}: → ${data.tool || ''}`
              progressEntry.tool = data.tool || ''
            } else if (data.step === 'tool_result') {
              statusMsg = `${agent}: ← ${data.tool || ''}`
              progressEntry.tool = data.tool || ''
            } else if (data.step === 'tool_round') {
              const roundInfo = data.round && data.max_rounds ? ` ${data.round}/${data.max_rounds}` : (data.round ? ` ${data.round}` : '')
              statusMsg = `${agent}: ${t('hyperAi.subagentRound', 'round')}${roundInfo}...`
              progressEntry.round = data.round
              progressEntry.max_rounds = data.max_rounds
            } else {
              statusMsg = `${agent}: ${t('hyperAi.subagentProcessing', 'processing')}...`
            }

            setMessages(prev => prev.map((m, idx) =>
              idx === prev.length - 1 && m.isStreaming
                ? { ...m, statusText: statusMsg, toolCalls: [...(m.toolCalls || []), progressEntry] }
                : m
            ))
          } else if (eventType === 'retry') {
            const attempt = data.attempt || 2
            const maxRetries = data.max_retries || 3
            setMessages(prev => prev.map((m, idx) =>
              idx === prev.length - 1 && m.isStreaming
                ? { ...m, statusText: `${t('hyperAi.retrying', 'Retrying')} (${attempt}/${maxRetries})...` }
                : m
            ))
          } else if (eventType === 'confirmation_required') {
            const confirmationEntry: ToolCallEntry = {
              type: 'confirmation_required',
              taskId,
              confirmationId: data.confirmation_id,
              name: data.tool_name,
              args: data.args || {},
              description: data.description || '',
              status: 'pending',
            }
            setMessages(prev => prev.map((m, idx) =>
              idx === prev.length - 1 && m.isStreaming
                ? {
                    ...m,
                    statusText: t('hyperAi.confirmationRequired', 'Confirmation required'),
                    toolCalls: [...(m.toolCalls || []), confirmationEntry],
                  }
                : m
            ))
            // Force scroll after DOM renders the confirmation card
            setTimeout(() => {
              messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
            }, 100)
          } else if (eventType === 'tool_error') {
            const errorEntry: ToolCallEntry = {
              type: 'tool_error',
              name: data.name,
              message: data.message || data.code || '',
              severity: data.severity || data.status,
            }
            setMessages(prev => prev.map((m, idx) =>
              idx === prev.length - 1 && m.isStreaming
                ? {
                    ...m,
                    statusText: data.message || t('hyperAi.toolWarning', 'Tool warning'),
                    toolCalls: [...(m.toolCalls || []), errorEntry],
                  }
                : m
            ))
          } else if (eventType === 'interrupted') {
            isInterrupted = true
            interruptedRound = data.round || 0
            if (data.conversation_id) {
              setCurrentConvId(data.conversation_id)
            }
          } else if (eventType === 'error') {
            console.error('Stream error:', data.message)
          } else if (eventType === 'done') {
            if (data.content) content = data.content
            if (data.conversation_id) setCurrentConvId(data.conversation_id)
            if (data.token_usage) setTokenUsage(data.token_usage)
            if (data.compression_points) setCompressionPoints(data.compression_points)
            if (data.tool_calls_log) doneToolCallsLog = data.tool_calls_log
            if (data.reasoning_snapshot) doneReasoningSnapshot = data.reasoning_snapshot
          }
        },
        onTaskLost: () => {
          // Task buffer expired — reload conversation to get final result
          if (convId) {
            fetchMessages(convId)
          }
        },
      })

      if (pollResult.status === 'lost') {
        setSending(false)
        return
      }

      // Finalize message - prefer backend done event data, fallback to streaming conversion
      const localToolCallsLog = toolCalls.filter(tc => tc.type === 'tool_call' || tc.type === 'tool_result')
        .reduce((acc: ToolCallLogEntry[], tc) => {
          if (tc.type === 'tool_call' && tc.name) {
            acc.push({ tool: tc.name, args: tc.args || {}, result: '' })
          } else if (tc.type === 'tool_result' && tc.name && acc.length > 0) {
            // Find matching tool call and add result
            const lastCall = acc[acc.length - 1]
            if (lastCall.tool === tc.name) {
              lastCall.result = tc.result || ''
            }
          }
          return acc
        }, [])
      const finalToolCallsLog = doneToolCallsLog || (localToolCallsLog.length > 0 ? localToolCallsLog : null)
      const finalReasoning = doneReasoningSnapshot || reasoning || undefined

      setMessages(prev => prev.map((m, idx) =>
        idx === prev.length - 1 && m.isStreaming
          ? {
              ...m,
              content: content || m.content,
              reasoning_snapshot: finalReasoning,
              tool_calls_log: finalToolCallsLog ? JSON.stringify(finalToolCallsLog) : undefined,
              isStreaming: false,
              statusText: undefined,
              toolCalls: undefined,
              isInterrupted,
              interruptedRound: isInterrupted ? interruptedRound : undefined,
              is_complete: !isInterrupted
            }
          : m
      ))
      setStreamingContent('')
      setSending(false)
      fetchConversations()
    } catch (e) {
      console.error('Polling error:', e)
      setMessages(prev => prev.map((m, idx) =>
        idx === prev.length - 1 && m.isStreaming
          ? { ...m, isStreaming: false, content: content || t('hyperAi.connectionLost', 'Connection lost') }
          : m
      ))
      setSending(false)
    }
  }

  const handleContinue = () => {
    setInputValue(t('hyperAi.continueMessage', 'Please continue'))
    setTimeout(() => handleSend(), 100)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex h-full">
      {/* Left: Conversation List */}
      <div className={`border-r flex flex-col transition-all duration-200 ${sidebarCollapsed ? 'w-0 overflow-hidden border-r-0' : 'w-64'}`}>
        <div className="p-3 flex items-center gap-2">
          <Button onClick={handleNewConversation} className="flex-1" size="sm">
            <Plus className="w-4 h-4 mr-2" />
            {t('hyperAi.newChat', 'New Chat')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="px-2 shrink-0"
            onClick={() => setSidebarCollapsed(true)}
            title={t('hyperAi.collapseSidebar', 'Collapse sidebar')}
          >
            <PanelLeftClose className="w-4 h-4" />
          </Button>
        </div>
        <ScrollArea className="flex-1">
          <div className="p-2 space-y-1">
            {conversations.map(conv => (
              <button
                key={conv.id}
                onClick={() => setCurrentConvId(conv.id)}
                className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  conv.is_bot_conversation
                    ? 'border border-blue-500/30 bg-blue-500/5 mb-1 '
                    : ''
                }${
                  currentConvId === conv.id
                    ? 'bg-secondary text-secondary-foreground'
                    : 'hover:bg-muted text-muted-foreground'
                }`}
              >
                {conv.is_bot_conversation ? (
                  <>
                    <div className="flex items-center gap-2">
                      <BotConvIcon />
                      <span className="truncate font-medium">{conv.title}</span>
                    </div>
                    <div className="flex items-center gap-1.5 mt-1.5 ml-6">
                      {botConfig?.status === 'connected' && <TelegramSmallIcon />}
                      {discordBotConfig?.status === 'connected' && <DiscordSmallIcon />}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="flex items-center gap-2">
                      <MessageSquare className="w-4 h-4 flex-shrink-0" />
                      <span className="truncate">{conv.title}</span>
                    </div>
                    <div className="text-xs text-muted-foreground mt-1">
                      {conv.message_count} {t('hyperAi.messages', 'messages')}
                    </div>
                  </>
                )}
              </button>
            ))}
          </div>
        </ScrollArea>
      </div>

      {/* Center: Chat Area */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {sidebarCollapsed && (
          <Button
            variant="ghost"
            size="sm"
            className="absolute top-2 left-2 z-10 px-2"
            onClick={() => setSidebarCollapsed(false)}
            title={t('hyperAi.expandSidebar', 'Expand sidebar')}
          >
            <PanelLeftOpen className="w-4 h-4" />
          </Button>
        )}
        {messages.length === 0 ? (
          <WelcomeMessage
            nickname={nickname}
            t={t}
            onSuggestionClick={(question) => {
              setInputValue(question)
              setTimeout(() => handleSend(), 100)
            }}
          />
        ) : (
          <ScrollArea className="flex-1 p-4">
            <div className="space-y-4 max-w-5xl mx-auto">
              {messages.map((msg, idx) => {
                // Check if this message is a compression point
                const compressionPoint = compressionPoints.find(cp => cp.message_id === msg.id)
                return (
                  <div key={idx}>
                    <MessageBubble
                      message={msg}
                      onContinue={msg.isInterrupted && !sending ? handleContinue : undefined}
                      onToolConfirmation={handleToolConfirmation}
                      t={t}
                    />
                    {compressionPoint && (
                      <div className="flex items-center gap-3 my-4 text-xs text-muted-foreground">
                        <div className="flex-1 border-t border-dashed border-muted-foreground/30" />
                        <span className="px-2 py-1 bg-muted rounded text-[10px]">
                          {t('hyperAi.compressionPoint', 'Context compressed')}
                        </span>
                        <div className="flex-1 border-t border-dashed border-muted-foreground/30" />
                      </div>
                    )}
                  </div>
                )
              })}
              <div ref={messagesEndRef} />
            </div>
          </ScrollArea>
        )}

        {/* Input Area */}
        <div className="px-4 pb-4 pt-2">
          <div className="max-w-5xl mx-auto relative">
            <textarea
              ref={textareaRef}
              value={inputValue}
              onChange={e => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t('hyperAi.inputPlaceholder', 'Type a message...')}
              disabled={sending}
              className="w-full min-h-[80px] max-h-[200px] rounded-xl border border-input bg-transparent px-4 py-3 pb-12 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 resize-y"
              rows={3}
            />
            <div className="absolute bottom-3 right-3 flex items-center gap-2">
              {tokenUsage?.show_warning && (
                <p className="text-xs text-amber-500">
                  {t('hyperAi.contextWarning', 'Context remaining: {{percent}}% · Compressing soon', { percent: Math.max(0, Math.round((1 - tokenUsage.usage_ratio) * 100)) })}
                </p>
              )}
              <Button
                onClick={handleSend}
                disabled={!inputValue.trim() || sending}
                size="icon"
                className="rounded-full h-8 w-8 shrink-0"
              >
                {sending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Right: Config Panel */}
      {showConfig && (
        <div className="w-[500px] border-l p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium flex items-center gap-1.5">
              <Settings className="w-4 h-4 shrink-0" />
              {t('hyperAi.configTitle', 'Hyper AI Config')}
            </h3>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setShowConfigModal(true)}>
              <Pencil className="w-3.5 h-3.5" />
            </Button>
          </div>

          {profile && (
            <div
              className="space-y-1.5 text-sm cursor-pointer hover:bg-muted/50 rounded-lg p-2 -mx-2 transition-colors"
              onClick={() => setShowConfigModal(true)}
            >
              <div className="flex items-center">
                <span className="text-muted-foreground shrink-0 w-[72px]">Provider</span>
                <span className="truncate">{profile.llm_provider || 'Not configured'}</span>
              </div>
              <div className="flex items-center">
                <span className="text-muted-foreground shrink-0 w-[72px]">Model</span>
                <span className="truncate">{profile.llm_model || '-'}</span>
              </div>
              {profile.llm_base_url && (
                <div className="flex items-center">
                  <span className="text-muted-foreground shrink-0 w-[72px]">Base URL</span>
                  <span className="truncate">{profile.llm_base_url}</span>
                </div>
              )}
            </div>
          )}

          {/* Memory Entry */}
          <div className="pt-4">
            <button
              onClick={() => setShowMemoryModal(true)}
              className="w-full flex items-center gap-1.5 py-1 rounded-lg text-sm hover:bg-muted/50 transition-colors text-left"
            >
              <Brain className="w-4 h-4 text-primary shrink-0" />
              <span className="text-sm font-medium">{t('hyperAi.memory.button', 'Memory')}</span>
              <ChevronRight className="w-3 h-3 text-muted-foreground ml-auto shrink-0" />
            </button>
          </div>

          <div className="pt-4">
            <div className="flex items-center justify-between mb-1">
              <h4 className="text-sm font-medium flex items-center gap-1.5">
                <svg className="w-4 h-4 shrink-0" viewBox="0 0 1024 1024" fill="currentColor">
                  <path d="M556.8 960H166.4c-25.6 0-51.2-12.8-70.4-25.6-19.2-19.2-32-44.8-32-70.4v-115.2c6.4-19.2 12.8-38.4 32-51.2 12.8-6.4 19.2-12.8 32-12.8s25.6 6.4 44.8 12.8H192c12.8 6.4 19.2 6.4 32 6.4s25.6 0 32-6.4c12.8-6.4 19.2-12.8 25.6-19.2 6.4-6.4 12.8-19.2 19.2-25.6 6.4-12.8 6.4-19.2 6.4-32s0-25.6-6.4-32c-6.4-12.8-12.8-19.2-19.2-25.6s-19.2-12.8-25.6-19.2c-12.8-6.4-19.2-6.4-32-6.4s-19.2 0-32 6.4h-6.4-6.4c-6.4 6.4-19.2 6.4-25.6 12.8-12.8 6.4-25.6 6.4-38.4 6.4-19.2 0-32-12.8-38.4-25.6-6.4-12.8-12.8-25.6-12.8-44.8V390.4c0-25.6 12.8-51.2 32-70.4 19.2-19.2 44.8-32 70.4-32h83.2c-6.4-19.2-6.4-32-6.4-51.2 0-25.6 6.4-51.2 12.8-70.4l38.4-57.6c19.2-19.2 38.4-32 57.6-38.4 25.6-12.8 44.8-12.8 70.4-12.8s51.2 6.4 70.4 12.8l57.6 38.4c19.2 19.2 32 38.4 38.4 57.6 12.8 25.6 12.8 44.8 12.8 70.4 0 19.2 0 38.4-6.4 51.2h25.6c25.6 0 51.2 12.8 70.4 32 19.2 19.2 25.6 44.8 25.6 70.4v19.2c0 12.8-12.8 32-38.4 32-25.6 0-32-12.8-38.4-25.6v-25.6c0-6.4 0-12.8-6.4-19.2-6.4-6.4-6.4-6.4-19.2-6.4H441.6l51.2-64c19.2-19.2 25.6-38.4 25.6-64 0-12.8 0-32-6.4-44.8-6.4-12.8-12.8-25.6-25.6-32-12.8-12.8-19.2-19.2-32-25.6-12.8-6.4-25.6-6.4-44.8-6.4-12.8 0-25.6 0-44.8 6.4-12.8 6.4-25.6 12.8-32 25.6-12.8 12.8-19.2 19.2-25.6 32-6.4 12.8-6.4 25.6-6.4 44.8 0 12.8 0 25.6 6.4 38.4 6.4 12.8 12.8 25.6 19.2 32l51.2 64H153.6c-6.4 0-12.8 0-19.2 6.4-6.4 6.4-6.4 12.8-6.4 19.2v89.6s6.4 0 6.4-6.4c6.4 0 6.4-6.4 12.8-6.4 19.2-6.4 38.4-12.8 64-12.8 19.2 0 44.8 6.4 64 12.8 19.2 6.4 38.4 19.2 51.2 32 12.8 12.8 25.6 32 32 51.2 6.4 19.2 12.8 38.4 12.8 64 0 19.2-6.4 44.8-12.8 64-6.4 19.2-19.2 38.4-32 51.2-12.8 12.8-32 25.6-51.2 32-19.2 6.4-38.4 12.8-64 12.8-19.2 0-44.8-6.4-64-12.8-6.4 0-12.8-6.4-19.2-6.4v96c0 6.4 0 12.8 6.4 19.2 6.4 6.4 12.8 6.4 19.2 6.4h396.8c19.2 6.4 25.6 19.2 25.6 38.4 6.4 25.6 0 32-19.2 38.4z m204.8-76.8c-6.4-6.4-25.6-19.2-32-19.2-6.4 0-25.6 12.8-32 19.2-6.4 6.4-19.2 12.8-25.6 12.8-6.4 0-12.8 0-12.8-6.4l-51.2-25.6c-12.8-12.8-19.2-25.6-12.8-44.8 0 0 6.4-6.4 6.4-12.8 0-12.8-6.4-19.2-12.8-25.6-6.4-6.4-19.2-12.8-25.6-12.8-12.8 0-25.6-12.8-32-32 0 0-6.4-25.6-6.4-44.8 0-19.2 6.4-44.8 6.4-44.8 6.4-19.2 12.8-32 32-32s38.4-19.2 38.4-38.4c0-6.4-6.4-12.8-6.4-12.8-6.4-19.2 0-38.4 12.8-44.8l57.6-32c6.4 0 12.8-6.4 12.8-6.4 12.8 0 19.2 6.4 25.6 12.8 6.4 6.4 25.6 19.2 32 19.2 6.4 0 25.6-12.8 32-19.2 6.4-6.4 19.2-12.8 25.6-12.8 6.4 0 12.8 0 12.8 6.4l51.2 25.6c12.8 12.8 19.2 25.6 12.8 44.8 0 0-6.4 6.4-6.4 12.8 0 19.2 19.2 38.4 38.4 38.4 12.8 0 25.6 12.8 32 32 0 0 6.4 25.6 6.4 44.8 0 19.2-6.4 44.8-6.4 44.8-6.4 19.2-12.8 32-32 32-12.8 0-19.2 6.4-25.6 12.8s-12.8 19.2-12.8 25.6c0 6.4 6.4 12.8 6.4 12.8 6.4 19.2 0 38.4-12.8 44.8l-57.6 32c-6.4 0-12.8 6.4-12.8 6.4-12.8 0-19.2-6.4-25.6-12.8z m-38.4-70.4c19.2 0 32 6.4 51.2 19.2 6.4 6.4 12.8 6.4 12.8 12.8l32-19.2c0-6.4 0-12.8-6.4-25.6 0-44.8 32-83.2 76.8-89.6v-19.2-19.2c-44.8-6.4-76.8-44.8-76.8-89.6 0-6.4 0-19.2 6.4-25.6l-32-19.2-12.8 12.8c-19.2 12.8-32 19.2-51.2 19.2s-32-6.4-51.2-19.2c-6.4-6.4-12.8-6.4-12.8-12.8l-32 19.2c0 6.4 6.4 12.8 6.4 25.6 0 44.8-32 83.2-76.8 89.6v38.4c44.8 6.4 76.8 44.8 76.8 89.6 0 6.4 0 19.2-6.4 25.6l25.6 12.8 12.8-12.8c25.6-6.4 44.8-12.8 57.6-12.8z m0-38.4c-44.8 0-83.2-38.4-83.2-83.2 0-44.8 38.4-83.2 83.2-83.2 44.8 0 83.2 38.4 83.2 83.2 6.4 44.8-32 83.2-83.2 83.2z m0-115.2c-6.4 0-19.2 6.4-25.6 6.4-6.4 6.4-6.4 12.8-6.4 25.6 0 19.2 12.8 32 32 32s32-12.8 32-32-12.8-32-32-32z" />
                </svg>
                {t('hyperAi.skills', 'Skills')}
              </h4>
              {skills.length > 0 && !skillsEditMode && (
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setSkillsEditMode(true)}>
                  <Pencil className="w-3.5 h-3.5" />
                </Button>
              )}
            </div>
            <p className="text-[10px] text-muted-foreground/60 mb-2 px-0.5">
              {t('hyperAi.skillsHint', 'Auto-loaded by AI, or type /command')}
            </p>
            {skills.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                {t('hyperAi.skillsLoading', 'Loading...')}
              </p>
            ) : (
              <>
                <div className="space-y-1">
                  {skills.map(skill => {
                    const isEnabled = pendingSkillToggles[skill.name] !== undefined
                      ? pendingSkillToggles[skill.name]
                      : skill.enabled
                    return (
                      <div
                        key={skill.name}
                        className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-muted/50 transition-colors"
                      >
                        {skillsEditMode ? (
                          <Switch
                            checked={isEnabled}
                            onCheckedChange={v => setPendingSkillToggles(prev => ({ ...prev, [skill.name]: v }))}
                            disabled={skillsLoading}
                            className="scale-75 origin-left shrink-0"
                          />
                        ) : activeSkill === skill.name ? (
                          <svg className="w-3.5 h-3.5 shrink-0 text-red-500" viewBox="0 0 1024 1024" fill="currentColor">
                            <path d="M896.512 471.04c-23.04 0-38.4 15.36-38.4 38.4s15.36 38.4 38.4 38.4 38.4-15.36 38.4-38.4c0-23.552-15.36-38.4-38.4-38.4z m-76.8-267.264c-23.04 0-38.4 15.36-38.4 38.4s15.36 38.4 38.4 38.4 38.4-15.36 38.4-38.4-15.36-38.4-38.4-38.4z m-192.512-38.4c23.04 0 38.4-15.36 38.4-38.4s-15.36-38.4-38.4-38.4-38.4 15.36-38.4 38.4 15.36 38.4 38.4 38.4z m-230.4 0c23.04 0 38.4-15.36 38.4-38.4s-15.36-38.4-38.4-38.4-38.4 15.36-38.4 38.4 15.36 38.4 38.4 38.4zM165.888 241.664c-23.04 0-38.4 15.36-38.4 38.4s15.36 38.4 38.4 38.4 38.4-15.36 38.4-38.4-15.36-38.4-38.4-38.4zM127.488 471.04c-23.04 0-38.4 15.36-38.4 38.4s15.36 38.4 38.4 38.4 38.4-15.36 38.4-38.4c0-23.552-15.36-38.4-38.4-38.4z m508.416 203.264c-24.576 16.384-53.76 32.768-82.432 36.864-12.288 4.096-28.672 4.096-41.472 4.096-12.288 0-28.672 0-41.472-4.096-33.28-4.096-57.856-20.48-82.432-36.864-49.664-36.864-82.432-98.304-82.432-163.84 0-114.688 90.624-204.8 206.336-204.8s206.336 90.112 206.336 204.8c0 65.536-32.768 126.976-82.432 163.84z m-58.88 154.112c0 37.888-25.088 62.976-62.976 62.976s-62.976-25.088-62.976-62.976v-59.392c18.944 6.144 44.032 6.144 62.976 6.144s44.032-6.144 62.976-6.144v59.392zM512 247.808c-150.016 0-269.312 118.272-269.312 267.264 0 107.008 61.44 198.656 153.6 240.64v65.024c0 65.024 50.176 114.688 115.2 114.688 65.536 0 115.2-49.664 115.2-114.688v-65.024c92.16-41.984 153.6-133.632 153.6-240.64 1.024-148.992-118.272-267.264-268.288-267.264z" />
                          </svg>
                        ) : (
                          <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${isEnabled ? 'bg-green-500' : 'bg-muted-foreground/30'}`} />
                        )}
                        <span className="text-xs truncate flex-1">
                          {t(`hyperAi.skillNames.${skill.name}`, skill.name)}
                        </span>
                        <span className="text-[10px] text-muted-foreground/50 shrink-0 font-mono">{skill.command}</span>
                      </div>
                    )
                  })}
                </div>
                {skillsEditMode && (
                  <div className="flex gap-2 pt-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleSkillsEditCancel}
                      className="h-7 px-3 text-xs"
                    >
                      {t('hyperAi.skillsCancel', 'Cancel')}
                    </Button>
                    <Button
                      size="sm"
                      onClick={handleSkillsEditSave}
                      disabled={skillsLoading}
                      className="h-7 px-3 text-xs"
                    >
                      {t('hyperAi.skillsSave', 'Save')}
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>

          {/* External Tools */}
          {externalTools.length > 0 && (
            <div className="pt-4">
              <h4 className="text-sm font-medium flex items-center gap-1.5 mb-2">
                <Wrench className="w-4 h-4 shrink-0" />
                {t('hyperAi.tools', 'Tools')}
              </h4>
              <div className="space-y-1">
                {externalTools.map(tool => (
                  <div
                    key={tool.name}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-muted/30 hover:bg-muted/50 cursor-pointer transition-colors"
                    onClick={() => { setSelectedTool(tool); setShowToolModal(true) }}
                  >
                    <SearchIcon className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                    <span className="text-xs truncate flex-1">
                      {currentLang === 'zh' ? tool.display_name_zh : tool.display_name}
                    </span>
                    {tool.configured ? (
                      <span className="w-2 h-2 rounded-full bg-green-500 shrink-0"></span>
                    ) : (
                      <span className="text-[10px] text-primary shrink-0">
                        {t('tools.setup', 'Setup')}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Bot Integrations */}
          <div className="pt-4">
            <h4 className="text-sm font-medium flex items-center gap-1.5 mb-2">
              <Blocks className="w-4 h-4 shrink-0" />
              {t('hyperAi.integrations', 'Integrations')}
              <button
                onClick={() => setShowNotificationModal(true)}
                className="ml-auto flex items-center gap-0.5 px-1.5 py-0.5 rounded-full bg-primary/10 hover:bg-primary/20 transition-colors"
                title={t('bot.notificationSettings', 'Push Notifications')}
              >
                <NotificationBellSmallIcon />
                {notificationCount > 0 && (
                  <span className="text-[10px] text-primary font-medium min-w-[14px] text-center">
                    {notificationCount}
                  </span>
                )}
              </button>
            </h4>
            <div className="space-y-2">
              {/* Telegram Bot */}
              <div
                className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-muted/30 hover:bg-muted/50 cursor-pointer transition-colors"
                onClick={() => setShowBotModal(true)}
              >
                <TelegramSmallIcon />
                <span className="text-xs">{t('hyperAi.telegramBot', 'Telegram Bot')}</span>
                {botConfig && botConfig.status === 'connected' ? (
                  <>
                    <span className="ml-auto text-[10px] text-muted-foreground">@{botConfig.bot_username}</span>
                    <span className="w-2 h-2 rounded-full bg-green-500"></span>
                  </>
                ) : (
                  <span className="ml-auto text-[10px] text-primary">
                    {t('bot.setup', 'Setup')}
                  </span>
                )}
              </div>
              {/* Discord Bot - Coming Soon */}
              <div
                className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-muted/30 hover:bg-muted/50 cursor-pointer transition-colors"
                onClick={() => setShowDiscordBotModal(true)}
              >
                <DiscordSmallIcon />
                <span className="text-xs">{t('hyperAi.discordBot', 'Discord Bot')}</span>
                {discordBotConfig && discordBotConfig.status === 'connected' ? (
                  <>
                    <span className="ml-auto text-[10px] text-muted-foreground">@{discordBotConfig.bot_username}</span>
                    <span className="w-2 h-2 rounded-full bg-green-500"></span>
                  </>
                ) : (
                  <span className="ml-auto text-[10px] text-primary">
                    {t('bot.setup', 'Setup')}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* LLM Config Modal */}
      <LLMConfigModal
        open={showConfigModal}
        onClose={() => setShowConfigModal(false)}
        providers={providers}
        currentProfile={profile}
        onSaved={fetchProfile}
      />

      {/* Memory Modal */}
      <MemoryModal
        open={showMemoryModal}
        onClose={() => setShowMemoryModal(false)}
      />

      {/* Bot Integration Modal */}
      <BotIntegrationModal
        open={showBotModal}
        onClose={() => setShowBotModal(false)}
        platform="telegram"
        onConnected={fetchBotConfig}
        currentBotUsername={botConfig?.status === 'connected' ? botConfig.bot_username : undefined}
      />

      {/* Discord Bot Integration Modal */}
      <BotIntegrationModal
        open={showDiscordBotModal}
        onClose={() => setShowDiscordBotModal(false)}
        platform="discord"
        onConnected={fetchDiscordBotConfig}
        currentBotUsername={discordBotConfig?.status === 'connected' ? discordBotConfig.bot_username : undefined}
        currentBotAppId={discordBotConfig?.bot_app_id}
      />

      {/* Notification Config Modal */}
      <NotificationConfigModal
        open={showNotificationModal}
        onClose={() => setShowNotificationModal(false)}
        onConfigChange={(count) => setNotificationCount(count)}
      />
      <ToolConfigModal
        open={showToolModal}
        onClose={() => { setShowToolModal(false); setSelectedTool(null) }}
        tool={selectedTool}
        onSaved={fetchExternalTools}
      />
    </div>
  )
}

// Message bubble component with avatar, markdown, tool calls, and interrupt recovery
// Wrapped with React.memo to prevent re-rendering when parent state (e.g. inputValue)
// changes but message props remain the same. Without memo, every keystroke in the
// textarea triggers re-render of ALL message bubbles (including expensive ReactMarkdown),
// causing noticeable input lag when conversation history is long.
const MessageBubble = memo(function MessageBubble({
  message,
  onContinue,
  onToolConfirmation,
  t
}: {
  message: Message
  onContinue?: () => void
  onToolConfirmation: (taskId: string, confirmationId: string, confirmed: boolean) => void
  t: (key: string, fallback?: string) => string
}) {
  const [showReasoning, setShowReasoning] = useState(false)
  const [showToolCalls, setShowToolCalls] = useState(false)
  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>({})
  const isUser = message.role === 'user'

  // Parse tool calls log from stored messages
  const toolCallsLog: ToolCallLogEntry[] = message.tool_calls_log
    ? (() => { try { return JSON.parse(message.tool_calls_log) } catch { return [] } })()
    : []

  /**
   * Extract created entities from tool call results.
   * These are displayed as interactive cards above the AI's text response.
   */
  const createdEntities: CreatedEntityCard[] = toolCallsLog
    .filter(entry => {
      if (!['save_prompt', 'save_program', 'save_signal_pool', 'create_ai_trader', 'save_factor'].includes(entry.tool)) return false
      try {
        const result = JSON.parse(entry.result)
        return result.success === true && result.view_url
      } catch {
        return false
      }
    })
    .map(entry => {
      const result = JSON.parse(entry.result)
      const toolToType: Record<string, CreatedEntityCard['type']> = {
        save_prompt: 'prompt',
        save_program: 'program',
        save_signal_pool: 'signal_pool',
        create_ai_trader: 'ai_trader',
        save_factor: 'factor'
      }
      return {
        type: toolToType[entry.tool],
        id: result.prompt_id || result.program_id || result.pool_id || result.trader_id || result.factor_id,
        name: result.name || result.pool_name || result.trader_name,
        content: result.template_text || result.code || result.expression || (result.signals ? JSON.stringify(result.signals, null, 2) : undefined),
        viewUrl: result.view_url
      } as CreatedEntityCard
    })

  const toggleCardExpanded = (cardId: string) => {
    setExpandedCards(prev => ({ ...prev, [cardId]: !prev[cardId] }))
  }

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
        isUser ? 'bg-primary text-primary-foreground' : 'bg-muted'
      }`}>
        {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
      </div>

      {/* Message content */}
      <div className={`max-w-[80%] rounded-lg px-4 py-3 ${
        isUser
          ? 'bg-primary text-primary-foreground'
          : 'bg-muted min-w-[400px]'
      }`}>
        {/* Status text during streaming */}
        {message.isStreaming && message.statusText && (
          <div className="flex items-center gap-2 text-xs mb-2 text-primary animate-pulse">
            <PacmanLoader className="w-6 h-3" />
            <span>{message.statusText}</span>
          </div>
        )}

        {/* Real-time tool calls during streaming */}
        {message.isStreaming && message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mb-2 text-xs bg-background/50 rounded p-2 max-h-32 overflow-y-auto">
            {message.toolCalls.filter(e => e.type !== 'confirmation_required').slice(-8).map((entry, idx) => (
              <div key={idx} className="mb-1 last:mb-0">
                {entry.type === 'tool_call' && (
                  <span className="text-blue-500">→ {entry.name}</span>
                )}
                {entry.type === 'tool_result' && (
                  <span className="text-green-500">← {entry.name}: done</span>
                )}
                {entry.type === 'reasoning' && (
                  <span className="text-gray-500 italic">{(entry.content || '').slice(0, 100)}...</span>
                )}
                {entry.type === 'subagent_progress' && entry.step === 'reasoning' && (
                  <span className="text-gray-500 italic">[{entry.subagent}] {(entry.content || '').slice(0, 100)}...</span>
                )}
                {entry.type === 'subagent_progress' && entry.step === 'tool_call' && (
                  <span className="text-blue-400">[{entry.subagent}] → {entry.tool}</span>
                )}
                {entry.type === 'subagent_progress' && entry.step === 'tool_result' && (
                  <span className="text-green-400">[{entry.subagent}] ← {entry.tool}: done</span>
                )}
                {entry.type === 'subagent_progress' && entry.step === 'tool_round' && (
                  <span className="text-orange-400">[{entry.subagent}] {t('hyperAi.subagentRound', 'round')}{entry.round ? ` ${entry.round}${entry.max_rounds ? `/${entry.max_rounds}` : ''}` : ''}</span>
                )}
                {entry.type === 'tool_error' && (
                  <span className="text-amber-500">[{entry.name}] {entry.message || entry.severity}</span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Confirmation cards rendered outside the scrollable tool log for visibility */}
        {message.isStreaming && message.toolCalls?.filter(e => e.type === 'confirmation_required').map((entry, idx) => (
          <div key={`confirm-${idx}`} className="mb-2 rounded border border-amber-300 bg-amber-50 p-3 text-amber-950 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-100">
            <div className="mb-1 flex items-center gap-1 text-sm font-medium">
              <AlertCircle className="h-4 w-4" />
              {t('hyperAi.confirmationRequired', 'Confirmation required')}
            </div>
            <div className="mb-2 text-xs text-muted-foreground">
              {entry.description || entry.name}
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                className="h-7 px-2 text-xs"
                disabled={entry.status !== 'pending' || !entry.taskId || !entry.confirmationId}
                onClick={() => entry.taskId && entry.confirmationId && onToolConfirmation(entry.taskId, entry.confirmationId, true)}
              >
                <CheckCircle2 className="mr-1 h-3 w-3" />
                {entry.status === 'confirmed' ? t('hyperAi.confirmed', 'Confirmed') : t('common.confirm', 'Confirm')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                disabled={entry.status !== 'pending' || !entry.taskId || !entry.confirmationId}
                onClick={() => entry.taskId && entry.confirmationId && onToolConfirmation(entry.taskId, entry.confirmationId, false)}
              >
                <X className="mr-1 h-3 w-3" />
                {entry.status === 'cancelled' ? t('hyperAi.cancelled', 'Cancelled') : t('common.cancel', 'Cancel')}
              </Button>
              {entry.status === 'failed' && (
                <span className="self-center text-[11px] text-destructive">
                  {t('hyperAi.confirmationFailed', 'Confirmation failed')}
                </span>
              )}
            </div>
          </div>
        ))}

        {/* Tool calls log for completed messages - moved above content */}
        {!message.isStreaming && toolCallsLog.length > 0 && (
          <details className="mb-3 text-xs border rounded-md">
            <summary className="px-3 py-2 cursor-pointer bg-muted/50 hover:bg-muted font-medium flex items-center gap-1">
              <Wrench className="w-3 h-3" />
              {t('hyperAi.toolCallsDetail', 'Tool calls')} ({toolCallsLog.length})
            </summary>
            <div className="p-3 space-y-3 max-h-96 overflow-y-auto">
              {toolCallsLog.map((entry, idx) => (
                <div key={idx} className="border-b pb-2 last:border-b-0 last:pb-0">
                  <div className="font-medium text-blue-600 dark:text-blue-400 mb-1">
                    {idx + 1}. {entry.tool}
                  </div>
                  {entry.args && Object.keys(entry.args).length > 0 && (
                    <div className="mb-1 ml-2 text-muted-foreground">
                      {Object.entries(entry.args).map(([key, value]) => (
                        <div key={key}>{key}: {JSON.stringify(value)}</div>
                      ))}
                    </div>
                  )}
                  <div className="ml-2 text-green-600 dark:text-green-400">
                    Result: {entry.result.length > 200 ? entry.result.slice(0, 200) + '...' : entry.result}
                  </div>
                </div>
              ))}
            </div>
          </details>
        )}

        {/* Created entity cards - displayed above reasoning and main content */}
        {!message.isStreaming && createdEntities.length > 0 && (
          <div className="mb-3 space-y-2">
            {createdEntities.map((entity, idx) => {
              const cardId = `${entity.type}-${entity.id}`
              const isExpanded = expandedCards[cardId]
              const typeLabels: Record<CreatedEntityCard['type'], { label: string; icon: string; color: string }> = {
                prompt: { label: t('hyperAi.createdPrompt', 'Prompt Created'), icon: '📝', color: 'border-l-green-500' },
                program: { label: t('hyperAi.createdProgram', 'Program Created'), icon: '🐍', color: 'border-l-blue-500' },
                signal_pool: { label: t('hyperAi.createdSignalPool', 'Signal Pool Created'), icon: '📊', color: 'border-l-purple-500' },
                ai_trader: { label: t('hyperAi.createdAiTrader', 'AI Trader Created'), icon: '🤖', color: 'border-l-amber-500' },
                factor: { label: t('hyperAi.createdFactor', 'Factor Saved'), icon: '📐', color: 'border-l-violet-500' }
              }
              const { label, icon, color } = typeLabels[entity.type]

              return (
                <div key={cardId} className={`border rounded-lg border-l-4 ${color} bg-background text-foreground`}>
                  {/* Card header */}
                  <div className="px-3 py-2 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span>{icon}</span>
                      <span className="text-sm font-medium text-green-600 dark:text-green-400">✓ {label}</span>
                    </div>
                  </div>

                  {/* Card body */}
                  <div className="px-3 pb-2">
                    <div className="text-sm mb-2">
                      <span className="text-muted-foreground">{t('hyperAi.entityName', 'Name')}:</span>{' '}
                      <span className="font-medium">{entity.name}</span>
                      <span className="text-muted-foreground ml-2">(ID: {entity.id})</span>
                    </div>

                    {/* Expandable content preview */}
                    {entity.content && (
                      <div className="mb-2">
                        <button
                          onClick={() => toggleCardExpanded(cardId)}
                          className="text-xs text-primary hover:underline flex items-center gap-1"
                        >
                          {isExpanded ? (
                            <><ChevronDown className="w-3 h-3" />{t('hyperAi.hideContent', 'Hide content')}</>
                          ) : (
                            <><ChevronRight className="w-3 h-3" />{t('hyperAi.viewContent', 'View content')}</>
                          )}
                        </button>
                        {isExpanded && (
                          <div className="mt-2 max-h-64 overflow-y-auto rounded border bg-muted/30 p-2">
                            <pre className="text-xs whitespace-pre-wrap font-mono">{entity.content}</pre>
                          </div>
                        )}
                      </div>
                    )}

                    {/* View in page link */}
                    <a
                      href={entity.viewUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-primary hover:underline flex items-center gap-1"
                    >
                      {t('hyperAi.viewInPage', 'View in page')} →
                    </a>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* Reasoning snapshot for completed messages - moved above content */}
        {!message.isStreaming && message.reasoning_snapshot && (
          <details className="mb-3 text-xs border rounded-md">
            <summary className="px-3 py-2 cursor-pointer bg-muted/50 hover:bg-muted font-medium">
              {t('hyperAi.reasoningProcess', 'Reasoning process')}
            </summary>
            <div className="p-3 max-h-96 overflow-y-auto">
              <pre className="whitespace-pre-wrap text-muted-foreground">{message.reasoning_snapshot}</pre>
            </div>
          </details>
        )}

        {/* Main content with Markdown */}
        <div className={`text-sm prose prose-sm max-w-none ${
          isUser ? 'prose-invert' : 'dark:prose-invert'
        }`}>
          {message.content ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                /**
                 * Custom link renderer for Hyper AI chat:
                 * - Internal hash links (e.g., /#trader-management) open in new tab
                 *   so the chat conversation is not interrupted
                 * - External links also open in new tab with security attributes
                 */
                a: ({ href, children }) => {
                  const isInternal = href?.startsWith('/') || href?.startsWith('#')
                  return (
                    <a
                      href={href}
                      target="_blank"
                      rel={isInternal ? undefined : 'noopener noreferrer'}
                      className={isUser
                        ? 'text-white underline hover:text-white/80'
                        : 'text-primary hover:underline'
                      }
                    >
                      {children}
                    </a>
                  )
                }
              }}
            >
              {message.content}
            </ReactMarkdown>
          ) : message.isStreaming ? (
            <span className="text-muted-foreground italic">{t('hyperAi.generating', 'Generating...')}</span>
          ) : null}
        </div>

        {/* Streaming cursor */}
        {message.isStreaming && message.content && (
          <span className="inline-block w-2 h-4 bg-current animate-pulse ml-1" />
        )}

        {/* Interrupted message - continue button */}
        {message.isInterrupted && onContinue && (
          <div className="mt-3 pt-3 border-t border-border/50">
            <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400 mb-2">
              <AlertCircle className="w-3 h-3" />
              <span>
                {t('hyperAi.interruptedAt', 'Interrupted at round {{round}}').replace('{{round}}', String(message.interruptedRound || '?'))}
              </span>
            </div>
            <Button size="sm" variant="outline" onClick={onContinue} className="text-xs">
              <Play className="w-3 h-3 mr-1" />
              {t('hyperAi.continueButton', 'Continue')}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
})
