const PROFILE_KEY = 'aria:member-profile'
const WELCOME_DISMISSED_KEY = 'aria:welcome-dismissed'

export interface MemberProfile {
  handle?: string
  message: string
}

export function setMemberProfile(profile: MemberProfile): void {
  try {
    sessionStorage.setItem(PROFILE_KEY, JSON.stringify(profile))
    sessionStorage.removeItem(WELCOME_DISMISSED_KEY)
  } catch {
    /* ignore */
  }
}

export function getMemberProfile(): MemberProfile | null {
  try {
    const raw = sessionStorage.getItem(PROFILE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as MemberProfile
  } catch {
    return null
  }
}

export function clearMemberProfile(): void {
  try {
    sessionStorage.removeItem(PROFILE_KEY)
    sessionStorage.removeItem(WELCOME_DISMISSED_KEY)
  } catch {
    /* ignore */
  }
}

export function dismissMemberWelcome(): void {
  try {
    sessionStorage.setItem(WELCOME_DISMISSED_KEY, '1')
  } catch {
    /* ignore */
  }
}

export function shouldShowMemberWelcome(hasSession: boolean): boolean {
  if (!hasSession) return false
  if (!getMemberProfile()) return false
  try {
    return sessionStorage.getItem(WELCOME_DISMISSED_KEY) !== '1'
  } catch {
    return true
  }
}