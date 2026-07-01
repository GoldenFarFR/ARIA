# ARIA → Ouvrier Cursor

> **SSOT** — ARIA écrit ici quand elle est bloquée.  
> **Cursor/Grok (ouvrier)** : à chaque session, traiter **tous** les items `[pending]`, puis les passer en `[done]`.

Dernière mise à jour ouvrier : 2026-06-20

---

## [done] triage-issues-2026-06-20 — 2026-06-20T17:00:00Z

**Titre :** Triage 32 issues aria-sandbox + 1 aria-vanguard  
**Source :** ouvrier Cursor (demande Sylvain)

### Problème
31 issues ouvertes sur `aria-sandbox` (rafale `identity_anchor` + doublons `image_api_key`) — dedup local Render éphémère.

### Action réalisée
- Fermé #1–#32 en doublon / résolu, #33 `identity_anchor` completed
- Fermé #4 `image_api_key` — secret Render requis (voir ci-dessous)
- Fermé aria-vanguard #1 bump pin (déjà fait)
- Code : dedup GitHub open issues + `ensure_identity_anchor_from_current()` — `0fe97c36`

### Reste opérateur (pas code)
- [ ] `IMAGE_API_KEY=xai-...` dans `production.env` + `sync-render.ps1` si bannière xAI souhaitée
- [ ] Sinon : envoyer photo référence Telegram `/avatar identity` si pas de `current.jpg` sur Render

---

## [done] gem-crush-assets-sprint-wave1 — 2026-06-20T17:40:00Z

**Brief :** `aria-sandbox/docs/gem-crush-assets-sprint.md`  
**Livré :** GemSprite SVG, LevelMap, chute animée, releases v40–v42 planifiées

---

## [done] gem-crush-error-v37 — 2026-06-20T17:30:00Z

**Problème :** v37 patch anchor `v30/240ms` absent — prod à `v26/280ms`  
**Fix :** `gem_crush_premium.py` + test v37 — `37b49783`, pin vanguard `37b4978`

---

## [done] gem-crush-error-v41 — 2026-06-20T17:50:00Z

**Problème :** v41 patch `board-wrap` sans `data-combo` — sprint assets l'avait déjà ship (`2af25d8`)  
**Fix :** ancre alignée prod (no-op TSX) + test v41 — `ad73fe1e`, pin vanguard `429fcd4`

---

_Aucun `[pending]` restant._

---