import { ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { cn } from '../lib/cn'
import {
  DEXSCREENER_CHART_INTERVALS,
  dexscreenerEmbedUrl,
  dexscreenerPageUrl,
  loadChartInterval,
  saveChartInterval,
  type DexChartInterval,
} from '../lib/dexscreener'

interface EmbeddedChartPanelProps {
  chainId: string
  pairAddress: string
  pageUrl?: string
  pairLabel?: string
}

export function EmbeddedChartPanel({
  chainId,
  pairAddress,
  pageUrl,
  pairLabel,
}: EmbeddedChartPanelProps) {
  const [chartInterval, setChartInterval] = useState<DexChartInterval>(loadChartInterval)

  const embedUrl = dexscreenerEmbedUrl(chainId, pairAddress, pageUrl, chartInterval)
  const fullUrl = dexscreenerPageUrl(chainId, pairAddress, pageUrl)

  const selectInterval = (interval: DexChartInterval) => {
    setChartInterval(interval)
    saveChartInterval(interval)
  }

  return (
    <div className="pixel-panel overflow-hidden">
      <div className="px-4 py-3 border-b-2 border-border-bright flex items-center justify-between gap-3 bg-panel-elevated">
        <div>
          <h3 className="pixel-label">Live chart</h3>
          {pairLabel && (
            <p className="text-sm text-terminal/50 font-terminal mt-0.5">
              Powered by DexScreener · {pairLabel}
            </p>
          )}
        </div>
        <a
          href={fullUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="pixel-btn px-3 py-1.5 text-sm font-terminal flex items-center gap-1.5 shrink-0"
        >
          Open full chart
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
      </div>

      <div className="px-3 py-2 border-b-2 border-border bg-panel flex flex-wrap gap-1">
        {DEXSCREENER_CHART_INTERVALS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => selectInterval(id)}
            className={cn(
              'px-2.5 py-1 text-sm font-terminal transition-colors',
              chartInterval === id
                ? 'pixel-btn pixel-btn-active'
                : 'pixel-btn text-terminal/50 hover:text-terminal',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      <iframe
        key={`${chainId}-${pairAddress}-${chartInterval}`}
        src={embedUrl}
        title={pairLabel ? `DexScreener chart for ${pairLabel}` : 'DexScreener chart'}
        className="w-full h-[420px] border-0 bg-panel"
        loading="lazy"
        allow="clipboard-write"
      />
      <p className="px-4 py-2 text-xs text-terminal/40 font-terminal border-t-2 border-border">
        Aria Market reads the market — DexScreener renders the chart. Our signals sit above.
      </p>
    </div>
  )
}