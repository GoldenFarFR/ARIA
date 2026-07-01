import { getMemberProfile } from './member-profile'
import { getVisitorId } from './visitor'

const PREFS_PREFIX = 'aria:visitor-prefs:'
const MEMBER_HANDLE_KEY = 'aria:member-handle'
const LEGACY_BANNER_DISMISS = 'aria:community-banner-dismissed'

export interface VisitorPrefs {
  bannerMinimized?: boolean
  formOpen?: boolean
  feedbackDraft?: { message: string; handle: string }
}

export function getStoredMemberHandle(): string | null {
  try {
    const fromProfile = getMemberProfile()?.handle?.replace(/^@/, '')
    if (fromProfile) return fromProfile
    return localStorage.getItem(MEMBER_HANDLE_KEY)
  } catch {
    return null
  }
}

export function setStoredMemberHandle(handle: string | undefined): void {
  try {
    const clean = handle?.replace(/^@/, '').trim()
    if (clean) localStorage.setItem(MEMBER_HANDLE_KEY, clean)
  } catch {
    /* ignore */
  }
}

export function prefsUserKey(): string {
  const member = getStoredMemberHandle()
  if (member) return `member:${member}`
  return `anon:${getVisitorId()}`
}

function storageKey(): string {
  return `${PREFS_PREFIX}${prefsUserKey()}`
}

function isMemberKey(key: string): boolean {
  return key.startsWith('member:')
}

function readStore(key: string): Storage {
  return isMemberKey(key) ? localStorage : sessionStorage
}

export function loadVisitorPrefs(): VisitorPrefs {
  purgeLegacyBannerDismiss()
  const key = storageKey()
  try {
    const raw = readStore(key).getItem(key)
    if (!raw) return {}
    const prefs = JSON.parse(raw) as VisitorPrefs
    if (!isMemberKey(prefsUserKey())) {
      delete prefs.bannerMinimized
    }
    return prefs
  } catch {
    return {}
  }
}

export function saveVisitorPrefs(patch: Partial<VisitorPrefs>): void {
  const key = storageKey()
  const member = isMemberKey(key)
  const next = { ...loadVisitorPrefs(), ...patch }
  if (!member) {
    delete next.bannerMinimized
  }
  try {
    readStore(key).setItem(key, JSON.stringify(next))
  } catch {
    /* ignore */
  }
}

/** À la connexion : reprendre brouillon anonyme sous le profil membre. */
export function mergeAnonPrefsIntoMember(memberHandle: string): void {
  const anonKey = `${PREFS_PREFIX}anon:${getVisitorId()}`
  const memberKey = `${PREFS_PREFIX}member:${memberHandle.replace(/^@/, '')}`
  try {
    const anonRaw = localStorage.getItem(anonKey)
    if (!anonRaw) return
    const anon = JSON.parse(anonRaw) as VisitorPrefs
    const member = JSON.parse(localStorage.getItem(memberKey) || '{}') as VisitorPrefs
    const merged: VisitorPrefs = {
      bannerMinimized: member.bannerMinimized ?? anon.bannerMinimized,
      formOpen: member.formOpen ?? anon.formOpen,
      feedbackDraft: member.feedbackDraft?.message
        ? member.feedbackDraft
        : anon.feedbackDraft,
    }
    localStorage.setItem(memberKey, JSON.stringify(merged))
    sessionStorage.removeItem(anonKey)
  } catch {
    /* ignore */
  }
}

export function purgeLegacyBannerDismiss(): void {
  try {
    localStorage.removeItem(LEGACY_BANNER_DISMISS)
  } catch {
    /* ignore */
  }
}