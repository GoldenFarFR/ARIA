import { useEffect, useRef, useState } from 'react'
import { getToken } from '../lib/auth'
import { getVisitorId } from '../lib/visitor'
import type { Alert } from '../types'

interface WsMessage {
  type: string
  payload: Record<string, unknown>
}

export function useWebSocket(onAlert?: (alert: Alert) => void) {
  const [connected, setConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState<string | null>(null)
  const callbackRef = useRef(onAlert)

  useEffect(() => {
    callbackRef.current = onAlert
  }, [onAlert])

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws`)
    let authed = false

    ws.onopen = () => {
      const token = getToken()
      if (token) {
        ws.send(JSON.stringify({ type: 'auth', token }))
      } else {
        ws.send(JSON.stringify({ type: 'auth', visitor_id: getVisitorId() }))
      }
    }

    ws.onclose = () => setConnected(false)
    ws.onerror = () => setConnected(false)

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WsMessage
        if (data.type === 'connected') {
          setConnected(true)
          authed = true
          setLastMessage(String(data.payload.message ?? 'Connected'))
        }
        if (data.type === 'alert' && callbackRef.current) {
          callbackRef.current(data.payload as unknown as Alert)
        }
      } catch {
        setLastMessage(event.data)
      }
    }

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN && authed) ws.send('ping')
    }, 25000)

    return () => {
      clearInterval(ping)
      ws.close()
    }
  }, [])

  return { connected, lastMessage }
}