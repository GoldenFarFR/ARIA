const STORAGE_KEY = 'aria_vanguard_visitor_id'

function randomId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, '').slice(0, 24)
  }
  return `v${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
}

export function getVisitorId(): string {
  try {
    const existing = localStorage.getItem(STORAGE_KEY)
    if (existing && existing.length >= 8) return existing
    const id = randomId()
    localStorage.setItem(STORAGE_KEY, id)
    return id
  } catch {
    return randomId()
  }
}

export function visitorHeaders(): Record<string, string> {
  return { 'X-Visitor-Id': getVisitorId() }
}