import { ChevronDown, HelpCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getFaqContent } from '../api'
import { cn } from '../lib/cn'

interface FaqItem {
  id: string
  question: string
  answer: string
}

function FaqSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="glass-vanguard rounded-xl px-5 py-4">
          <div className="h-4 w-3/4 max-w-md rounded skeleton" />
        </div>
      ))}
    </div>
  )
}

export function FaqSection() {
  const [items, setItems] = useState<FaqItem[]>([])
  const [openId, setOpenId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getFaqContent()
      .then((d) => setItems(d.items ?? []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  if (!loading && !items.length) return null

  const visible = items.slice(0, 4)

  return (
    <section id="faq" className="page-shell py-16 md:py-20 border-t border-[#c9a962]/8">
      <div>
        <p className="section-label mb-3 flex items-center gap-2">
          <HelpCircle className="w-3.5 h-3.5 text-[#c9a962]" />
          FAQ
        </p>
        <h2 className="font-display text-2xl text-[#f4efe6] mb-8 tracking-wide">
          Essentials
        </h2>

        {loading ? (
          <FaqSkeleton />
        ) : (
          <div className="space-y-2">
            {visible.map((item) => {
              const open = openId === item.id
              const panelId = `faq-panel-${item.id}`
              return (
                <div
                  key={item.id}
                  className="minimal-card overflow-hidden"
                >
                  <button
                    type="button"
                    id={`faq-trigger-${item.id}`}
                    aria-expanded={open}
                    aria-controls={panelId}
                    onClick={() => setOpenId(open ? null : item.id)}
                    className="w-full text-left px-5 py-4 flex items-start justify-between gap-3 focus-ring"
                  >
                    <span className="font-display font-medium text-[#f4efe6] pr-2 tracking-wide">{item.question}</span>
                    <ChevronDown
                      className={cn(
                        'w-4 h-4 text-gray-500 shrink-0 mt-0.5 transition-transform duration-200',
                        open && 'rotate-180',
                      )}
                    />
                  </button>
                  <div
                    id={panelId}
                    role="region"
                    aria-labelledby={`faq-trigger-${item.id}`}
                    className={cn(
                      'grid transition-all duration-200',
                      open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
                    )}
                  >
                    <div className="overflow-hidden">
                      <p className="text-sm text-gray-400 px-5 pb-4 leading-relaxed whitespace-pre-wrap border-t border-white/5 pt-3">
                        {item.answer}
                      </p>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}