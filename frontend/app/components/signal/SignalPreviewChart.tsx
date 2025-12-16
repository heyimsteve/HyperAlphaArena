import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, CandlestickData, Time, IChartApi, ISeriesApi, createSeriesMarkers } from 'lightweight-charts'
import { formatChartTime } from '../../lib/dateTime'

interface KlineData {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
}

// Full trigger data from backend
interface TriggerData {
  timestamp: number
  value?: number
  threshold?: number
  metric?: string
  triggered_signals?: Array<{
    signal_id: number
    signal_name: string
    value: number
    threshold: number
  }>
  trigger_type?: string
}

interface SignalPreviewChartProps {
  klines: KlineData[]
  triggers: TriggerData[]
  timeWindow: string
  signalMetric?: string // For single signal display
}

// Format metric name for display
function formatMetricName(metric: string): string {
  const names: Record<string, string> = {
    cvd_change: 'CVD Change',
    oi_delta: 'OI Delta',
    oi_delta_percent: 'OI Delta %',
    buy_sell_imbalance: 'Buy/Sell Imbalance',
    depth_ratio: 'Depth Ratio',
    taker_buy_ratio: 'Taker Buy Ratio',
    taker_direction: 'Taker Direction',
  }
  return names[metric] || metric
}

// Format value based on metric type
function formatValue(metric: string, value: number): string {
  if (metric.includes('ratio') || metric.includes('imbalance')) {
    return value.toFixed(3)
  }
  if (metric.includes('percent') || metric === 'cvd_change' || metric === 'oi_delta') {
    return `${value.toFixed(2)}%`
  }
  return value.toFixed(4)
}

