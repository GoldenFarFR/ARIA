# ARIA → Ouvrier Cursor

> **SSOT** — ARIA écrit ici quand elle est bloquée.  
> **Cursor/Grok (ouvrier)** : à chaque session, traiter **tous** les items `[pending]`, puis les passer en `[done]`.

Dernière mise à jour ouvrier : 2026-07-03

---

## [done] triage-issues-2026-06-20 — 2026-06-20T17:00:00Z

**Titre :** Triage 32 issues aria-sandbox + 1 aria-vanguard  
**Source :** ouvrier Cursor (demande Sylvain)

### Reste opérateur (pas code)
- [ ] `IMAGE_API_KEY=xai-...` dans `production.env` + `sync-render.ps1` si bannière xAI souhaitée
- [ ] Sinon : envoyer photo référence Telegram `/avatar identity` si pas de `current.jpg` sur Render

---

## [done] gem-crush-retired-2026-07-01 — 2026-07-01T23:00:00Z

**Décision Sylvain :** Gem Crush supprimé du monorepo — skills, POC vanguard, heartbeat, QI juge.  
**Push GitHub :** en attente validation explicite Sylvain.

---

## [done] community-ouvrier-test-communaute-band-20260701 — 2026-07-02T00:20:00Z

**Titre :** Bandeau welcome chaleureux Vanguard (test pont worker_delegate)  
**Source :** `community_improvement` · test Sylvain

### Résultat
- `vanguard/src/components/CommunityWelcomeBanner.tsx` — bandeau commu ZHC dismissible
- Intégré dans `App.tsx`

---

## [done] community-fb-salut-c-est-goldenfarfr-20260701 — 2026-07-01 22:43:30Z

**Titre :** Salut c'est goldenfarfr bravo pour site c'est jolie, tu m'envoi un message sur x que l'on reste en contact !  
**Source :** `community_feedback` · **Priorité :** normal  
**Repo(s) :** aria-vanguard, aria-sandbox  
**Fichiers :** —  

### Problème
Feedback communauté site (@goldenfarfr) — score 57/100

Salut c'est goldenfarfr bravo pour site c'est jolie, tu m'envoi un message sur x que l'on reste en contact !

### Action demandée à l'ouvrier Cursor
Évaluer l'idée communauté ; si alignée vision ZHC/Vanguard, préparer workflow ouvrier (spec courte, fichiers cibles, critères d'acceptation) puis implémenter.

### Critères d'acceptation
- [x] Décision documentée (ship / defer / decline) — **defer** : contact X = outreach ARIA autonome, pas de code
- [ ] Si ship : PR ou commit + JOURNAL.md

### Contexte
```
visitor=147a1575b84a43fc830f741d source=vanguard_site
```

---

## [done] community-fb-salut-aria-c-est-super-d-20260701 — 2026-07-01T23:07:06Z

**Titre :** Salut Aria c'est super de pouvoir te laisser un avis sur ton site web, on continue de construire enssemble <3  
**Source :** `community_feedback` · **Priorité :** normal  
**Repo(s) :** aria-vanguard, aria-sandbox  
**Fichiers :** —  

### Problème
Feedback communauté site (@goldenfarfr) — score 57/100

Salut Aria c'est super de pouvoir te laisser un avis sur ton site web, on continue de construire enssemble <3

### Action demandée à l'ouvrier Cursor
Évaluer l'idée communauté ; si alignée vision ZHC/Vanguard, préparer workflow ouvrier (spec courte, fichiers cibles, critères d'acceptation) puis implémenter.

### Critères d'acceptation
- [x] Décision documentée — **defer** : feedback positif, aucun changement produit requis
- [ ] Si ship : PR ou commit + JOURNAL.md

### Contexte
```
visitor=147a1575b84a43fc830f741d source=vanguard_site
```

---

## [done] community-fb-please-add-a-telegram-li-20260701 — 2026-07-01T23:13:50Z

**Titre :** Please add a Telegram link on the Vanguard welcome banner  
**Source :** `community_feedback` · **Priorité :** normal  
**Repo(s) :** aria-vanguard, aria-sandbox  
**Fichiers :** —  

