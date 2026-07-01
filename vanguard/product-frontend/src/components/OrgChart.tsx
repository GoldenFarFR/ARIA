import { ChevronRight, Layers, Zap } from 'lucide-react'
import { BrandMark } from './BrandMark'
import type { RepertoireItem } from '../types'

const STATUS_DOT: Record<string, string> = {
  live: 'bg-buy',
  building: 'bg-accent',
  idea: 'bg-border-bright',
  paused: 'bg-watch',
  archived: 'bg-border',
}

interface OrgChartProps {
  holdingName: string
  holdingStatus?: string
  portfolio: RepertoireItem[]
  subsidiaryLabel?: string
}

function PortfolioNode({ item, holdingName }: { item: RepertoireItem; holdingName: string }) {
  const isFlagship = item.slug === 'market'
  const dot = STATUS_DOT[item.status] ?? STATUS_DOT.idea

  return (
    <div className="flex items-start gap-2 ml-6 pl-4 border-l-2 border-violet/20">
      <div className="w-5 h-5 bg-panel border-2 border-border flex items-center justify-center shrink-0 -ml-[2.35rem] mt-0.5">
        {isFlagship ? (
          <Zap className="w-3 h-3 text-accent" />
        ) : (
          <Layers className="w-3 h-3 text-violet" />
        )}
      </div>
      <div className="flex-1 min-w-0 pb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-terminal font-medium text-terminal">{item.name}</span>
          <span className={`w-1.5 h-1.5 ${dot}`} />
          <span className="text-sm text-terminal/50 font-terminal">{item.status}</span>
          {isFlagship && (
            <span className="pixel-label text-[8px]">flagship</span>
          )}
        </div>
        <p className="text-sm text-violet/70 mt-0.5 font-terminal">
          Subsidiary of {holdingName}
        </p>
      </div>
    </div>
  )
}

export function OrgChart({ holdingName, holdingStatus = 'live', portfolio, subsidiaryLabel: label }: OrgChartProps) {
  const childLabel = label ?? `Subsidiary of ${holdingName}`

  return (
    <section className="pixel-panel border-violet/20 p-5">
      <p className="pixel-label mb-4">
        Corporate structure
      </p>

      <div className="space-y-1">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-violet/20 border-2 border-violet/40 flex items-center justify-center">
            <BrandMark size={22} />
          </div>
          <div>
            <p className="text-sm font-terminal font-semibold text-terminal">{holdingName}</p>
            <p className="text-sm text-violet font-terminal">Parent holding · {holdingStatus}</p>
          </div>
        </div>

        {portfolio.length > 0 ? (
          <div className="mt-2">
            {portfolio.map((item) => (
              <div key={item.id}>
                <PortfolioNode item={item} holdingName={holdingName} />
              </div>
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-2 ml-6 mt-3 text-sm text-terminal/50 font-terminal">
            <ChevronRight className="w-3 h-3" />
            No subsidiaries registered yet
          </div>
        )}

        <div className="flex items-center gap-2 ml-6 mt-1 pl-4 border-l-2 border-dashed border-violet/15">
          <div className="w-5 h-5 border-2 border-dashed border-violet/30 flex items-center justify-center shrink-0 -ml-[2.35rem]">
            <span className="text-violet text-sm font-terminal">+</span>
          </div>
          <p className="text-sm text-terminal/50 pb-1 font-terminal">
            Future ventures attach here — always under {holdingName}
          </p>
        </div>
      </div>

      <p className="text-sm text-terminal/40 mt-4 pt-4 border-t-2 border-border leading-relaxed font-terminal">
        {childLabel}. Aria Market is the first live subsidiary; every new project follows the same
        structure.
      </p>
    </section>
  )
}