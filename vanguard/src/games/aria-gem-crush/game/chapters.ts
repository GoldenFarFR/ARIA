export interface Chapter {
  id: number
  name: string
  subtitle: string
  accent: string
}

export const CHAPTERS: Chapter[] = [
  { id: 1, name: 'Éveil', subtitle: 'Premiers échanges', accent: '#c9a962' },
  { id: 2, name: 'Carrière', subtitle: 'Cascades dorées', accent: '#e8a040' },
  { id: 3, name: 'Sanctuaire', subtitle: 'Combo ARIA', accent: '#9b6bff' },
  { id: 4, name: 'Temple', subtitle: 'Rayures & bombes', accent: '#4ecdc4' },
  { id: 5, name: 'Couronne', subtitle: 'Maître des gemmes', accent: '#ff6b8a' },
]

export function chapterForLevel(level: number): Chapter {
  const idx = Math.min(CHAPTERS.length - 1, Math.max(0, level - 1))
  return CHAPTERS[idx]
}