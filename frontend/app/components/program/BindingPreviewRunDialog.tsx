/**
 * Preview Run Dialog for Program Bindings
 * Shows execution details with real account data
 * Layout: Left panel (action + result) | Right panel (details)
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Play, CheckCircle2, AlertCircle, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { previewRunBinding, PreviewRunResponse } from '@/lib/api'
import { toast } from 'react-hot-toast'

interface BindingPreviewRunDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  bindingId: number
  programName: string
  accountName: string
}

export function BindingPreviewRunDialog({
  open,
  onOpenChange,
  bindingId,
  programName,
  accountName,
}: BindingPreviewRunDialogProps) {
  const { t } = useTranslation()
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<PreviewRunResponse | null>(null)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({})

  const toggleSection = (section: string) => {
    setExpandedSections(prev => ({ ...prev, [section]: !prev[section] }))
  }

  const handleRun = async () => {
    setRunning(true)
    setResult(null)
    try {
      const res = await previewRunBinding(bindingId)
      setResult(res)
      if (res.success) {
        toast.success(t('programTrader.previewSuccess', 'Preview run successful'))
      }
    } catch (err) {
      console.error('Preview run failed:', err)
      toast.error(t('programTrader.previewFailed', 'Preview run failed'))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[80vh] overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Play className="h-4 w-4" />
            {t('programTrader.previewRun', 'Preview Run')}
            <span className="text-sm font-normal text-muted-foreground ml-2">
              {programName} → {accountName}
            </span>
          </DialogTitle>
        </DialogHeader>

        {/* Two-column layout */}
        <div className="flex gap-4 h-[60vh]">
          {/* LEFT PANEL: Action + Result */}
          <LeftPanel
            running={running}
            result={result}
            onRun={handleRun}
            t={t}
          />

          {/* RIGHT PANEL: Details */}
          <RightPanel
            result={result}
            expandedSections={expandedSections}
            toggleSection={toggleSection}
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}

// Left Panel: Run button + Result/Error display
function LeftPanel({ running, result, onRun, t }: any) {
  return (
    <div className="w-2/5 flex flex-col space-y-4">
      {/* Run Button */}
      <Button onClick={onRun} disabled={running} className="w-full">
        {running ? (
          <>
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            {t('programTrader.running', 'Running...')}
          </>
        ) : (
          <>
            <Play className="h-4 w-4 mr-2" />
            {t('programTrader.runPreview', 'Run Preview')}
          </>
        )}
      </Button>

      {/* Result Display */}
      {result && (
        <div className="flex-1 overflow-y-auto space-y-3">
          {/* Status Banner */}
          <div className={`p-3 rounded-md ${result.success
            ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800'
            : 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800'
          }`}>
            <div className="flex items-center gap-2">
              {result.success ? (
                <CheckCircle2 className="h-5 w-5 text-green-600 flex-shrink-0" />
              ) : (
                <AlertCircle className="h-5 w-5 text-red-600 flex-shrink-0" />
              )}
              <span className="font-medium text-sm">
                {result.success ? 'Success' : 'Error'}
              </span>
            </div>
          </div>

          {/* Decision or Error Details */}
          {result.success && result.decision ? (
            <div className="p-3 bg-muted rounded-md text-sm space-y-2">
              <div><span className="text-muted-foreground">Operation:</span> <span className="font-medium">{(result.decision as any).operation}</span></div>
              <div><span className="text-muted-foreground">Symbol:</span> {(result.decision as any).symbol}</div>
              {(result.decision as any).reason && (
                <div className="text-xs text-muted-foreground mt-2 italic">{(result.decision as any).reason}</div>
              )}
            </div>
          ) : result.error && (
            <div className="p-3 bg-muted rounded-md">
              <pre className="text-xs text-red-600 dark:text-red-400 whitespace-pre-wrap break-words overflow-x-auto max-h-[30vh]">
                {result.error}
              </pre>
            </div>
          )}

          {/* Execution Time */}
          <div className="text-xs text-muted-foreground">
            Execution time: {result.execution_time_ms.toFixed(0)}ms
          </div>
        </div>
      )}

      {/* Empty state */}
      {!result && !running && (
        <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
          Click "Run Preview" to test
        </div>
      )}
    </div>
  )
}

