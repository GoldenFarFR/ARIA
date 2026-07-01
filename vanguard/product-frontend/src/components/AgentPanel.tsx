import { Bot, Send, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { agentChat, getAgentMessages } from '../api'
import type { ChatMessage } from '../types'

const QUICK_COMMANDS = [
  'What is ARIA and what does she do?',
  'Draft a holding marketing update',
  'FAQ: What is Aria Market?',
  'Plan a build for the dashboard',
  'Which BASE launchpad for a token?',
]

export function AgentPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getAgentMessages().then(setMessages).catch(console.error)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async (text: string) => {
    if (!text.trim() || loading) return
    setInput('')
    setLoading(true)
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: 'user', content: text, created_at: new Date().toISOString() },
    ])
    try {
      const res = await agentChat(text)
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'agent',
          content: res.reply,
          skill_used: res.skill_used,
          created_at: new Date().toISOString(),
        },
      ])
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="pixel-panel overflow-hidden flex flex-col h-[520px]">
      <div className="px-4 py-3 border-b-2 border-border-bright flex items-center justify-between bg-panel-elevated">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 pixel-panel-inset flex items-center justify-center">
            <Bot className="w-4 h-4 text-accent" />
          </div>
          <div>
            <h3 className="font-display text-[9px] text-terminal">ARIA</h3>
            <p className="text-xs text-gray-500">
              Heart of the project · build · marketing · comms · FAQ
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center py-8">
            <Sparkles className="w-8 h-8 text-violet-400 mx-auto mb-3 opacity-60" />
            <p className="text-sm text-gray-400 mb-2">
              ARIA is the heart of the project — she builds, markets, communicates, and runs the FAQ.
            </p>
            <p className="text-xs text-violet-400/80 mb-4">
              Ask for plans, copy drafts, holding answers, or DEX education.
            </p>
            <div className="flex flex-wrap gap-2 justify-center">
              {QUICK_COMMANDS.map((cmd) => (
                <button
                  key={cmd}
                  onClick={() => send(cmd)}
                  className="pixel-btn text-base px-3 py-1 focus-ring"
                >
                  {cmd}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[85%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'pixel-panel-inset text-terminal border-accent'
                  : 'pixel-panel-inset text-[#c8c4bc] border-border'
              }`}
            >
              {msg.content}
              {msg.skill_used && (
                <div className="text-xs text-violet-400/70 mt-1">skill: {msg.skill_used}</div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="text-xs text-gray-500 animate-pulse">ARIA is thinking...</div>
        )}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); send(input) }}
        className="p-3 border-t-2 border-border-bright flex gap-2 bg-panel-elevated"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Talk to ARIA..."
          className="flex-1 px-3 py-2 pixel-input focus-ring"
        />
        <button
          type="submit"
          disabled={loading}
          className="pixel-btn pixel-btn-primary px-3 py-2 disabled:opacity-40 focus-ring"
        >
          <Send className="w-4 h-4" />
        </button>
      </form>
    </div>
  )
}