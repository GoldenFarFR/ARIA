import { Send, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { agentChat } from '../api'
import { cn } from '../lib/cn'

const SUGGESTIONS = ['What is Vanguard?', 'ZHC model in brief']

export function AriaChat() {
  const [input, setInput] = useState('')
  const [reply, setReply] = useState<string | null>(null)
  const [lastQuestion, setLastQuestion] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || loading) return
    setLoading(true)
    setReply(null)
    setError(false)
    setLastQuestion(trimmed)
    setInput('')
    try {
      const res = await agentChat(trimmed)
      setReply(res.reply)
    } catch {
      setError(true)
      setReply('ARIA is unavailable. Please try again in a moment.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="minimal-card p-5 md:p-6">
      {!reply && !loading && (
        <div className="flex flex-wrap gap-2 mb-4">
          {SUGGESTIONS.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => send(q)}
              className="text-xs text-[#8a8578] hover:text-[#e8d5a8] border border-[#c9a962]/15 hover:border-[#c9a962]/30 px-3 py-1.5 transition-colors focus-ring"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
        className="flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="A question about Vanguard or ZHC…"
          className="flex-1 px-4 py-3 bg-[#08080a]/80 border border-[#c9a962]/15 text-sm text-[#e8d5a8] placeholder:text-[#4a4840] focus-visible:outline-none focus-visible:border-[#c9a962]/35 focus-ring"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="btn-vanguard-glow px-4 py-3 disabled:opacity-40 focus-ring"
          aria-label="Send"
        >
          <Send className="w-4 h-4" />
        </button>
      </form>

      {(loading || reply) && (
        <div className="mt-5 pt-5 border-t border-[#c9a962]/10 space-y-3">
          {lastQuestion && (
            <p className="text-xs text-[#6b665c] text-right">{lastQuestion}</p>
          )}
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-[#6b665c]">
              <Sparkles className="w-4 h-4 text-[#c9a962]" />
              ARIA is thinking…
            </div>
          ) : reply ? (
            <div
              className={cn(
                'text-sm leading-relaxed whitespace-pre-wrap',
                error ? 'text-[#d4a574]' : 'text-[#b8b2a6]',
              )}
            >
              {reply}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}