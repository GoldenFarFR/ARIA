import { ChevronRight, Layers, Zap } from 'lucide-react'
import { BrandMark } from './BrandMark'
import type { RepertoireItem } from '../types'

const STATUS_DOT: Record<string, string> = {
  live: 'bg-[#c9a962]',
  building: 'bg-[#8a7344]',
  idea: 'bg-[#4a4840]',
  paused: 'bg-[#d4a574]',
  archived: 'bg-[#3a3832]',
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
    <div className="flex items-start gap-2 ml-6 pl-4 border-l border-[#c9a962]/20">
      <div className="luxury-icon-box w-5 h-5 rounded-sm shrink-0 -ml-[2.35rem] mt-0.5">
        {isFlagship ? (
          <Zap className="w-3 h-3 text-[#c9a962]" />
        ) : (
          <Layers className="w-3 h-3 text-[#8a7344]" />
        )}
      </div>
      <div className="flex-1 min-w-0 pb-4">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-display text-sm font-medium text-[#e8d5a8] tracking-wide">{item.name}</span>
          <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
          <span className="text-xs text-[#6b665c] uppercase tracking-wider">{item.status}</span>
          {isFlagship && (
            <span className="luxury-badge text-[9px] py-0.5">flagship</span>
          )}
        </div>
        <p className="text-xs text-[#8a7344] mt-1 tracking-wide">Subsidiary of {holdingName}</p>
      </div>
    </div>
  )
}

export function OrgChart({ holdingName, holdingStatus = 'live', portfolio, subsidiaryLabel: label }: OrgChartProps) {
  const childLabel = label ?? `Subsidiary of ${holdingName}`

  return (
    <section className="glass-vanguard rounded-sm p-8 md:p-10">
      <p className="section-label mb-6">Corporate structure</p>

      <div className="space-y-1">
        <div className="flex items-center gap-4">
          <div className="luxury-icon-box w-11 h-11 rounded-sm">
            <BrandMark size={26} />
          </div>
          <div>
            <p className="font-display font-semibold text-[#f4efe6] tracking-wide">{holdingName}</p>
            <p className="text-xs text-[#8a7344] tracking-widest uppercase mt-1">Parent holding · {holdingStatus}</p>
          </div>
        </div>

        {portfolio.length > 0 ? (
          <div className="mt-4">
            {portfolio.map((item) => (
              <div key={item.id}>
                <PortfolioNode item={item} holdingName={holdingName} />
              </div>
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-2 ml-6 mt-4 text-xs text-[#6b665c]">
            <ChevronRight className="w-3 h-3 text-[#c9a962]" />
            No subsidiaries registered yet
          </div>
        )}

        <div className="flex items-center gap-2 ml-6 mt-2 pl-4 border-l border-dashed border-[#c9a962]/15">
          <div className="luxury-icon-box w-5 h-5 rounded-sm border-dashed shrink-0 -ml-[2.35rem]">
            <span className="text-[#c9a962] text-xs">+</span>
          </div>
          <p className="text-xs text-[#6b665c] pb-1 font-light">
            Future ventures attach here — always under {holdingName}
          </p>
        </div>
      </div>

      <p className="text-xs text-[#5a564c] mt-6 pt-6 border-t border-[#c9a962]/10 leading-relaxed font-light">
        {childLabel}. Aria Market is the first live subsidiary; every new project follows the same
        structure.
      </p>
    </section>
  )
}