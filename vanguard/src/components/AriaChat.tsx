import { Bot, Send, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { agentChat } from '../api'
import { cn } from '../lib/cn'

const SUGGESTIONS = [
  'What is Aria Vanguard ZHC?',
  'How does the ZHC model work?',
  'Tell me about Aria Market',
]

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
      setReply('ARIA is temporarily unavailable. Try again or open Aria Market for the full agent.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="glass-vanguard rounded-sm p-6 border border-[#c9a962]/15">
      <div className="flex items-center gap-3 mb-5">
        <div className="luxury-icon-box w-10 h-10 rounded-sm shrink-0">
          <Bot className="w-5 h-5 text-[#c9a962]" />
        </div>
        <div>
          <h3 className="font-display font-semibold text-[#f4efe6] tracking-wide">Talk to ARIA</h3>
          <p className="text-xs text-[#6b665c] tracking-widest uppercase mt-0.5">Build · marketing · comms · FAQ</p>
        </div>
      </div>

      {!reply && !loading && (
        <div className="flex flex-wrap gap-2 mb-5">
          {SUGGESTIONS.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => send(q)}
              className="btn-vanguard-secondary px-3 py-1.5 text-xs tracking-wide focus-ring"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      <form
        onSubmit={(e) => { e.preventDefault(); send(input) }}
        className="flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about the holding, ventures, or ZHC…"
          className="flex-1 px-4 py-3 bg-[#08080a]/80 border border-[#c9a962]/20 text-sm text-[#e8d5a8] placeholder:text-[#4a4840] focus-visible:outline-none focus-visible:border-[#c9a962]/45 focus-ring"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="btn-vanguard-glow px-4 py-3 disabled:opacity-40 focus-ring"
          aria-label="Send message"
        >
          <Send className="w-4 h-4" />
        </button>
      </form>

      {(loading || reply) && (
        <div className="mt-6 pt-6 border-t border-[#c9a962]/12 space-y-4">
          {lastQuestion && (
            <div className="flex justify-end">
              <p className="max-w-[90%] px-4 py-3 text-sm text-[#e8d5a8] border border-[#c9a962]/20 bg-[#c9a962]/5">
                {lastQuestion}
              </p>
            </div>
          )}
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-[#6b665c]">
              <Sparkles className="w-4 h-4 text-[#c9a962]" />
              ARIA is thinking…
            </div>
          ) : reply ? (
            <div
              className={cn(
                'text-sm leading-relaxed whitespace-pre-wrap px-4 py-4 border',
                error
                  ? 'text-[#d4a574] bg-[#c9a962]/5 border-[#c9a962]/25'
                  : 'text-[#b8b2a6] bg-[#08080a]/50 border-[#c9a962]/10',
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