// Right Panel: Input Data, Data Queries, Logs, Decision Details
function RightPanel({ result, expandedSections, toggleSection }: any) {
  if (!result) {
    return (
      <div className="w-3/5 border-l pl-4 flex items-center justify-center text-muted-foreground text-sm">
        Run preview to see details
      </div>
    )
  }

  const inputData = result.input_data || {}

  return (
    <div className="w-3/5 border-l pl-4 overflow-y-auto space-y-2">
      {/* Input Data */}
      {result.input_data && (
        <Collapsible open={expandedSections['input']} onOpenChange={() => toggleSection('input')}>
          <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 hover:bg-muted rounded text-sm font-medium">
            {expandedSections['input'] ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            Input Data
          </CollapsibleTrigger>
          <CollapsibleContent className="pl-4 text-xs space-y-2 pb-2">
            {/* Basic Info */}
            <div className="space-y-1 p-2 bg-muted/50 rounded">
              <div className="font-medium text-muted-foreground mb-1">Basic Info</div>
              <div>Environment: <span className="font-mono">{inputData.environment}</span></div>
              <div>Trigger: <span className="font-mono">{inputData.trigger_symbol || '(scheduled)'}</span> ({inputData.trigger_type})</div>
              {inputData.signal_source_type && (
                <div>Source: <span className="font-mono">{inputData.signal_source_type}</span></div>
              )}
              <div>Balance: <span className="font-mono">${inputData.available_balance?.toFixed(2)}</span></div>
              <div>Equity: <span className="font-mono">${inputData.total_equity?.toFixed(2)}</span></div>
              <div>Margin Used: <span className="font-mono">{inputData.margin_usage_percent?.toFixed(1)}%</span></div>
              <div>Max Leverage: <span className="font-mono">{inputData.max_leverage}</span></div>
              <div>Default Leverage: <span className="font-mono">{inputData.default_leverage}</span></div>
            </div>

            {/* Signal Context */}
            {inputData.trigger_type === 'signal' && (
              <Collapsible>
                <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 hover:bg-muted rounded text-xs font-medium">
                  <ChevronRight className="h-3 w-3" />
                  Signal Context ({inputData.signal_pool_name || 'N/A'})
                </CollapsibleTrigger>
                <CollapsibleContent className="pl-4 text-xs">
                  <pre className="bg-muted p-2 rounded overflow-x-auto whitespace-pre-wrap">
{JSON.stringify({
  signal_pool_name: inputData.signal_pool_name,
  pool_logic: inputData.pool_logic,
  triggered_signals: inputData.triggered_signals,
  signal_source_type: inputData.signal_source_type,
  wallet_event: inputData.wallet_event,
}, null, 2)}
                  </pre>
                </CollapsibleContent>
              </Collapsible>
            )}

            {/* Trigger Market Regime */}
            {inputData.trigger_market_regime && (
              <Collapsible>
                <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 hover:bg-muted rounded text-xs font-medium">
                  <ChevronRight className="h-3 w-3" />
                  Trigger Market Regime ({inputData.trigger_market_regime.regime})
                </CollapsibleTrigger>
                <CollapsibleContent className="pl-4 text-xs">
                  <pre className="bg-muted p-2 rounded overflow-x-auto whitespace-pre-wrap">
{JSON.stringify(inputData.trigger_market_regime, null, 2)}
                  </pre>
                </CollapsibleContent>
              </Collapsible>
            )}

            {/* Positions */}
            <Collapsible>
              <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 hover:bg-muted rounded text-xs font-medium">
                <ChevronRight className="h-3 w-3" />
                Positions ({inputData.positions_count || Object.keys(inputData.positions || {}).length})
              </CollapsibleTrigger>
              <CollapsibleContent className="pl-4 text-xs">
                <pre className="bg-muted p-2 rounded overflow-x-auto whitespace-pre-wrap">
{JSON.stringify(inputData.positions, null, 2)}
                </pre>
              </CollapsibleContent>
            </Collapsible>

            {/* Open Orders */}
            <Collapsible>
              <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 hover:bg-muted rounded text-xs font-medium">
                <ChevronRight className="h-3 w-3" />
                Open Orders ({inputData.open_orders_count ?? (inputData.open_orders?.length || 0)})
              </CollapsibleTrigger>
              <CollapsibleContent className="pl-4 text-xs">
                <pre className="bg-muted p-2 rounded overflow-x-auto whitespace-pre-wrap">
{JSON.stringify(inputData.open_orders, null, 2)}
                </pre>
              </CollapsibleContent>
            </Collapsible>
          </CollapsibleContent>
        </Collapsible>
      )}

      {/* Data Queries */}
      {result.data_queries && result.data_queries.length > 0 && (
        <Collapsible open={expandedSections['queries']} onOpenChange={() => toggleSection('queries')}>
          <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 hover:bg-muted rounded text-sm font-medium">
            {expandedSections['queries'] ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            Data Queries ({result.data_queries.length})
          </CollapsibleTrigger>
          <CollapsibleContent className="pl-6 text-xs space-y-2 max-h-48 overflow-y-auto pb-2">
            {result.data_queries.map((q: any, i: number) => (
              <div key={i} className="p-2 bg-muted rounded">
                <div className="font-mono text-primary">{q.method}({JSON.stringify(q.args)})</div>
                <div className="text-muted-foreground mt-1 truncate">
                  → {JSON.stringify(q.result).slice(0, 100)}...
                </div>
              </div>
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}

      {/* Execution Logs */}
      {result.execution_logs && result.execution_logs.length > 0 && (
        <Collapsible open={expandedSections['logs']} onOpenChange={() => toggleSection('logs')}>
          <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 hover:bg-muted rounded text-sm font-medium">
            {expandedSections['logs'] ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            Execution Logs ({result.execution_logs.length})
          </CollapsibleTrigger>
          <CollapsibleContent className="pl-6 text-xs font-mono bg-muted p-2 rounded max-h-32 overflow-y-auto">
            {result.execution_logs.map((log: string, i: number) => (
              <div key={i}>{log}</div>
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}

      {/* Decision Details */}
      {result.decision && (
        <Collapsible open={expandedSections['decision']} onOpenChange={() => toggleSection('decision')}>
          <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 hover:bg-muted rounded text-sm font-medium">
            {expandedSections['decision'] ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            Decision Details
          </CollapsibleTrigger>
          <CollapsibleContent className="pl-6 text-xs pb-2">
            <pre className="bg-muted p-2 rounded overflow-x-auto">
              {JSON.stringify(result.decision, null, 2)}
            </pre>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  )
}