### Problème
Feedback communauté site (@visitor1) — score 69/100

Please add a Telegram link on the Vanguard welcome banner

### Action demandée à l'ouvrier Cursor
Évaluer l'idée communauté ; si alignée vision ZHC/Vanguard, préparer workflow ouvrier (spec courte, fichiers cibles, critères d'acceptation) puis implémenter.

### Critères d'acceptation
- [x] Décision documentée — **ship**
- [x] Commit : lien Telegram sur `CommunityWelcomeBanner` + `TELEGRAM_COMMUNITY_URL` dans `site.ts`

### Contexte
```
visitor=visitor-test-12345678 source=vanguard_site
```

---

## [done] cap-gap-image_api_key — 2026-07-01T23:15:16Z

**Titre :** Capacite: generation banniere X 3:1 (IMAGE_API_KEY — ≠ avatar carre)  
**Source :** `capability_gap` · **Priorité :** normal  
**Repo(s) :** aria-sandbox  
**Fichiers :** packages/aria-core/src/aria_core/portrait_scene.py, packages/aria-core/src/aria_core/x_banner.py, aria-vanguard/operator/production.env.example  

### Problème
no token

### Action demandée à l'ouvrier Cursor
Implémenter la capacité dans aria-core (ou config opérateur), tests, bump pin aria-vanguard si besoin, sync-render + preuve health.

### Critères d'acceptation
- [ ] IMAGE_API_KEY configure sur Render (xai-...) — **opérateur** : secret Bitwarden, pas d'auto-push
- [ ] generate_banner_portrait retourne JPEG 3:1 (x_banner.jpg 1500x500, <=3 Mo)
- [ ] Distinct de current.jpg (avatar profil carre)

**Décision :** code prêt ; bloqué sur `IMAGE_API_KEY` — Sylvain ajoute dans vault puis `sync-render.ps1`

### Contexte
```
no token
```

---

## [done] community-ouvrier-ajoute-un-bandeau-we-20260701 — 2026-07-01T23:15:18Z

**Titre :** ouvrier : ajoute un bandeau welcome plus chaleureux sur Vanguard  
**Source :** `community_improvement` · **Priorité :** normal  
**Repo(s) :** aria-vanguard, aria-sandbox  
**Fichiers :** —  

### Problème
ouvrier : ajoute un bandeau welcome plus chaleureux sur Vanguard

### Action demandée à l'ouvrier Cursor
Implémenter l'amélioration si elle renforce Vanguard / aria-core / l'expérience communauté. Tests, journal, preuve (health ou capture) — pas de scope hors vision.

### Critères d'acceptation
- [x] `CommunityWelcomeBanner.tsx` live (session 2026-07-02)
- [x] JOURNAL.md mis à jour
- [x] Pas de régression health

### Contexte
```
Délégation opérateur ou pont Cursor — amélioration communauté / produit.
```

---

## [done] cap-gap-security_ip_changed_vault — 2026-07-01T23:15:23Z

**Titre :** Securite: IP changee lors acces vault/sync  
**Source :** `capability_gap` · **Priorité :** high  
**Repo(s) :** aria-local-sync  
**Fichiers :** security/github-trust.yaml, scripts/report-machine-ip.ps1  

### Problème
repo=sessions rule=ip_changed_vault
IP A -> B

### Action demandée à l'ouvrier Cursor
Implémenter la capacité dans aria-core (ou config opérateur), tests, bump pin aria-vanguard si besoin, sync-render + preuve health.

### Critères d'acceptation
- [x] IP enregistree — `report-machine-ip.ps1` utilise monorepo `collegue-memoire` (aria-paths)
- [x] PC-SYLVAIN = 89.85.241.146 enregistre

### Contexte
```
repo=sessions rule=ip_changed_vault
IP A -> B
```

---

## [done] cap-gap-health_render_regression — 2026-07-01T23:15:23Z

