---
name: graft-mini-games
description: >
  Développer des mini-jeux et les greffer sur la page d'accueil d'un site :
  image fixe (poster) par jeu, animation au survol souris pour montrer le gameplay.
  Utiliser UNIQUEMENT quand l'utilisateur lance /graft-mini-games ou demande
  explicitement des mini-jeux, jeux sur l'accueil, ou preview animée au hover.
disable-model-invocation: true
user-invocable: true
argument-hint: "nom-du-site ou liste de jeux"
metadata:
  short-description: "Mini-jeux sur accueil — poster fixe + preview hover"
---

# Graft mini-games

Greffer une **galerie de mini-jeux** sur la page d'accueil d'un site existant (lambda ou enrichi).

## Règle UX (non négociable)

| État | Comportement |
|------|--------------|
| **Repos** | Image **fixe** (poster) — une frame représentative, pas d'animation |
| **Survol** (`mouseenter` / `focus`) | **Preview animée** — boucle courte montrant comment on joue |
| **Sortie survol** | Retour immédiat au poster fixe |
| **`prefers-reduced-motion`** | Poster seul, pas d'animation (option : légère pulsation) |

La preview doit être **lisible en 2–4 secondes** de boucle — pas une partie complète.

## Références à lire avant de coder

1. `references/hover-preview-spec.md` — contrat technique poster / preview
2. `references/types.ts` — types `MiniGameDefinition`, `Poster`, `Preview`
3. `references/MiniGameCard.tsx` — carte avec bascule poster ↔ preview
4. `references/MiniGameGrid.tsx` — grille responsive pour l'accueil
5. `references/registry.example.ts` — enregistrement des jeux

Copier `references/*` vers `src/mini-games/` du site cible (adapter imports).

**Repo canonique** : `GoldenFarFR/aria-skills` — installer via `scripts/install.ps1` ou ouvrir ce repo dans Grok.

## Arborescence cible (`src/mini-games/`)

```
src/mini-games/
├── types.ts
├── registry.ts              # liste exportée des jeux
├── MiniGameCard.tsx
├── MiniGameGrid.tsx
├── index.css                # styles carte + transitions (ou dans src/index.css)
├── posters/                 # composants poster OU assets /public/mini-games/<id>/poster.webp
│   └── SnakePoster.tsx
└── previews/                # une preview animée par jeu
    └── SnakePreview.tsx
```

## Workflow — greffer sur un site

### 1. Cible

- Site existant (ex. `projets/kikou`) — **ne pas** recréer le repo sauf demande
- Conserver Nav, Privy, footer — **ajouter** une section jeux sous le hero

### 2. Créer l'infrastructure (une fois par site)

1. Copier `MiniGameCard`, `MiniGameGrid`, `types.ts` depuis `references/`
2. Créer `registry.ts` vide ou avec jeux demandés
3. Ajouter styles minimaux (`.mini-game-card`, frame `aspect-[4/3]`, `overflow-hidden`)
4. Modifier `HomePage.tsx` :
   - Hero existant en haut
   - Section `<MiniGameGrid games={MINI_GAMES} />` en dessous

### 3. Ajouter un mini-jeu (répéter par jeu)

Pour chaque jeu :

1. **Id** kebab-case : `snake`, `pong`, `memory`, …
2. **Poster** — image fixe :
   - Préférer `public/mini-games/<id>/poster.webp` (1200×900 ou 4:3)
   - Ou composant React/CSS qui dessine **une frame figée**
3. **Preview** — composant React :
   - Animation **uniquement** quand la carte est en mode preview (prop `active`)
   - Boucle 2–4 s : `requestAnimationFrame` ou CSS `@keyframes`
   - Pas de son, pas de clic requis pour la preview
4. Entrée dans `registry.ts` : `title`, `description`, `Poster`, `Preview`, `href` (route jeu optionnelle)
5. `npm run build` — vérifier pas de regression bundle

### 4. Qualité preview

- Montrer **mécanique principale** (mouvement, objectif, contrôle implicite)
- 60 fps max pas requis ; fluidité perçue suffisante
- Couleurs cohérentes avec le poster (même palette)
- Taille preview = même box que poster (pas de layout shift)

### 5. Jeux multiples

L'utilisateur peut demander « plein de mini-jeux » — les développer **un par un** ou par lot de 3–5, toujours :

- poster fixe + preview hover pour **chaque** jeu
- grille homogène sur l'accueil

Catalogue suggéré (au choix utilisateur) : Snake, Pong, Memory, 2048, Breakout, Flappy, Tic-tac-toe, Whack-a-mole.

## Patterns preview (choisir le plus simple)

| Jeu | Poster | Preview hover |
|-----|--------|---------------|
| Snake | Tête + corps figé | Serpent qui avance en boucle |
| Pong | Raquettes + balle au centre | Balle rebondit entre raquettes |
| Memory | Grille cartes dos visible | Deux cartes se retournent puis se referment |
| 2048 | Grille avec quelques tuiles | Une fusion + glissement en boucle |

Voir `references/previews/` pour exemples Snake et Pong.

## Intégration HomePage (extrait)

```tsx
import { MiniGameGrid } from '../mini-games/MiniGameGrid'
import { MINI_GAMES } from '../mini-games/registry'

// Dans <main> après le hero :
<section className="w-full max-w-5xl mx-auto px-5 pb-16">
  <h2 className="text-xl font-medium text-white mb-6 text-center">Mini games</h2>
  <MiniGameGrid games={MINI_GAMES} />
</section>
```

## Ne pas faire

- Animer le poster au repos (sauf `prefers-reduced-motion` fallback)
- Charger la skill `spawn-holding-site` sauf création d'un **nouveau** site
- Jeu jouable complet dans la carte — la carte = **teaser** ; le jeu complet = route `/play/<id>` via skill **`web-playable-game`**
- Vidéo lourde ou GIF multi-Mo — préférer CSS / canvas / React léger

## Livrable par session

- Liste des jeux ajoutés
- Fichiers créés sous `src/mini-games/`
- `HomePage` mise à jour
- `npm run build` OK
- Rappel : tester hover desktop + focus clavier + reduced-motion

## Voir aussi

- **`web-playable-game`** — GameShell, boucle jouable, `/play/:id`, design tokens gaming