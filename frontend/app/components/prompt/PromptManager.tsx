import { useEffect, useMemo, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'react-hot-toast'
import { ExternalLink } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  getPromptTemplates,
  updatePromptTemplate,
  upsertPromptBinding,
  deletePromptBinding,
  getAccounts,
  createPromptTemplate,
  copyPromptTemplate,
  deletePromptTemplate,
  updatePromptTemplateName,
  getVariablesReference,
  PromptTemplate,
  PromptBinding,
  TradingAccount,
} from '@/lib/api'
import { STRATEGY_RADAR_URL } from '@/lib/strategyRadar'
import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import PromptPreviewDialog from './PromptPreviewDialog'
import AiPromptChatModal from './AiPromptChatModal'
import PremiumRequiredModal from '@/components/ui/PremiumRequiredModal'

interface BindingFormState {
  id?: number
  accountId?: number
  promptTemplateId?: number
}

const DEFAULT_BINDING_FORM: BindingFormState = {
  accountId: undefined,
  promptTemplateId: undefined,
}

export default function PromptManager() {
  const { t, i18n } = useTranslation()
  const [templates, setTemplates] = useState<PromptTemplate[]>([])
  const [bindings, setBindings] = useState<PromptBinding[]>([])
  const [accounts, setAccounts] = useState<TradingAccount[]>([])
  const [accountsLoading, setAccountsLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [templateDraft, setTemplateDraft] = useState<string>('')
  const [nameDraft, setNameDraft] = useState<string>('')
  const [descriptionDraft, setDescriptionDraft] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [bindingSaving, setBindingSaving] = useState(false)
  const [bindingForm, setBindingForm] = useState<BindingFormState>(DEFAULT_BINDING_FORM)
  const [previewDialogOpen, setPreviewDialogOpen] = useState(false)

  /**
   * Initial view ID from URL parameter: #prompt-management?view=ID
   * Parsed once at component mount to handle deep linking from Hyper AI.
   */
  const initialViewIdRef = useRef<number | null>((() => {
    const hash = window.location.hash
    const hashParamIndex = hash.indexOf('?')
    if (hashParamIndex !== -1) {
      const hashParams = new URLSearchParams(hash.slice(hashParamIndex))
      const viewId = hashParams.get('view')
      if (viewId) {
        const numId = Number(viewId)
        if (!isNaN(numId)) {
          // Clean up URL after reading
          window.history.replaceState({}, '', window.location.pathname + hash.slice(0, hashParamIndex))
          return numId
        }
      }
    }
    return null
  })())

  // New template dialog
  const [newTemplateDialogOpen, setNewTemplateDialogOpen] = useState(false)
  const [newTemplateName, setNewTemplateName] = useState('')
  const [newTemplateDescription, setNewTemplateDescription] = useState('')
  const [creating, setCreating] = useState(false)

  // Copy template dialog
  const [copyDialogOpen, setCopyDialogOpen] = useState(false)
  const [copyName, setCopyName] = useState('')
  const [copying, setCopying] = useState(false)

  // AI Prompt Chat Modal
  const [aiChatModalOpen, setAiChatModalOpen] = useState(false)

  // Premium Modal
  const [premiumModalOpen, setPremiumModalOpen] = useState(false)

  // Variables Reference Modal
  const [variablesRefModalOpen, setVariablesRefModalOpen] = useState(false)
  const [variablesRefContent, setVariablesRefContent] = useState<string>('')
  const [variablesRefLoading, setVariablesRefLoading] = useState(false)
  const [variablesRefLang, setVariablesRefLang] = useState<string>('')

  // Clear cached content when language changes
  useEffect(() => {
    setVariablesRefContent('')
    setVariablesRefLang('')
  }, [i18n.language])

  // Auth context
  const { user, membership } = useAuth()

  const selectedTemplate = useMemo(
    () => templates.find((tpl) => tpl.id === selectedId) || null,
    [templates, selectedId],
  )

  const loadTemplates = async () => {
    setLoading(true)
    try {
      const data = await getPromptTemplates()
      setTemplates(data.templates)
      setBindings(data.bindings)

      // Check for initial view ID from URL (deep link from Hyper AI)
      const initialViewId = initialViewIdRef.current
      const effectiveSelectedId = initialViewId ?? selectedId

      if (!effectiveSelectedId && data.templates.length > 0) {
        const first = data.templates[0]
        setSelectedId(first.id)
        setTemplateDraft(first.templateText)
        setNameDraft(first.name)
        setDescriptionDraft(first.description ?? '')
      } else if (effectiveSelectedId) {
        const tpl = data.templates.find((item) => item.id === effectiveSelectedId)
        if (tpl) {
          setSelectedId(effectiveSelectedId)  // Ensure state is set
          setTemplateDraft(tpl.templateText)
          setNameDraft(tpl.name)
          setDescriptionDraft(tpl.description ?? '')
        }
      }

      // Clear the ref after first use
      initialViewIdRef.current = null
    } catch (err) {
      console.error(err)
      toast.error(err instanceof Error ? err.message : 'Failed to load prompt templates')
    } finally {
      setLoading(false)
    }
  }

  const loadAccounts = async () => {
    setAccountsLoading(true)
    try {
      const list = await getAccounts()
      setAccounts(list)
    } catch (err) {
      console.error(err)
      toast.error(err instanceof Error ? err.message : 'Failed to load AI traders')
    } finally {
      setAccountsLoading(false)
    }
  }

  useEffect(() => {
    loadTemplates()
    loadAccounts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSelectTemplate = (id: string) => {
    const numId = Number(id)
    setSelectedId(numId)
    const tpl = templates.find((item) => item.id === numId)
    setTemplateDraft(tpl?.templateText ?? '')
    setNameDraft(tpl?.name ?? '')
    setDescriptionDraft(tpl?.description ?? '')
  }

  const handleSaveTemplate = async () => {
    if (!selectedTemplate) return
    setSaving(true)
    try {
      const updated = await updatePromptTemplate(selectedTemplate.key, {
        templateText: templateDraft,
        description: descriptionDraft,
        updatedBy: 'ui',
      })

      // Also update name if changed
      if (nameDraft !== selectedTemplate.name) {
        await updatePromptTemplateName(selectedTemplate.id, {
          name: nameDraft,
          description: descriptionDraft,
          updatedBy: 'ui',
        })
      }

      setTemplates((prev) =>
        prev.map((tpl) =>
          tpl.id === selectedTemplate.id
            ? { ...tpl, ...updated, name: nameDraft, description: descriptionDraft }
            : tpl,
        ),
      )
      toast.success('Prompt template saved')
    } catch (err) {
      console.error(err)
      toast.error(err instanceof Error ? err.message : 'Failed to save prompt template')
    } finally {
      setSaving(false)
    }
  }

  const handleCreateTemplate = async () => {
    if (!newTemplateName.trim()) {
      toast.error('Please enter a template name')
      return
    }

    setCreating(true)
    try {
      const created = await createPromptTemplate({
        name: newTemplateName,
        description: newTemplateDescription,
        createdBy: 'ui',
      })

      setTemplates((prev) => [created, ...prev])
      setSelectedId(created.id)
      setTemplateDraft(created.templateText)
      setNameDraft(created.name)
      setDescriptionDraft(created.description ?? '')

      setNewTemplateDialogOpen(false)
      setNewTemplateName('')
      setNewTemplateDescription('')
      toast.success('Template created')
    } catch (err) {
      console.error(err)
      toast.error(err instanceof Error ? err.message : 'Failed to create template')
    } finally {
      setCreating(false)
    }
  }

  const handleCopyTemplate = async () => {
    if (!selectedTemplate) return

    setCopying(true)
    try {
      const copied = await copyPromptTemplate(selectedTemplate.id, {
        newName: copyName || undefined,
        createdBy: 'ui',
      })

      setTemplates((prev) => [copied, ...prev])
      setSelectedId(copied.id)
      setTemplateDraft(copied.templateText)
      setNameDraft(copied.name)
      setDescriptionDraft(copied.description ?? '')

      setCopyDialogOpen(false)
      setCopyName('')
      toast.success('Template copied')
    } catch (err) {
      console.error(err)
      toast.error(err instanceof Error ? err.message : 'Failed to copy template')
    } finally {
      setCopying(false)
    }
  }

  const handleDeleteTemplate = async () => {
    if (!selectedTemplate) return

    if (selectedTemplate.isSystem === 'true') {
      toast.error('Cannot delete system templates')
      return
    }

    if (!confirm(`Delete template "${selectedTemplate.name}"?`)) {
      return
    }

    try {
      await deletePromptTemplate(selectedTemplate.id)
      setTemplates((prev) => prev.filter((tpl) => tpl.id !== selectedTemplate.id))

      // Select first available template
      const remaining = templates.filter((tpl) => tpl.id !== selectedTemplate.id)
      if (remaining.length > 0) {
        setSelectedId(remaining[0].id)
        setTemplateDraft(remaining[0].templateText)
        setNameDraft(remaining[0].name)
        setDescriptionDraft(remaining[0].description ?? '')
      } else {
        setSelectedId(null)
        setTemplateDraft('')
        setNameDraft('')
        setDescriptionDraft('')
      }

      toast.success('Template deleted')
    } catch (err) {
      console.error(err)
      toast.error(err instanceof Error ? err.message : 'Failed to delete template')
    }
  }

  const handleBindingSubmit = async () => {
    if (!bindingForm.accountId) {
      toast.error('Please select an AI trader')
      return
    }
    if (!bindingForm.promptTemplateId) {
      toast.error('Please select a prompt template')
      return
    }

    setBindingSaving(true)
    try {
      const payload = await upsertPromptBinding({
        id: bindingForm.id,
        accountId: bindingForm.accountId,
        promptTemplateId: bindingForm.promptTemplateId,
        updatedBy: 'ui',
      })

      setBindings((prev) => {
        const existingIndex = prev.findIndex((item) => item.id === payload.id)
        if (existingIndex !== -1) {
          const next = [...prev]
          next[existingIndex] = payload
          return next
        }
        return [...prev, payload].sort((a, b) => a.accountName.localeCompare(b.accountName))
      })
      setBindingForm(DEFAULT_BINDING_FORM)
      toast.success('Prompt binding saved')
    } catch (err) {
      console.error(err)
      toast.error(err instanceof Error ? err.message : 'Failed to save binding')
    } finally {
      setBindingSaving(false)
    }
  }

  const handleDeleteBinding = async (bindingId: number) => {
    try {
      await deletePromptBinding(bindingId)
      setBindings((prev) => prev.filter((item) => item.id !== bindingId))
      toast.success('Binding deleted')
    } catch (err) {
      console.error(err)
      toast.error(err instanceof Error ? err.message : 'Failed to delete binding')
    }
  }

  const handleEditBinding = (binding: PromptBinding) => {
    setBindingForm({
      id: binding.id,
      accountId: binding.accountId,
      promptTemplateId: binding.promptTemplateId,
    })
  }

  const handleAiWriteClick = () => {
    // Check if user is logged in
    if (!user) {
      toast.error('Please log in to use this feature')
      return
    }

    // Limited Time Free - skip premium check
    // Open AI generator
    setAiChatModalOpen(true)
  }

  const handleSubscribe = () => {
    setPremiumModalOpen(false)
    window.open('https://www.akooi.com/#pricing-section', '_blank')
  }

  const handleOpenVariablesRef = async () => {
    setVariablesRefModalOpen(true)
    const currentLang = i18n.language.startsWith('zh') ? 'zh' : 'en'
    // Load if no content or language changed
    if (!variablesRefContent || variablesRefLang !== currentLang) {
      setVariablesRefLoading(true)
      try {
        const data = await getVariablesReference(currentLang)
        setVariablesRefContent(data.content)
        setVariablesRefLang(currentLang)
      } catch (err) {
        console.error(err)
        toast.error(t('prompt.loadVariablesFailed', 'Failed to load variables reference'))
      } finally {
        setVariablesRefLoading(false)
      }
    }
  }

  useEffect(() => {
    if (selectedTemplate) {
      setTemplateDraft(selectedTemplate.templateText)
      setNameDraft(selectedTemplate.name)
      setDescriptionDraft(selectedTemplate.description ?? '')
    }
  }, [selectedTemplate])

  const accountOptions = useMemo(() => {
    return accounts
      .filter((account) => account.account_type === 'AI')
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [accounts])

  return (
    <>
      <div className="h-full w-full overflow-hidden flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row gap-4 h-full overflow-hidden">
        {/* LEFT COLUMN - Template Selection + Edit Area */}
        <div className="flex-1 flex flex-col h-full gap-4 overflow-hidden">
          <Card className="flex-1 flex flex-col h-full overflow-hidden">
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CardTitle className="text-base">{t('prompt.templateEditor', 'Prompt Template Editor')}</CardTitle>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleOpenVariablesRef}
                  >
                    📖 {t('prompt.variablesGuide', 'Variables Guide')}
                  </Button>
                  <Button size="sm" variant="outline" asChild>
                    <a href={STRATEGY_RADAR_URL} target="_blank" rel="noopener noreferrer">
                      {t('prompt.strategyIdeasLink', 'Need strategy ideas?')}
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </Button>
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setNewTemplateDialogOpen(true)}
                  >
                    ➕ {t('prompt.new', 'New')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setCopyDialogOpen(true)}
                    disabled={!selectedTemplate}
                  >
                    📋 {t('prompt.copy', 'Copy')}
                  </Button>
                  {selectedTemplate && selectedTemplate.isSystem !== 'true' && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={handleDeleteTemplate}
                      className="text-destructive"
                    >
                      🗑️ {t('common.delete', 'Delete')}
                    </Button>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-4 h-[100%] flex-1 overflow-hidden">
              {/* Template Selection Dropdown */}
              <div>
                <label className="text-xs uppercase text-muted-foreground">{t('prompt.template', 'Template')}</label>
                <Select
                  value={selectedId ? String(selectedId) : ''}
                  onValueChange={handleSelectTemplate}
                  disabled={loading}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={loading ? t('common.loading', 'Loading...') : t('prompt.selectTemplate', 'Select a template')} />
                  </SelectTrigger>
                  <SelectContent>
                    {templates.map((tpl) => (
                      <SelectItem key={tpl.id} value={String(tpl.id)}>
                        <div className="flex flex-col items-start">
                          <span className="font-semibold">
                            {tpl.name}
                            {tpl.isSystem === 'true' && (
                              <span className="ml-2 text-xs text-muted-foreground">[{t('prompt.system', 'System')}]</span>
                            )}
                            {tpl.isSystem === 'true' && (
                              <span className="ml-2 text-xs text-muted-foreground">[{t('prompt.testTemplate', 'Test template')}]</span>
                            )}
                          </span>
                          <span className="text-xs text-muted-foreground">{tpl.key}</span>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Name Input */}
              <div>
                <label className="text-xs uppercase text-muted-foreground">{t('prompt.templateName', 'Template Name')}</label>
                <Input
                  value={nameDraft}
                  onChange={(event) => setNameDraft(event.target.value)}
                  placeholder={t('prompt.templateNamePlaceholder', 'Template name')}
                  disabled={!selectedTemplate || saving}
                />
              </div>

              {/* Description Input */}
              <div>
                <label className="text-xs uppercase text-muted-foreground">{t('common.description', 'Description')}</label>
                <Input
                  value={descriptionDraft}
                  onChange={(event) => setDescriptionDraft(event.target.value)}
                  placeholder={t('prompt.descriptionPlaceholder', 'Prompt description')}
                  disabled={!selectedTemplate || saving}
                />
                {selectedTemplate?.isSystem === 'true' && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('prompt.testTemplateHint', 'For learning and workflow testing only. Not a profit guarantee.')}
                  </p>
                )}
              </div>

              {/* Template Text Area */}
              <div className="flex-1 flex flex-col overflow-hidden">
                <label className="text-xs uppercase text-muted-foreground mb-2">{t('prompt.templateText', 'Template Text')}</label>
                <textarea
                  className="flex-1 w-full rounded-md border bg-background p-3 font-mono text-sm leading-relaxed focus:outline-none focus:ring-1 focus:ring-ring"
                  value={templateDraft}
                  onChange={(event) => setTemplateDraft(event.target.value)}
                  disabled={!selectedTemplate || saving}
                />
              </div>

              {/* Action Buttons */}
              <div className="flex justify-between mt-2 gap-2">
                <div className="flex gap-2">
                  <Button
                    onClick={handleAiWriteClick}
                    disabled={!selectedTemplate || saving}
                    className="bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white border-0 shadow-lg hover:shadow-xl transition-all"
                  >
                    ✨ {t('prompt.aiWritePrompt', 'AI Write Strategy Prompt')}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => setPreviewDialogOpen(true)}
                    disabled={!selectedTemplate || saving}
                  >
                    💡 {t('prompt.previewFilled', 'Preview Filled')}
                  </Button>
                </div>
                <div className="flex gap-2">
                  <Button onClick={handleSaveTemplate} disabled={!selectedTemplate || saving}>
                    {t('prompt.saveTemplate', 'Save Template')}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* RIGHT COLUMN - Binding Management */}
        <Card className="flex flex-col w-full lg:w-[40rem] flex-shrink-0 overflow-hidden">
          <CardHeader>
            <CardTitle className="text-base">{t('prompt.accountBindings', 'Account Prompt Bindings')}</CardTitle>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col gap-6">
            {/* Bindings Table */}
            <div className="flex-1 overflow-auto">
              <table className="min-w-full text-sm">
                <thead className="text-left text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-4">{t('prompt.account', 'Account')}</th>
                    <th className="py-2 pr-4">{t('prompt.model', 'Model')}</th>
                    <th className="py-2 pr-4">{t('prompt.template', 'Template')}</th>
                    <th className="py-2 pr-4 text-right">{t('common.actions', 'Actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {bindings.map((binding) => (
                    <tr key={binding.id} className="border-t">
                      <td className="py-2 pr-4">{binding.accountName}</td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {binding.accountModel || '—'}
                      </td>
                      <td className="py-2 pr-4">{binding.promptName}</td>
                      <td className="py-2 pr-4 text-right space-x-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleEditBinding(binding)}
                        >
                          {t('common.edit', 'Edit')}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive"
                          onClick={() => handleDeleteBinding(binding.id)}
                        >
                          {t('common.delete', 'Delete')}
                        </Button>
                      </td>
                    </tr>
                  ))}
                  {bindings.length === 0 && (
                    <tr>
                      <td colSpan={4} className="py-4 text-center text-muted-foreground">
                        {t('prompt.noBindings', 'No prompt bindings configured.')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Binding Form */}
            <div className="space-y-4 border-t pt-4">
              <div className="grid grid-cols-1 gap-3">
                <div>
                  <label className="text-xs uppercase text-muted-foreground">
                    {t('prompt.aiTrader', 'AI Trader')}
                  </label>
                  <Select
                    value={
                      bindingForm.accountId !== undefined ? String(bindingForm.accountId) : ''
                    }
                    onValueChange={(value) =>
                      setBindingForm((prev) => ({
                        ...prev,
                        accountId: Number(value),
                      }))
                    }
                    disabled={accountsLoading}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={accountsLoading ? t('common.loading', 'Loading...') : t('common.select', 'Select')} />
                    </SelectTrigger>
                    <SelectContent>
                      {accountOptions.map((account) => (
                        <SelectItem key={account.id} value={String(account.id)}>
                          {account.name}
                          {account.model ? ` (${account.model})` : ''}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs uppercase text-muted-foreground">{t('prompt.template', 'Template')}</label>
                  <Select
                    value={
                      bindingForm.promptTemplateId !== undefined
                        ? String(bindingForm.promptTemplateId)
                        : ''
                    }
                    onValueChange={(value) =>
                      setBindingForm((prev) => ({
                        ...prev,
                        promptTemplateId: Number(value),
                      }))
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('common.select', 'Select')} />
                    </SelectTrigger>
                    <SelectContent>
                      {templates.map((tpl) => (
                        <SelectItem key={tpl.id} value={String(tpl.id)}>
                          {tpl.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => setBindingForm(DEFAULT_BINDING_FORM)}
                  disabled={bindingSaving}
                >
                  {t('common.reset', 'Reset')}
                </Button>
                <Button onClick={handleBindingSubmit} disabled={bindingSaving}>
                  {t('prompt.saveBinding', 'Save Binding')}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>

      {/* Preview Dialog */}
      {selectedTemplate && (
        <PromptPreviewDialog
          open={previewDialogOpen}
          onOpenChange={setPreviewDialogOpen}
          templateKey={selectedTemplate.key}
          templateName={selectedTemplate.name}
          templateText={templateDraft}
        />
      )}

      {/* New Template Dialog */}
      <Dialog open={newTemplateDialogOpen} onOpenChange={setNewTemplateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('prompt.createNewTemplate', 'Create New Template')}</DialogTitle>
            <DialogDescription>
              {t('prompt.createNewTemplateDesc', 'Create a new prompt template from scratch. It will be initialized with the default template content.')}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div>
              <label className="text-sm font-medium">{t('prompt.templateName', 'Template Name')}</label>
              <Input
                value={newTemplateName}
                onChange={(e) => setNewTemplateName(e.target.value)}
                placeholder={t('prompt.myCustomTemplate', 'My Custom Template')}
              />
            </div>
            <div>
              <label className="text-sm font-medium">{t('prompt.descriptionOptional', 'Description (Optional)')}</label>
              <Input
                value={newTemplateDescription}
                onChange={(e) => setNewTemplateDescription(e.target.value)}
                placeholder={t('prompt.templateDescPlaceholder', 'Description of this template')}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setNewTemplateDialogOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button onClick={handleCreateTemplate} disabled={creating}>
              {t('common.create', 'Create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Copy Template Dialog */}
      <Dialog open={copyDialogOpen} onOpenChange={setCopyDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('prompt.copyTemplate', 'Copy Template')}</DialogTitle>
            <DialogDescription>
              {t('prompt.copyTemplateDesc', 'Create a copy of "{name}". You can specify a new name or leave blank to auto-generate.').replace('{name}', selectedTemplate?.name || '')}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div>
              <label className="text-sm font-medium">{t('prompt.newNameOptional', 'New Name (Optional)')}</label>
              <Input
                value={copyName}
                onChange={(e) => setCopyName(e.target.value)}
                placeholder={`${selectedTemplate?.name} (Copy)`}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCopyDialogOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button onClick={handleCopyTemplate} disabled={copying}>
              {t('prompt.copy', 'Copy')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* AI Prompt Chat Modal */}
      <AiPromptChatModal
        open={aiChatModalOpen}
        onOpenChange={setAiChatModalOpen}
        accounts={accounts}
        accountsLoading={accountsLoading}
        onApplyPrompt={(promptText) => {
          setTemplateDraft(promptText)
        }}
        promptId={selectedId}
        promptName={selectedTemplate?.name}
      />

      {/* Premium Required Modal */}
      <PremiumRequiredModal
        isOpen={premiumModalOpen}
        onClose={() => setPremiumModalOpen(false)}
        onSubscribe={handleSubscribe}
        featureName="AI Strategy Prompt Generator"
        description="Let AI help you write professional trading strategy prompts with natural language conversation."
      />

      {/* Variables Reference Modal */}
      <Dialog open={variablesRefModalOpen} onOpenChange={setVariablesRefModalOpen}>
        <DialogContent className="max-w-5xl max-h-[80vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>{t('prompt.variablesReference', 'Strategy Variables Reference')}</DialogTitle>
            <DialogDescription>
              {t('prompt.variablesReferenceDesc', 'All available variables for prompt templates')}
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 flex gap-4 overflow-hidden">
            {/* Left Sidebar - AI Prompt CTA */}
            <div className="w-56 flex-shrink-0">
              <div className="bg-gradient-to-b from-purple-50 to-pink-50 dark:from-purple-950/30 dark:to-pink-950/30 border border-purple-200 dark:border-purple-800 rounded-lg p-4 h-full flex flex-col">
                <div className="text-2xl mb-3">✨</div>
                <p className="text-sm font-medium text-foreground mb-3">
                  {t('prompt.needHelpWriting', 'Need help writing prompts?')}
                </p>
                <p className="text-xs text-muted-foreground mb-3">
                  {t('prompt.tryAiPromptGeneration', 'Try')} <span className="font-semibold text-purple-600 dark:text-purple-400">{t('prompt.aiPromptGeneration', 'AI Prompt Generation')}</span> {t('prompt.toCreateStrategies', 'to create professional trading strategies.')}
                </p>
                <div className="text-xs text-muted-foreground space-y-1 mb-4">
                  <p className="font-medium">{t('prompt.aiWillHelp', 'AI will help you:')}</p>
                  <ul className="list-disc list-inside space-y-0.5 text-[11px]">
                    <li>{t('prompt.generateOptimized', 'Generate optimized prompts')}</li>
                    <li>{t('prompt.selectVariables', 'Select appropriate variables')}</li>
                    <li>{t('prompt.addRiskManagement', 'Add risk management')}</li>
                    <li>{t('prompt.refineViaConversation', 'Refine via conversation')}</li>
                  </ul>
                </div>
                <Button
                  size="sm"
                  onClick={() => {
                    setVariablesRefModalOpen(false)
                    handleAiWriteClick()
                  }}
                  className="w-full bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white border-0 shadow-md hover:shadow-lg transition-all text-xs"
                >
                  ✨ {t('prompt.tryAiWrite', 'Try AI Write')}
                </Button>
                <p className="text-[10px] text-muted-foreground mt-2 text-center">
                  {t('prompt.premiumFeature', 'Premium feature')}
                </p>
              </div>
            </div>

            {/* Right Content - Variables Documentation */}
            <ScrollArea className="flex-1 pr-4">
            {variablesRefLoading ? (
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
                    code: ({ children, className }) => {
                      const isInline = !className
                      return isInline ? (
                        <code className="bg-muted text-primary px-1 py-0.5 rounded text-xs">
                          {children}
                        </code>
                      ) : (
                        <code className={className}>{children}</code>
                      )
                    },
                  }}
                >
                  {variablesRefContent}
                </ReactMarkdown>
              </div>
            )}
          </ScrollArea>
          </div>
          <DialogFooter>
            <Button onClick={() => setVariablesRefModalOpen(false)}>
              {t('common.close', 'Close')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
