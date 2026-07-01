import { ChevronDown, HelpCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getFaqContent } from '../api'
import { Spinner } from './ui/Spinner'

interface FaqItem {
  id: string
  question: string
  answer: string
  tags?: string[]
}

export function FaqPanel() {
  const [items, setItems] = useState<FaqItem[]>([])
  const [openId, setOpenId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getFaqContent()
      .then((data) => setItems(data.items ?? []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="pixel-panel p-6 flex items-center justify-center gap-3">
        <Spinner size="sm" />
        <span className="text-sm text-terminal/50 font-terminal">Loading FAQ…</span>
      </div>
    )
  }

  return (
    <div className="pixel-panel overflow-hidden">
      <div className="px-4 py-3 border-b-2 border-border-bright flex items-center gap-2 bg-panel-elevated">
        <HelpCircle className="w-4 h-4 text-violet" />
        <div>
          <h3 className="pixel-label">ARIA FAQ</h3>
          <p className="text-sm text-terminal/50 font-terminal mt-0.5">Built and maintained by ARIA — public knowledge</p>
        </div>
      </div>
      <div className="divide-y-2 divide-border max-h-[360px] overflow-y-auto">
        {items.map((item) => {
          const open = openId === item.id
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => setOpenId(open ? null : item.id)}
              className="w-full text-left px-4 py-3 hover:bg-panel-elevated transition-colors"
            >
              <div className="flex items-start justify-between gap-3">
                <span className="text-sm font-terminal font-medium text-terminal">{item.question}</span>
                <ChevronDown
                  className={`w-4 h-4 text-terminal/40 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
                />
              </div>
              {open && (
                <p className="text-sm text-terminal/60 mt-2 leading-relaxed whitespace-pre-wrap font-terminal">
                  {item.answer}
                </p>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}