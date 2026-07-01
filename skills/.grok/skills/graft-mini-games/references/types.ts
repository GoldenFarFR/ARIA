import type { ComponentType } from 'react'

/** Frame fixe — aucune animation */
export type PosterProps = {
  className?: string
}

/** Teaser gameplay — anime seulement si active */
export type PreviewProps = {
  active: boolean
  className?: string
}

export type MiniGameDefinition = {
  id: string
  title: string
  description: string
  Poster: ComponentType<PosterProps>
  Preview: ComponentType<PreviewProps>
  /** Route vers le jeu complet (optionnel) */
  href?: string
}