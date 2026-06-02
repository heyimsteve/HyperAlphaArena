import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'

type Props = {
  symbol: string
  size?: number
  className?: string
}

const PREFIX_RE = /^(k|K)(?=[A-Z])/

function stripPrefix(symbol: string): string {
  return symbol.replace(PREFIX_RE, '')
}

function fallbackHue(symbol: string): number {
  let hash = 0
  for (let i = 0; i < symbol.length; i += 1) {
    hash = symbol.charCodeAt(i) + ((hash << 5) - hash)
  }
  return Math.abs(hash) % 360
}

function getSources(symbol: string): string[] {
  const clean = stripPrefix(symbol)
  const upper = clean.toUpperCase()
  const lower = clean.toLowerCase()
  return [
    `https://app.hyperliquid.xyz/coins/${upper}.svg`,
    `https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/svg/color/${lower}.svg`,
  ]
}

export function CoinIcon({ symbol, size = 20, className }: Props) {
  const [sourceIdx, setSourceIdx] = useState(0)
  const sources = useMemo(() => getSources(symbol), [symbol])
  const hue = useMemo(() => fallbackHue(symbol), [symbol])
  const letter = stripPrefix(symbol).charAt(0) || symbol.charAt(0) || '?'

  if (sourceIdx >= sources.length) {
    return (
      <span
        className={cn(
          'inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white',
          className,
        )}
        style={{
          width: size,
          height: size,
          background: `linear-gradient(135deg, hsl(${hue} 55% 45%), hsl(${(hue + 40) % 360} 50% 30%))`,
          fontSize: Math.round(size * 0.5),
        }}
        aria-label={symbol}
      >
        {letter.toUpperCase()}
      </span>
    )
  }

  return (
    <img
      className={cn('shrink-0 rounded-full', className)}
      src={sources[sourceIdx]}
      alt={symbol}
      width={size}
      height={size}
      loading="lazy"
      onError={() => setSourceIdx((idx) => idx + 1)}
    />
  )
}
