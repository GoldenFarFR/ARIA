const STEPS = [
  {
    title: 'Sourcing',
    body: "Découverte de pools sur Base (volume, nouveauté, niche Virtuals pré-bonding) et écoute sociale (X). Le social réveille l'attention, il ne déclenche jamais un ordre.",
  },
  {
    title: 'Filtre de sécurité',
    body: "Contrat, holders, honeypot, taxes réelles, propriété du mint (Blockscout + GoPlus Security). Un token qui échoue ici n'atteint jamais l'analyse suivante.",
  },
  {
    title: 'Analyse quantitative',
    body: 'Niveaux techniques réels (support/résistance, RSI, EMA/MACD, Bollinger, Fibonacci + divergence), liquidité, concentration des holders, comportement smart-money — tout dérivé de données on-chain vérifiables, jamais estimé.',
  },
  {
    title: 'Analyse qualitative (LLM)',
    body: "Thèse d'investissement ancrée sur les chiffres ci-dessus, jamais librement inventée. Toute donnée manquante est déclarée comme telle plutôt que comblée.",
  },
  {
    title: 'Juge adverse',
    body: "Une seconde passe LLM, indépendante, cherche activement les failles de l'analyse avant qu'elle ne sorte.",
  },
  {
    title: 'Track record',
    body: 'Chaque verdict est journalisé et daté avant de connaître son issue. Le résultat réel (P&L) est attribué plus tard, jamais réécrit — la calibration ci-dessus vient de ce journal.',
  },
]

export function CockpitMethodologyPanel() {
  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <p className="section-label mb-5">Comment ARIA arrive à ses résultats</p>
      <div className="relative">
        <div
          className="absolute left-[13px] top-2 bottom-2 w-px"
          style={{ background: 'linear-gradient(180deg, rgba(201,169,98,0.35), rgba(201,169,98,0.05))' }}
        />
        <div className="space-y-5">
          {STEPS.map((step, i) => (
            <div key={step.title} className="relative flex gap-4">
              <span
                className="relative z-10 shrink-0 w-[27px] h-[27px] rounded-full flex items-center justify-center text-[11px] font-mono"
                style={{
                  color: '#0f0e0c',
                  backgroundColor: '#c9a962',
                  boxShadow: '0 0 0 4px #26241f',
                }}
              >
                {i + 1}
              </span>
              <div className="pt-0.5">
                <p className="text-sm text-[#f4efe6] font-medium mb-1">{step.title}</p>
                <p className="text-xs text-[#8b8f9a] leading-relaxed max-w-xl">{step.body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
      <p className="text-[11px] text-[#8b8f9a] mt-6 pt-4 border-t border-[rgba(201,169,98,0.12)] leading-relaxed">
        Aucune étape n'exécute de trade. La signature reste humaine, toujours.
      </p>
    </div>
  )
}
