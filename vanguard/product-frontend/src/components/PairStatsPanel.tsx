import { useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { PeriodPicker } from './PeriodPicker'
import {
  formatCompactUsd,
  formatPairAge,
  formatPercent,
  formatPrice,
  formatTxns,
  percentColor,
} from '../lib/chains'
import {
  loadSelectedPeriods,
  priceChangeForPeriod,
  saveSelectedPeriods,
  volumeForPeriod,
  type StatsPeriod,
} from '../lib/periods'
import type { PairSummary } from '../types'

interface PairStatsPanelProps {
  pair: PairSummary
}

function StatCell({ label, value, className = 'text-terminal' }: { label: string; value: string; className?: string }) {
  return (
    <div className="pixel-panel-inset px-3 py-2">
      <div className="pixel-label text-[8px]">{label}</div>
      <div className={`text-sm font-terminal font-medium mt-0.5 ${className}`}>{value}</div>
    </div>
  )
}

export function PairStatsPanel({ pair }: PairStatsPanelProps) {
  const [selectedPeriods, setSelectedPeriods] = useState<StatsPeriod[]>(loadSelectedPeriods)
  const txns = pair.txns

  const handlePeriodsChange = (periods: StatsPeriod[]) => {
    saveSelectedPeriods(periods)
    setSelectedPeriods(periods)
  }

  return (
    <div className="pixel-panel p-4 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="pixel-label">Pair stats</h3>
        {pair.url && (
          <a
            href={pair.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-sm text-accent hover:text-terminal font-terminal"
          >
            DexScreener <ExternalLink className="w-3 h-3" />
          </a>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatCell label="Price" value={formatPrice(pair.price_usd)} />
        <StatCell label="Age" value={formatPairAge(pair.pair_created_at)} />
        <StatCell label="Liquidity" value={formatCompactUsd(pair.liquidity_usd)} />
        <StatCell label="Volume 24h" value={formatCompactUsd(pair.volume_h24)} />
        <StatCell label="MCAP" value={formatCompactUsd(pair.market_cap)} />
        <StatCell label="FDV" value={formatCompactUsd(pair.fdv)} />
        <StatCell
          label="24h change"
          value={formatPercent(pair.price_change_h24)}
          className={percentColor(pair.price_change_h24)}
        />
        {pair.boosts_active != null && (
          <StatCell label="Boosts" value={String(pair.boosts_active)} />
        )}
      </div>

      {txns && (
        <div className="space-y-3">
          <PeriodPicker selected={selectedPeriods} onChange={handlePeriodsChange} />
          <div
            className="grid gap-2"
            style={{
              gridTemplateColumns: `repeat(${Math.min(selectedPeriods.length, 4)}, minmax(0, 1fr))`,
            }}
          >
            {selectedPeriods.map((period) => {
              const block = txns[period]
              const change = priceChangeForPeriod(pair, period)
              const volume = volumeForPeriod(pair, period)
              return (
                <div key={period} className="pixel-panel-inset p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-terminal font-semibold text-terminal uppercase">
                      {period}
                    </span>
                    <span className={`text-sm font-terminal font-medium ${percentColor(change)}`}>
                      {formatPercent(change)}
                    </span>
                  </div>
                  <div className="text-sm font-terminal text-terminal/70">
                    <span className="text-terminal/45">Txns </span>
                    {block ? formatTxns(block.buys, block.sells) : '—'}
                  </div>
                  <div className="text-xs font-terminal text-terminal/45">
                    Vol {formatCompactUsd(volume)}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {(pair.websites?.length || pair.socials?.length) ? (
        <div className="flex flex-wrap gap-2">
          {pair.websites?.map((url) => (
            <a
              key={url}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm px-2 py-1 pixel-btn font-terminal"
            >
              Website
            </a>
          ))}
          {pair.socials?.map((s, i) => (
            <a
              key={`${s.platform}-${i}`}
              href={s.url || '#'}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm px-2 py-1 pixel-btn font-terminal capitalize"
            >
              {s.platform}
            </a>
          ))}
        </div>
      ) : null}
    </div>
  )
}