import {
  ArrowRight,
  Bot,
  ChevronDown,
  ExternalLink,
  Layers,
  Sparkles,
  Zap,
} from 'lucide-react'
import { BrandMark } from '../components/BrandMark'
import { useEffect, useState } from 'react'
import { getHoldingStructure, getSiteContent } from '../api'
import { HOLDING_DOMAIN, HOLDING_SITE_URL } from '../lib/site'
import { ProductLaunchLink } from '../components/ProductLaunchLink'
import { AriaChat } from '../components/AriaChat'
import { FaqSection } from '../components/FaqSection'
import { OrgChart } from '../components/OrgChart'
import { PricingSection } from '../components/PricingSection'
import { VanguardNav } from '../components/VanguardNav'
import type { AgentSetup, HoldingStructure, RepertoireItem } from '../types'

const HOLDING = 'Aria Vanguard ZHC'

function Orb({ className }: { className: string }) {
  return <div className={`absolute rounded-full luxury-orb animate-vanguard-float ${className}`} />
}

function PortfolioCard({
  item,
  featured,
}: {
  item: RepertoireItem
  featured?: boolean
}) {
  return (
    <article
      className={`glass-vanguard rounded-sm p-7 md:p-8 card-vanguard-hover relative overflow-hidden ${
        featured ? 'border-[#c9a962]/25' : ''
      }`}
    >
      {featured && (
        <div className="absolute top-0 right-0 w-40 h-40 bg-[#c9a962]/8 rounded-full luxury-orb -translate-y-1/2 translate-x-1/2" />
      )}
      <div className="relative">
        <div className="flex items-start justify-between gap-3 mb-5">
          <div className="flex items-center gap-4">
            <div className="luxury-icon-box w-12 h-12 rounded-sm shrink-0">
              {featured ? (
                <Zap className="w-5 h-5 text-[#c9a962]" />
              ) : (
                <Layers className="w-5 h-5 text-[#8a7344]" />
              )}
            </div>
            <div>
              <h3 className="font-display font-semibold text-xl text-[#f4efe6] tracking-wide">{item.name}</h3>
              <p className="text-xs text-[#8a7344] tracking-widest uppercase mt-1">Subsidiary of {HOLDING}</p>
            </div>
          </div>
          <span className="luxury-badge shrink-0">{item.status}</span>
        </div>
        <p className="text-sm text-[#9a958a] leading-relaxed mb-6 font-light">{item.description}</p>
        {featured && (
          <ProductLaunchLink className="btn-vanguard-secondary w-full flex items-center justify-center gap-2 py-3.5 text-sm tracking-wide group focus-ring">
            Launch product
            <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform text-[#c9a962]" />
          </ProductLaunchLink>
        )}
      </div>
    </article>
  )
}

export function VanguardSite() {
  const [structure, setStructure] = useState<HoldingStructure | null>(null)
  const [setup, setSetup] = useState<AgentSetup | null>(null)

  useEffect(() => {
    getHoldingStructure().then(setStructure).catch(console.error)
    getSiteContent().then(setSetup).catch(console.error)
  }, [])

  const portfolio = [...(structure?.subsidiaries ?? []), ...(structure?.ventures ?? [])]
  const oneLiner =
    setup?.one_liner ??
    `${HOLDING} is the ZHC parent holding. Aria Market is its flagship subsidiary.`

  return (
    <div className="min-h-screen vanguard-mesh text-gray-100 overflow-x-hidden">
      <VanguardNav />

      <section className="relative min-h-screen flex flex-col justify-center pt-16">
        <div className="absolute inset-0 vanguard-grid pointer-events-none" />
        <Orb className="w-[480px] h-[480px] bg-[#c9a962]/12 -top-40 -left-32" />
        <Orb className="w-[360px] h-[360px] bg-[#8a7344]/10 top-1/3 -right-48 animate-vanguard-float-delayed" />

        <div className="relative max-w-6xl mx-auto px-5 py-20 md:py-28">
          <div className="luxury-badge inline-flex items-center gap-2 mb-10">
            <Sparkles className="w-3.5 h-3.5 text-[#c9a962]" />
            Zero-Human Company · AI-operated holding
          </div>

          <h1 className="font-display font-extrabold text-4xl sm:text-5xl md:text-6xl lg:text-7xl tracking-tight leading-[1.02] mb-6">
            <span className="text-gradient-vanguard">Aria Vanguard</span>
            <br />
            <span className="text-[#f4efe6] font-medium">ZHC</span>
          </h1>

          <div className="luxury-rule w-24 mb-8" />

          <p className="text-lg md:text-xl text-[#b8b2a6] max-w-2xl leading-relaxed mb-4 font-light">{oneLiner}</p>
          <p className="text-sm text-[#6b665c] max-w-xl mb-12 tracking-wide">
            {setup?.governance_rule ??
              'Every venture is a subsidiary. Aria Market is the flagship — not the holding.'}
          </p>

          <div className="flex flex-col sm:flex-row gap-4 mb-16">
            <ProductLaunchLink className="btn-vanguard-glow px-10 py-4 font-display text-lg tracking-wide flex items-center justify-center gap-3 focus-ring">
              Open Aria Market
              <ArrowRight className="w-5 h-5" />
            </ProductLaunchLink>
            <a
              href="#portfolio"
              className="btn-vanguard-secondary px-10 py-4 font-display text-lg tracking-wide text-center focus-ring"
            >
              View portfolio
            </a>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 max-w-3xl">
            {[
              { label: 'Holding', value: 'Live' },
              { label: 'CAO', value: 'ARIA ZHC' },
              { label: 'Flagship', value: 'Aria Market' },
              { label: 'Ventures', value: structure ? String(portfolio.length) : '—' },
            ].map((stat) => (
              <div key={stat.label} className="glass-vanguard rounded-sm px-5 py-4 card-vanguard-hover">
                <p className="section-label mb-2">{stat.label}</p>
                <p className="font-display font-semibold text-xl text-[#e8d5a8] tracking-wide">
                  {!structure && stat.label === 'Ventures' ? (
                    <span className="inline-block h-5 w-8 rounded skeleton align-middle" />
                  ) : (
                    stat.value
                  )}
                </p>
              </div>
            ))}
          </div>
        </div>

        <a
          href="#structure"
          className="absolute bottom-8 left-1/2 -translate-x-1/2 text-gray-600 hover:text-gray-400 transition-colors animate-bounce"
          aria-label="Scroll down"
        >
          <ChevronDown className="w-6 h-6" />
        </a>
      </section>

      <PricingSection />

      <section id="structure" className="relative py-24 md:py-32">
        <div className="max-w-6xl mx-auto px-5">
          <div className="text-center mb-16">
            <p className="section-label mb-4">Corporate architecture</p>
            <h2 className="font-display font-semibold text-3xl md:text-5xl text-[#f4efe6] mb-5 tracking-wide">
              One holding. Many ventures.
            </h2>
            <p className="text-[#6b665c] max-w-lg mx-auto font-light">
              All current and future ARIA projects register under {HOLDING} — always.
            </p>
          </div>
          <OrgChart
            holdingName={structure?.holding.name ?? HOLDING}
            holdingStatus={structure?.holding.status ?? 'live'}
            portfolio={portfolio}
            subsidiaryLabel={structure?.subsidiary_label}
          />
        </div>
      </section>

      <section id="portfolio" className="relative py-24 md:py-32 border-t border-[#c9a962]/10">
        <div className="max-w-6xl mx-auto px-5">
          <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-6 mb-12">
            <div>
              <p className="section-label mb-4">Portfolio</p>
              <h2 className="font-display font-semibold text-3xl md:text-4xl text-[#f4efe6] tracking-wide">
                Subsidiaries under Vanguard
              </h2>
            </div>
            <p className="text-sm text-[#6b665c] max-w-md font-light">
              Aria Market is live today. Every new venture follows the same subsidiary model.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {portfolio.length > 0 ? (
              portfolio.map((item) => (
                <PortfolioCard
                  key={item.id}
                  item={item}
                  featured={item.slug === 'market'}
                />
              ))
            ) : (
              <PortfolioCard
                item={{
                  id: 'market-seed',
                  name: 'Aria Market',
                  description:
                    'Subsidiary of Aria Vanguard ZHC. Real-time DEX analyzer — flagship product.',
                  status: 'live',
                  category: 'product',
                  priority: 5,
                  zhc_aligned: true,
                  tags: ['dex', 'flagship'],
                  notes: '',
                  revenue_monthly: 0,
                  created_at: '',
                  updated_at: '',
                  slug: 'market',
                }}
                featured
              />
            )}
            <article className="glass-vanguard rounded-sm p-8 border border-dashed border-[#c9a962]/20 flex flex-col items-center justify-center text-center min-h-[240px] card-vanguard-hover">
              <div className="luxury-icon-box w-14 h-14 rounded-sm mb-5 border-dashed">
                <span className="text-[#c9a962] text-2xl font-display font-light">+</span>
              </div>
              <h3 className="font-display font-semibold text-[#e8d5a8] mb-2 tracking-wide">Next venture</h3>
              <p className="text-sm text-[#6b665c] font-light">Future projects attach here — under {HOLDING}</p>
            </article>
          </div>
        </div>
      </section>

      <section id="aria" className="relative py-24 md:py-28 border-t border-[#c9a962]/10">
        <div className="max-w-6xl mx-auto px-5">
          <div className="glass-vanguard relative overflow-hidden rounded-sm p-8 md:p-14">
            <div className="absolute -right-20 -top-20 w-72 h-72 bg-[#c9a962]/8 rounded-full luxury-orb pointer-events-none" />
            <div className="grid md:grid-cols-[1fr_auto] gap-10 md:gap-16 items-start relative">
              <div className="min-w-0">
                <p className="section-label mb-4">Leadership</p>
                <h2 className="font-display font-semibold text-3xl md:text-4xl text-[#f4efe6] mb-2 tracking-wide">
                  {setup?.identity ?? 'ARIA ZHC'}
                </h2>
                <p className="text-[#c9a962] font-medium mb-5 tracking-wide">
                  {structure?.aria_title ?? setup?.aria_title ?? 'Chief Autonomous Officer (CAO)'}
                </p>
                <p className="text-[#9a958a] leading-relaxed mb-4 max-w-xl font-light">
                  {setup?.aria_role ??
                    setup?.bio_suggestion ??
                    'Heart of the project — builds, markets, communicates, and maintains the public FAQ.'}
                </p>
                <p className="text-sm text-[#6b665c] mb-8 max-w-xl">
                  Open to everyone. Portfolio, products, content, and ZHC network — one autonomous intelligence.
                </p>
                <AriaChat />
              </div>
              <div className="flex flex-col items-center gap-5 mx-auto md:mx-0 md:items-end">
                <div className="luxury-icon-box w-32 h-32 md:w-40 md:h-40 rounded-sm shrink-0">
                  <Bot className="w-16 h-16 md:w-20 md:h-20 text-[#c9a962]" />
                </div>
                <a
                  href="https://x.com/Aria_ZHC"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn-vanguard-secondary flex items-center gap-2 px-5 py-2.5 text-sm focus-ring"
                >
                  {setup?.x_handle ?? '@Aria_ZHC'}
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="model" className="relative py-24 md:py-32 border-t border-[#c9a962]/10">
        <div className="max-w-6xl mx-auto px-5">
          <div className="text-center mb-14">
            <p className="section-label mb-4">Operating model</p>
            <h2 className="font-display font-semibold text-3xl md:text-4xl text-[#f4efe6] tracking-wide">
              Built on the ZHC playbook
            </h2>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
            {(setup?.pillars ?? [
              { title: 'Build', body: 'Engineering plans, sandbox experiments, product iteration.' },
              { title: 'Marketing', body: 'Holding narrative, positioning, milestone updates.' },
              { title: 'Communication', body: 'Site copy, social drafts, and ZHC network.' },
              { title: 'FAQ', body: 'Structured answers and public education.' },
            ]).map((pillar: { title: string; body: string }, i: number) => {
              const icons = [null, Bot, Sparkles, Zap] as const
              const Icon = icons[i] ?? Bot
              const title = pillar.title
              const body = pillar.body
              return (
              <article key={title} className="glass-vanguard rounded-sm p-8 card-vanguard-hover">
                <div className="luxury-icon-box w-11 h-11 rounded-sm mb-6">
                  {Icon ? <Icon className="w-5 h-5 text-[#c9a962]" /> : <BrandMark size={24} />}
                </div>
                <h3 className="font-display font-semibold text-xl text-[#f4efe6] mb-3 tracking-wide">{title}</h3>
                <p className="text-sm text-[#6b665c] leading-relaxed font-light">{body}</p>
              </article>
              )
            })}
          </div>
        </div>
      </section>

      <FaqSection />

      <section className="relative py-28 border-t border-[#c9a962]/10">
        <div className="max-w-3xl mx-auto px-5 text-center">
          <div className="luxury-rule w-16 mx-auto mb-8" />
          <h2 className="font-display font-semibold text-3xl md:text-4xl text-[#f4efe6] mb-5 tracking-wide">
            Ready to explore Aria Market?
          </h2>
          <p className="text-[#6b665c] mb-10 font-light">
            Flagship subsidiary of {HOLDING} — real-time DEX analysis, alerts, and ARIA agent.
          </p>
          <ProductLaunchLink className="btn-vanguard-glow px-12 py-4 font-display text-lg tracking-wide inline-flex items-center gap-3 focus-ring">
            Open Aria Market
            <ArrowRight className="w-5 h-5" />
          </ProductLaunchLink>
        </div>
      </section>

      <footer className="border-t border-[#c9a962]/12 py-14">
        <div className="max-w-6xl mx-auto px-5">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-6 mb-10">
            <div className="flex items-center gap-4">
              <div className="luxury-icon-box w-11 h-11 rounded-sm">
                <BrandMark size={26} />
              </div>
              <div>
                <p className="font-display font-semibold text-[#f4efe6] tracking-wide">{HOLDING}</p>
                <p className="text-xs text-[#6b665c] tracking-widest uppercase mt-1">Zero-Human Company</p>
              </div>
            </div>
            <ProductLaunchLink className="btn-vanguard-secondary inline-flex items-center justify-center gap-2 px-6 py-3 text-sm w-fit focus-ring">
              Open Aria Market <ArrowRight className="w-4 h-4 text-[#c9a962]" />
            </ProductLaunchLink>
          </div>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-8 border-t border-[#c9a962]/10 text-xs text-[#6b665c]">
            <p>
              © {new Date().getFullYear()}{' '}
              <span className="text-[#c9a962]">{HOLDING}</span>
              {' · '}Aria Market is a subsidiary
            </p>
            <a
              href={HOLDING_SITE_URL}
              className="font-mono text-[#8a7344] hover:text-[#c9a962] transition-colors focus-ring"
            >
              {setup?.holding_domain ?? HOLDING_DOMAIN}
            </a>
          </div>
        </div>
      </footer>
    </div>
  )
}