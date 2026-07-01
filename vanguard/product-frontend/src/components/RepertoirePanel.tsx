import { ChevronRight, Plus, ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { addRepertoireItem, getHoldingStructure, getZhcIntro } from '../api'
import { BrandMark } from './BrandMark'
import type { HoldingStructure, RepertoireItem } from '../types'

const STATUS_COLORS: Record<string, string> = {
  idea: 'text-terminal/50 bg-panel-elevated border-border',
  building: 'text-accent bg-accent/10 border-accent/30',
  live: 'text-buy bg-buy/10 border-buy/30',
  paused: 'text-watch bg-watch/10 border-watch/30',
  archived: 'text-terminal/40 bg-panel border-border',
}

function EntityRow({
  item,
  indent = false,
  holdingName,
}: {
  item: RepertoireItem
  indent?: boolean
  holdingName?: string
}) {
  return (
    <div className={`pixel-panel-inset p-3 ${indent ? 'ml-4' : ''}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-terminal font-medium text-terminal">{item.name}</span>
        <span className={`text-sm px-2 py-0.5 border-2 font-terminal ${STATUS_COLORS[item.status] || STATUS_COLORS.idea}`}>
          {item.status}
        </span>
      </div>
      {indent && holdingName && (
        <p className="text-sm text-violet/70 mb-1 font-terminal">Subsidiary of {holdingName}</p>
      )}
      {item.description && (
        <p className="text-sm text-terminal/50 font-terminal">{item.description}</p>
      )}
      <div className="flex gap-2 mt-1.5 text-sm text-terminal/40 font-terminal">
        <span>P{item.priority}</span>
        {item.zhc_aligned && <span className="text-violet">ZHC</span>}
        <span>{item.category}</span>
        {item.entity_type && item.entity_type !== 'subsidiary' && (
          <span className="text-accent/80">{item.entity_type}</span>
        )}
      </div>
    </div>
  )
}

export function RepertoirePanel() {
  const [structure, setStructure] = useState<HoldingStructure | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const load = () => getHoldingStructure().then(setStructure).catch(console.error)
  useEffect(() => { load() }, [])

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    await addRepertoireItem({ name, description, zhc_aligned: true })
    setName('')
    setDescription('')
    setShowForm(false)
    load()
  }

  const contactJuno = async () => {
    const data = await getZhcIntro()
    window.open(data.x_intent_url, '_blank')
  }

  const holding = structure?.holding
  const subsidiaries = structure?.subsidiaries ?? []
  const ventures = structure?.ventures ?? []

  return (
    <div className="pixel-panel overflow-hidden">
      <div className="px-4 py-3 border-b-2 border-border-bright flex items-center justify-between bg-panel-elevated">
        <div className="flex items-center gap-2">
          <BrandMark size={16} />
          <h3 className="pixel-label">
            {holding?.name ?? 'Aria Vanguard ZHC'}
          </h3>
        </div>
        <div className="flex gap-2">
          <button
            onClick={contactJuno}
            className="text-sm px-2 py-1 pixel-btn text-violet font-terminal flex items-center gap-1"
          >
            <ExternalLink className="w-3 h-3" /> JUNO
          </button>
          <button
            onClick={() => setShowForm(!showForm)}
            className="text-sm px-2 py-1 pixel-btn font-terminal flex items-center gap-1"
          >
            <Plus className="w-3 h-3" /> Subsidiary
          </button>
        </div>
      </div>

      {holding && (
        <div className="px-4 py-2 text-sm border-b-2 border-border space-y-1 font-terminal">
          <p className="text-terminal/50">
            ARIA ZHC — {structure?.aria_title ?? 'Chief Autonomous Officer (CAO)'}
          </p>
          <p className="text-violet/80">
            {structure?.governance_rule ??
              'All ventures register as subsidiaries of the holding — Aria Market included.'}
          </p>
        </div>
      )}

      {showForm && (
        <form onSubmit={handleAdd} className="p-3 border-b-2 border-border space-y-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New subsidiary under Aria Vanguard ZHC"
            className="w-full px-3 py-2 pixel-input text-sm"
          />
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description"
            className="w-full px-3 py-2 pixel-input text-sm"
          />
          <button type="submit" className="text-sm px-3 py-1.5 pixel-btn pixel-btn-primary font-terminal">
            Register under {holding?.name ?? 'Vanguard'}
          </button>
        </form>
      )}

      <div className="p-3 space-y-2 max-h-72 overflow-y-auto">
        {!structure ? (
          <p className="text-sm text-terminal/50 text-center py-4 font-terminal">Loading holding structure…</p>
        ) : (
          <>
            {holding && <EntityRow item={holding} holdingName={holding.name} />}
            {subsidiaries.map((item) => (
              <div key={item.id} className="flex items-start gap-1">
                <ChevronRight className="w-3 h-3 text-terminal/40 mt-4 shrink-0" />
                <div className="flex-1">
                  <EntityRow item={item} indent holdingName={holding?.name} />
                </div>
              </div>
            ))}
            {ventures.map((item) => (
              <div key={item.id} className="flex items-start gap-1">
                <ChevronRight className="w-3 h-3 text-terminal/40 mt-4 shrink-0" />
                <div className="flex-1">
                  <EntityRow item={item} indent holdingName={holding?.name} />
                </div>
              </div>
            ))}
            {subsidiaries.length === 0 && ventures.length === 0 && (
              <p className="text-sm text-terminal/50 text-center py-2 font-terminal">
                No subsidiaries yet — add a venture under the holding
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}