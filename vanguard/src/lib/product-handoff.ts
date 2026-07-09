/** Nettoyage défensif d'un vieux paramètre `?launch=market` — le lanceur en iframe qui
 * l'utilisait (ProductFrame et consorts) a été retiré (09/07, jamais monté dans aucune
 * page réelle) ; ces deux fonctions restent car MemberGate les appelle encore pour
 * assainir l'URL des visiteurs qui arrivent avec un vieux lien. */
export function wantsProductLaunch(): boolean {
  return new URLSearchParams(window.location.search).get('launch') === 'market'
}

export function clearLaunchQuery(): void {
  const params = new URLSearchParams(window.location.search)
  if (!params.has('launch')) return
  params.delete('launch')
  const rest = params.toString()
  const clean = window.location.pathname + (rest ? `?${rest}` : '')
  window.history.replaceState({}, '', clean)
}