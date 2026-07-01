# Spécification poster + preview au survol

## Contrat `MiniGameCard`

```
┌─────────────────────────┐
│  REPOS: Poster (fixe)   │  ← img ou composant statique
├─────────────────────────┤
│  HOVER: Preview (loop)  │  ← animation gameplay teaser
└─────────────────────────┘
```

- Même dimensions pour poster et preview (`aspect-ratio: 4/3`, `object-fit: cover` si image)
- Transition optionnelle : `opacity 150ms` ou swap instantané (pas de flash blanc)
- `pointer-events: none` sur la preview pour éviter captures de clic accidentelles

## Props `Preview`

```ts
type PreviewProps = {
  active: boolean  // true seulement quand carte survolée ou focus
}
```

Quand `active === false` : preview **ne tourne pas** (économie CPU, pas d'animation hors écran).

## Accessibilité

- Carte `tabIndex={0}` + `onFocus` / `onBlur` = même comportement que hover
- `@media (prefers-reduced-motion: reduce)` : toujours poster, `aria-label` décrit le jeu
- `alt` sur images poster

## Assets

| Fichier | Rôle |
|---------|------|
| `public/mini-games/<id>/poster.webp` | Frame fixe (recommandé) |
| `src/mini-games/previews/<Id>Preview.tsx` | Animation code |

Poster généré ou exporté depuis la frame 0 de la preview — **cohérence visuelle**.

## Performance

- Max ~6 cartes visibles sans lag : previews inactives = `active: false`
- Pas de `setInterval` global — chaque preview gère sa boucle si `active`
- Canvas : `cancelAnimationFrame` quand `active` passe à false