export default function SignalPreviewChart({ klines, triggers, timeWindow, signalMetric }: SignalPreviewChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const [tooltip, setTooltip] = useState<{ visible: boolean; x: number; y: number; content: TriggerData | TriggerData[] | null }>({
    visible: false, x: 0, y: 0, content: null
  })

  // Build time-to-trigger map for quick lookup (may have multiple triggers per bucket)
  const triggerMap = useRef<Map<number, TriggerData | TriggerData[]>>(new Map())

  // Get bucket size in seconds from timeWindow
  const getBucketSize = (tw: string): number => {
    const match = tw.match(/^(\d+)([mhd])$/)
    if (!match) return 300 // default 5min
    const [, num, unit] = match
    const n = parseInt(num)
    if (unit === 'm') return n * 60
    if (unit === 'h') return n * 3600
    if (unit === 'd') return n * 86400
    return 300
  }

  // Calculate bucket time for a trigger (used by both marker and triggerMap)
  const getTriggerBucketTime = (timestamp: number, bucketSize: number): number => {
    const triggerSec = Math.floor(timestamp / 1000)
    const bucketSec = Math.floor(triggerSec / bucketSize) * bucketSize
    return formatChartTime(bucketSec)
  }

  useEffect(() => {
    // Build trigger map using floored bucket time as key (to match K-line time)
    triggerMap.current.clear()
    const bucketSize = getBucketSize(timeWindow)

    triggers.forEach(t => {
      const chartTime = getTriggerBucketTime(t.timestamp, bucketSize)

      // Store all triggers for this bucket (may have multiple)
      const existing = triggerMap.current.get(chartTime)
      if (existing) {
        // Merge triggered_signals if both have them
        if (Array.isArray(existing)) {
          existing.push(t)
        } else {
          triggerMap.current.set(chartTime, [existing, t])
        }
      } else {
        triggerMap.current.set(chartTime, t)
      }
    })
  }, [triggers, timeWindow])

  useEffect(() => {
    if (!chartContainerRef.current || klines.length === 0) return

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: 500,
      layout: {
        background: { color: '#1a1a2e' },
        textColor: '#d1d5db',
      },
      grid: {
        vertLines: { color: '#2d2d44' },
        horzLines: { color: '#2d2d44' },
      },
      crosshair: {
        mode: 1,
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: '#2d2d44',
        barSpacing: 9,
        rightBarStaysOnScroll: false,
      },
      rightPriceScale: {
        borderColor: '#2d2d44',
      },
    })

    chartRef.current = chart

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })

    seriesRef.current = candlestickSeries

    // Convert klines to chart format
    const chartData: CandlestickData<Time>[] = klines.map(k => ({
      time: formatChartTime(k.timestamp / 1000) as Time,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))

    candlestickSeries.setData(chartData)

    // Add trigger markers - use same bucket time as triggerMap
    if (triggers.length > 0) {
      const bucketSize = getBucketSize(timeWindow)
      const markers = triggers.map(t => ({
        time: getTriggerBucketTime(t.timestamp, bucketSize) as Time,
        position: 'aboveBar' as const,
        color: '#F8CD74',
        shape: 'arrowDown' as const,
        text: '⚡',
        size: 2,
      }))
      createSeriesMarkers(candlestickSeries, markers)
    }

    // Subscribe to crosshair move for tooltip
    chart.subscribeCrosshairMove(param => {
      if (!param.time || !param.point) {
        setTooltip(prev => ({ ...prev, visible: false }))
        return
      }

      // param.time is the local chart time (same format as triggerMap keys)
      const chartTime = param.time as number

      // Direct lookup - triggerMap uses same time format as chart
      const matchedTrigger = triggerMap.current.get(chartTime) || null

      if (matchedTrigger) {
        setTooltip({
          visible: true,
          x: param.point.x,
          y: param.point.y,
          content: matchedTrigger,
        })
      } else {
        setTooltip(prev => ({ ...prev, visible: false }))
      }
    })

    chart.timeScale().scrollToRealTime()

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [klines, triggers])

  // Render a single trigger's content
  const renderSingleTrigger = (t: TriggerData, idx?: number) => {
    // Pool trigger with multiple signals (AND logic)
    if (t.triggered_signals && t.triggered_signals.length > 0) {
      return (
        <div key={idx} className="space-y-1">
          {t.triggered_signals.map((sig, i) => (
            <div key={i} className="text-xs">
              <span className="text-gray-400">{sig.signal_name || 'Signal'}:</span>{' '}
              <span className="text-white font-mono">{sig.value?.toFixed(4) ?? 'N/A'}</span>
              <span className="text-gray-500 ml-1">(≥{sig.threshold?.toFixed(4) ?? 'N/A'})</span>
            </div>
          ))}
        </div>
      )
    }

    // Single signal trigger
    if (t.value !== undefined) {
      const metric = t.metric || signalMetric || 'value'
      return (
        <div key={idx} className="text-xs">
          <span className="text-gray-400">{formatMetricName(metric)}:</span>{' '}
          <span className="text-white font-mono">{formatValue(metric, t.value)}</span>
          {t.threshold !== undefined && (
            <span className="text-gray-500 ml-1">(≥{formatValue(metric, t.threshold)})</span>
          )}
        </div>
      )
    }
    return null
  }

  // Render tooltip content (may have multiple triggers in same bucket)
  const renderTooltipContent = () => {
    if (!tooltip.content) return null

    const content = tooltip.content
    const triggers = Array.isArray(content) ? content : [content]

    return (
      <div className="space-y-2">
        <div className="text-xs text-yellow-400 font-medium border-b border-gray-600 pb-1">
          Trigger Values {triggers.length > 1 && `(${triggers.length})`}
        </div>
        {triggers.map((t, idx) => renderSingleTrigger(t, idx))}
      </div>
    )
  }

  return (
    <div className="relative w-full h-[500px]">
      <div ref={chartContainerRef} className="w-full h-full" />
      {tooltip.visible && tooltip.content && (
        <div
          className="absolute z-50 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 shadow-lg pointer-events-none"
          style={{
            left: Math.min(tooltip.x + 15, (chartContainerRef.current?.clientWidth || 400) - 250),
            top: Math.max(tooltip.y - 60, 10),
          }}
        >
          {renderTooltipContent()}
        </div>
      )}
    </div>
  )
}