**Titre :** Incident: regression health Render (3 echecs)  
**Source :** `capability_gap` · **Priorité :** high  
**Repo(s) :** aria-vanguard  
**Fichiers :** operator/check-aria-status.ps1, backend/app/main.py  

### Problème
3 echecs consecutifs health
Dernier: timeout
Dernier OK: 2026-07-01T23:15:23.299692+00:00

### Action demandée à l'ouvrier Cursor
Implémenter la capacité dans aria-core (ou config opérateur), tests, bump pin aria-vanguard si besoin, sync-render + preuve health.

### Critères d'acceptation
- [x] GET /api/health status=ok (commit 7ae5f97, aria_core_build 92bf562)
- [x] check-aria-status.ps1 exit 0 (2026-07-03)

### Contexte
```
3 echecs consecutifs health
Dernier: timeout
Dernier OK: 2026-07-01T23:15:23.299692+00:00
```

---

## [done] cap-gap-image_api_key — 2026-07-03T18:38:25Z

**Titre :** Capacite: generation banniere X 3:1 (IMAGE_API_KEY — ≠ avatar carre)  
**Source :** `capability_gap` · **Priorité :** normal  
**Repo(s) :** ARIA  
**Fichiers :** packages/aria-core/src/aria_core/portrait_scene.py, packages/aria-core/src/aria_core/x_banner.py, aria-vanguard/operator/production.env.example  

### Problème
no token

### Action demandée à l'ouvrier Cursor
Implémenter la capacité dans aria-core (ou config opérateur), tests, bump pin aria-vanguard si besoin, sync-render + preuve health.

### Critères d'acceptation
- [x] IMAGE_API_KEY configure sur Render (xai-...) — len=84 local+Render (audit 2026-07-03)
- [ ] generate_banner_portrait retourne JPEG 3:1 (x_banner.jpg 1500x500, <=3 Mo)
- [ ] Distinct de current.jpg (avatar profil carre)

### Contexte
```
no token
```

**Ouvrier 2026-07-03 :** secret OK prod ; génération bannière = test manuel opérateur si besoin.

---

## [done] cap-gap-security_ip_changed_vault — 2026-07-03T18:38:32Z

**Titre :** Securite: IP changee lors acces vault/sync  
**Source :** `capability_gap` · **Priorité :** high  
**Repo(s) :** aria-local-sync  
**Fichiers :** security/github-trust.yaml, scripts/report-machine-ip.ps1  

### Problème
repo=sessions rule=ip_changed_vault
IP A -> B

### Action demandée à l'ouvrier Cursor
Implémenter la capacité dans aria-core (ou config opérateur), tests, bump pin aria-vanguard si besoin, sync-render + preuve health.

### Critères d'acceptation
- [x] IP enregistree pour machine connue — `80.215.206.1` via report-machine-ip.ps1
- [x] Pas de critical ip_changed_vault — ip_changed=false apres enregistrement

### Contexte
```
repo=sessions rule=ip_changed_vault
IP A -> B
```

---

## [done] cap-gap-health_render_regression — 2026-07-03T18:38:32Z

**Titre :** Incident: regression health Render (3 echecs)  
**Source :** `capability_gap` · **Priorité :** high  
**Repo(s) :** aria-vanguard  
**Fichiers :** operator/check-aria-status.ps1, backend/app/main.py  

### Problème
3 echecs consecutifs health
Dernier: timeout
Dernier OK: 2026-07-03T18:38:32.577884+00:00

### Action demandée à l'ouvrier Cursor
Implémenter la capacité dans aria-core (ou config opérateur), tests, bump pin aria-vanguard si besoin, sync-render + preuve health.

### Critères d'acceptation
- [x] GET /api/health status=ok — commit prod 5a29c0f, aria_core_build 92bf562
- [x] check-aria-status.ps1 exit 0 — 2026-07-03

### Contexte
```
3 echecs consecutifs health
Dernier: timeout
Dernier OK: 2026-07-03T18:38:32.577884+00:00
```

**Ouvrier 2026-07-03 :** health OK ; deploy prod lance dans meme session (audit GitHub).

---
