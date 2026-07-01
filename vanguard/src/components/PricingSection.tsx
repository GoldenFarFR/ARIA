import { Check, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getBillingPlan, type BillingPlan } from '../api'
import { SubscribeProButton } from './SubscribeProButton'

export function PricingSection() {
  const [plan, setPlan] = useState<BillingPlan | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    getBillingPlan()
      .then(setPlan)
      .catch(() => setLoadError('Tarifs indisponibles pour le moment.'))
  }, [])

  const price = plan?.price_usd ?? 12
  const features = plan?.features ?? [
    'Alertes watchlist prioritaires',
    'Brief signaux hebdomadaire',
    'Accès membre Aria Market',
    'Canal Telegram Pro (bientôt)',
  ]

  return (
    <section id="pricing" className="relative py-24 md:py-32 border-t border-[#c9a962]/10">
      <div className="max-w-6xl mx-auto px-5">
        <div className="text-center mb-14">
          <p className="section-label mb-4">Produit</p>
          <h2 className="font-display font-semibold text-3xl md:text-4xl text-[#f4efe6] tracking-wide mb-4">
            Aria Market Pro
          </h2>
          <p className="text-[#6b665c] max-w-xl mx-auto font-light">
            Signaux actionnables pour traders Base — abonnement mensuel, capital minimal pour démarrer.
          </p>
        </div>

        <div className="max-w-lg mx-auto glass-vanguard rounded-sm p-8 md:p-10 border border-[#c9a962]/20 relative overflow-hidden">
          <div className="absolute top-0 right-0 w-48 h-48 bg-[#c9a962]/10 rounded-full luxury-orb -translate-y-1/2 translate-x-1/2" />
          <div className="relative">
            <div className="luxury-badge inline-flex items-center gap-2 mb-6">
              <Sparkles className="w-3.5 h-3.5 text-[#c9a962]" />
              Flagship · Aria Vanguard ZHC
            </div>
            <p className="font-display text-4xl text-[#e8d5a8] mb-1">
              {price}$
              <span className="text-lg text-[#6b665c] font-light"> / mois</span>
            </p>
            <p className="text-sm text-[#8a8578] mb-8">Annulable à tout moment · paiement Stripe</p>

            <ul className="space-y-3 mb-8">
              {features.map((f) => (
                <li key={f} className="flex items-start gap-3 text-sm text-[#b8b2a6]">
                  <Check className="w-4 h-4 text-[#c9a962] shrink-0 mt-0.5" />
                  {f}
                </li>
              ))}
            </ul>

            <SubscribeProButton stripeReady={plan?.stripe_configured ?? false} />
            {loadError && (
              <p className="text-xs text-[#8a7344] mt-3 text-center">{loadError}</p>
            )}
            {!plan?.stripe_configured && plan && (
              <p className="text-xs text-[#6b665c] mt-3 text-center">
                Paiement en cours de configuration — liste d&apos;attente ouverte.
              </p>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}