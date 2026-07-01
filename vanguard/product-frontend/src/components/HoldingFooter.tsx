import { Building2 } from 'lucide-react'
import { HOLDING_DOMAIN, HOLDING_NAME, HOLDING_SITE_URL } from '../lib/site'

export function HoldingFooter() {
  const year = new Date().getFullYear()

  return (
    <footer className="border-t-2 border-border-bright bg-panel mt-10">
      <div className="max-w-7xl mx-auto px-4 py-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 text-base text-[#6a6a72] font-terminal">
        <div className="flex items-center gap-2">
          <Building2 className="w-4 h-4 text-accent shrink-0" />
          <span>
            <span className="text-terminal">{HOLDING_NAME}</span>
            {' · subsidiary product'}
          </span>
        </div>
        <a
          href={HOLDING_SITE_URL}
          className="text-accent hover:text-terminal font-mono text-sm transition-colors focus-ring"
        >
          {HOLDING_DOMAIN}
        </a>
        <p className="sm:text-right">© {year}</p>
      </div>
    </footer>
  )
}