# Handoff — 2026-07-07 (soir)

> Pour la prochaine session Claude Code. Branche : `claude/session-context-files-ofl85l`.
> Répondre en **français**. Lire d'abord `CLAUDE.md` (racine) + `docs/architecture-extensibilite.md`.

## Contexte : le proxy git est mort en cours de session
`git push` échouait (`fatal: could not read Username`). Contourné en poussant via
l'**API GitHub** (`mcp__github__push_files`). **Conséquence : une partie du travail est
sur GitHub via l'API, mais certains fichiers du sandbox local N'ONT PAS été poussés.**

## ✅ Sur GitHub (déployable)
- **Moteur de légitimité** (poussé avant la panne) : `skills/mint_authority.py`,
  `skills/safety_screen.py` (has_mint ABI + hard_fail), `skills/dev_wallet.py`,
  `skills/liquidity_depth.py`, `recalibration.py`, `radar_x.py`, `services/x_social.py`,
  `skills/roi_comparables.py`, `knowledge/launchpads.yaml`, changements `acp_onchain_scan.py`,
  `vc_analysis.py`, `vc_report.py`. + leurs tests. + `screened_pool`, `token_absorber`.
- **Carnet + graphique + A-Z** (poussé via API) : `thesis_journal.py`,
  `skills/chart_render.py` (render_scenario_png), `simulate_lifecycle.py`.
- **Contexte** (via API) : `CLAUDE.md` (racine, auto-lu), `.claude/statusline.sh` + `.claude/settings.json`.

## ⚠️ PRIORITÉ 0 — à re-pousser (dans le sandbox local, PAS sur GitHub)
Le code exact est décrit dans `CLAUDE.md` + régénérable depuis le transcript de la session.
À re-créer et pousser (API `push_files` ou session au proxy sain) :
- `base_crawler.py` : `discover_top_pools` (plancher liquidité) + `discover_virtuals_tokens`.
- `services/project_activity.py` : activité GitHub (livre/stagne).
- `weekly_training.py` : `_journal_forecast` (carnet auto à chaque pronostic) + `run_thesis_review`.
- `heartbeat.py` : tâche `vc_thesis_review` (surveillance des thèses).
- `release_pipeline.py` + `knowledge/release_pipeline.yaml` : pipeline de sorties (gaté opérateur) + teasers.
- `docs/roadmap-campagne.md`.
- Tests : `test_base_crawler` (top_pools/virtuals), `test_project_activity`, `test_release_pipeline`, `test_thesis_journal`.
- Graine carnet dev-wallet à retirer d'`improvement_seeds.yaml` (déjà construit).

## 🎯 À FAIRE — priorité haute → basse
1. **DÉPLOIEMENT + premier A-Z réel** (jamais lancé) : sur le VPS `git pull && ./vanguard/deploy.sh`
   puis `docker exec aria-api python -m aria_core.simulate_lifecycle 0x940181a94A35A4569E4529A3CDfB74e38FD98631` (AERO). Voir le cycle complet sur un vrai token.
2. **Sécurité** : IP VPS fuite dans `AGENTS.md` + `docs/deploy-ionos.md` (public) ET dans l'historique
   git → **durcir SSH (clé only + fail2ban) + firewall/Cloudflare** est le vrai correctif (#17, #22).
   Optionnel : sanitiser les 2 fichiers (cosmétique).
3. **Durcir l'anti-scam** (audit) : détection honeypot élargie (`enableTrading`/`tradingActive`/`setBots`
   non détectés), concentration multi-pools (exclure tous les pools), contrat non vérifié → plafonner à CAUTION.
4. **Premier cycle hebdo réel** : le crawl remplit la base, les pronostics tournent, le carnet se remplit.
5. **Screenshot DexScreener réel** (Playwright, Chromium installé sur VPS) — le rendu PIL actuel est correct mais « simpliste ».
6. **AGENTS.md legacy** : décider (sanitiser / pointer vers CLAUDE.md / supprimer).

## État validé
- ~190 tests verts hors-ligne sur la surface. Cycle de vie autonome câblé (localement).
- Campagne marketing = **12 munitions prêtes** (release_pipeline), **gatée opérateur** — rien de public sans `arm_campaign`.
- PC opérateur nettoyé (2 lanceurs ARIA locaux désactivés → fin des doubles Telegram).

## Doctrine (rappel)
Facts-only · jamais de trade auto · zéro trace IA (surfaces client) · anticipation (seams) ·
jamais satisfait = discerner la vraie plus-value, pas retoucher ce qui marche · reconnaître le bon travail.
