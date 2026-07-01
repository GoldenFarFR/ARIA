import {
  Bot,
  ExternalLink,
  Layers,
  Sparkles,
  Zap,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { getAgentSetup, getHoldingStructure } from '../api'
import { BrandMark } from './BrandMark'
import { OrgChart } from './OrgChart'
import type { AgentSetup, HoldingStructure, RepertoireItem } from '../types'

const STATUS_COLORS: Record<string, string> = {
  idea: 'text-terminal/50 bg-panel-elevated border-border',
  building: 'text-accent bg-accent/10 border-accent/30',
  live: 'text-buy bg-buy/10 border-buy/30',
  paused: 'text-watch bg-watch/10 border-watch/30',
  archived: 'text-terminal/40 bg-panel border-border',
}

const HOLDING_TAGLINE =
  'Zero-Human Company holding — parent entity for autonomous ventures'

function SubsidiaryCard({
  item,
  featured,
  holdingName,
}: {
  item: RepertoireItem
  featured?: boolean
  holdingName: string
}) {
  return (
    <div
      className={`pixel-panel p-5 transition-colors ${
        featured ? 'border-accent/30 bg-accent/5' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          {featured ? (
            <div className="w-8 h-8 bg-accent/20 border-2 border-accent flex items-center justify-center">
              <Zap className="w-4 h-4 text-accent" />
            </div>
          ) : (
            <Layers className="w-4 h-4 text-violet mt-1" />
          )}
          <div>
            <h4 className="text-sm font-terminal font-semibold text-terminal">{item.name}</h4>
            <p className="text-sm text-violet/80 font-terminal">Subsidiary of {holdingName}</p>
            {featured && (
              <span className="text-sm text-accent/80 font-terminal">Flagship product</span>
            )}
          </div>
        </div>
        <span
          className={`text-sm px-2 py-0.5 border-2 shrink-0 font-terminal ${
            STATUS_COLORS[item.status] || STATUS_COLORS.idea
          }`}
        >
          {item.status}
        </span>
      </div>
      {item.description && (
        <p className="text-sm text-terminal/60 leading-relaxed font-terminal">{item.description}</p>
      )}
      <div className="flex flex-wrap gap-2 mt-3 text-sm text-terminal/50 font-terminal">
        <span className="px-2 py-0.5 pixel-panel-inset">
          {item.category}
        </span>
        {item.zhc_aligned && (
          <span className="px-2 py-0.5 bg-violet/10 text-violet border-2 border-violet/20">
            ZHC aligned
          </span>
        )}
        {item.tags?.map((tag) => (
          <span
            key={tag}
            className="px-2 py-0.5 pixel-panel-inset text-terminal/40"
          >
            {tag}
          </span>
        ))}
      </div>
    </div>
  )
}

export function CorporatePanel() {
  const [structure, setStructure] = useState<HoldingStructure | null>(null)
  const [setup, setSetup] = useState<AgentSetup | null>(null)

  useEffect(() => {
    getHoldingStructure().then(setStructure).catch(console.error)
    getAgentSetup().then(setSetup).catch(console.error)
  }, [])

  const holding = structure?.holding
  const subsidiaries = structure?.subsidiaries ?? []
  const ventures = structure?.ventures ?? []
  const portfolio = [...subsidiaries, ...ventures]
  const holdingName = holding?.name ?? setup?.holding ?? 'Aria Vanguard ZHC'
  const ariaTitle = structure?.aria_title ?? setup?.aria_title ?? 'Chief Autonomous Officer (CAO)'

  const xUrl = setup?.x_handle
    ? `https://x.com/${setup.x_handle.replace('@', '')}`
    : 'https://x.com/Aria_ZHC'
  const governanceRule =
    structure?.governance_rule ??
    setup?.governance_rule ??
    'Every ARIA venture is a subsidiary of Aria Vanguard ZHC. Aria Market is the flagship — not the holding.'
  const narrativeLine = setup?.one_liner ?? governanceRule

  return (
    <div className="space-y-6">
      <div className="pixel-panel border-violet/25 bg-violet/5 px-4 py-3 space-y-2">
        <p className="text-sm text-violet/90 leading-relaxed font-terminal font-medium">{narrativeLine}</p>
        <p className="text-sm text-terminal/50 leading-relaxed font-terminal">{governanceRule}</p>
      </div>

      <OrgChart
        holdingName={holdingName}
        holdingStatus={holding?.status}
        portfolio={portfolio}
        subsidiaryLabel={structure?.subsidiary_label}
      />

      <section className="pixel-panel overflow-hidden">
        <div className="px-6 py-8 md:px-8 md:py-10">
          <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-6">
            <div className="space-y-4 max-w-2xl">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 border-2 border-violet/40 bg-violet/10 flex items-center justify-center">
                  <BrandMark size={28} />
                </div>
                <div>
                  <p className="pixel-label">
                    ZHC Holding
                  </p>
                  <h2 className="text-2xl md:text-3xl font-display text-terminal tracking-tight">
                    {holdingName}
                  </h2>
                </div>
              </div>
              <p className="text-terminal/60 leading-relaxed font-terminal">
                {holding?.description ?? setup?.holding_structure ?? HOLDING_TAGLINE}
              </p>
              <div className="flex flex-wrap gap-2">
                <span className="text-sm px-3 py-1 bg-violet/15 text-violet border-2 border-violet/25 font-terminal">
                  Zero-Human Company
                </span>
                <span className="text-sm px-3 py-1 bg-buy/10 text-buy border-2 border-buy/20 font-terminal">
                  {holding?.status ?? 'live'}
                </span>
              </div>
            </div>

            <div className="flex flex-col gap-2 shrink-0">
              <a
                href={xUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-terminal pixel-btn"
              >
                <ExternalLink className="w-4 h-4" />
                {setup?.x_handle ?? '@Aria_ZHC'}
              </a>
            </div>
          </div>
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <section className="lg:col-span-2 space-y-4">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-accent" />
            <h3 className="pixel-label">
              Portfolio
            </h3>
          </div>

          {!structure ? (
            <p className="text-sm text-terminal/50 py-8 text-center font-terminal">Loading portfolio…</p>
          ) : portfolio.length === 0 ? (
            <div className="pixel-panel border-dashed border-border p-8 text-center">
              <p className="text-sm text-terminal/50 font-terminal">
                No subsidiaries registered yet. Use the ARIA tab to add ventures under the holding.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {portfolio.map((item, i) => (
                <SubsidiaryCard
                  key={item.id}
                  item={item}
                  holdingName={holdingName}
                  featured={item.slug === 'market' || i === 0}
                />
              ))}
            </div>
          )}
        </section>

        <aside className="space-y-4">
          <section className="pixel-panel p-5">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 bg-violet/20 border-2 border-violet/40 flex items-center justify-center">
                <Bot className="w-5 h-5 text-violet" />
              </div>
              <div>
                <h3 className="text-sm font-terminal font-semibold text-terminal">
                  {setup?.identity ?? 'ARIA ZHC'}
                </h3>
                <p className="text-sm text-violet font-terminal">{ariaTitle}</p>
              </div>
            </div>
            {setup?.bio_suggestion && (
              <p className="text-sm text-terminal/50 leading-relaxed mb-4 font-terminal">
                {setup.bio_suggestion}
              </p>
            )}
            <p className="text-sm text-terminal/40 font-terminal">
              ARIA operates the holding and its subsidiaries autonomously. Human principal steers
              strategy; ARIA executes.
            </p>
          </section>

          <section className="pixel-panel p-5">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="w-4 h-4 text-violet" />
              <h3 className="pixel-label">Model</h3>
            </div>
            <p className="text-sm text-terminal/50 leading-relaxed font-terminal">
              Inspired by the ZHC playbook — autonomous AI operators building portfolio ventures
              with minimal human overhead. Aria Market is the first live subsidiary; new projects
              register under {holdingName}.
            </p>
          </section>
        </aside>
      </div>
    </div>
  )
}