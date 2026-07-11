# Roadmap de campagne ARIA — de la fondation au lancement public

> **Statut : BROUILLON à valider par l'opérateur.** Rien ne se diffuse en public tant
> que (1) le produit est parfait, (2) cette roadmap est validée, (3) l'opérateur ARME
> la campagne (`release_pipeline.arm_campaign`). Verrou du dôme : aucune action
> outward-facing autonome.

Ce document est la SSOT de la séquence de lancement. Il ne promet **aucune date** ni
**aucun rendement** : il ordonne les étapes. Voix « building in public », zéro trace IA.

---

## Principe directeur
**La preuve avant la promesse.** On ne lance pas un token, on construit un track record
vérifiable. La campagne ne démarre que sur du RÉEL (vraies analyses, vrai historique),
jamais sur une simulation présentée comme réelle.

---

## Phase 0 — Fondation (EN COURS / quasi terminée)
Construire et tester le moteur complet, hors ligne, avant toute exposition.
- [x] Moteur de légitimité (mint contextuel, launchpad, burn, dev-wallet, liquidité)
- [x] Transparence exigée + recalibrage
- [x] Analyse technique + graphique, projection ROI, Radar X
- [x] Track-record + wallet suivi 85/15
- [x] Carnet de bord + screenshots (chandeliers + simulation) + surveillance des thèses
- [x] Terrain de chasse multi-sources
- [x] Pipeline de sorties + teasers, **gaté opérateur**
- [x] **Déploiement VPS + première vraie analyse A-Z** (débloque le passage en Phase 1) — confirmé déployé (commit `30fd82c05777`, voir `docs/etat-systeme-cable.md`), heartbeat actif depuis le 08/07
- [x] Premier vrai cycle hebdomadaire (pronostics datés, résolution OHLCV) — `vc_crawl`/`vc_resolve`/`vc_weekly_forecast` enregistrés et exécutés en continu (`heartbeat.py`)

## Phase 1 — Produit parfait (avant le feu vert)
Rendre la surface irréprochable pour la gamme luxe (500 $/mois).
- [ ] Vitrine exceptionnelle (page d'accueil client) + cockpit « ARIA en direct »
- [ ] Wallet suivi allumé avec de vrais chiffres (FOMO honnête)
- [ ] Durcissement sécurité (Cloudflare edge, 2FA, secrets en vault)
- [ ] Conformité juridique validée AVANT tout encaissement (déjà : aucun paiement actif)
- [ ] Quelques semaines de track record réel dans le carnet (matière de preuve)

## Phase 2 — Teaser (1 à 2 semaines) — SUR FEU VERT OPÉRATEUR
ARIA chauffe le terrain en mode « on construit quelque chose ».
- [ ] Diffusion d'un teaser tous les 1-2 jours (X + TikTok), gaté
- [ ] Montée d'audience, aucune révélation, aucune promesse chiffrée
- [ ] Compte à rebours vers le reveal

## Phase 3 — Reveal + campagne (2 déploiements/semaine)
Les munitions déjà construites sortent une par une, synchronisées au site.
- [ ] Reveal : le track record public + le carnet ouverts
- [ ] Drop de 2 features/semaine (les 12 munitions du pipeline), site auto-synchro
- [ ] Chaque annonce X + TikTok bascule la feature en « live » sur la vitrine

## Phase 4 — Le pacte (argent réel)
Seulement quand ARIA a prouvé ~90 % de confiance (cf. `docs/protocole-argent-reel.md`).
- [ ] ≥ 80 verdicts sur ≥ 6 mois, calibration prouvée, bat le benchmark
- [ ] Décision humaine de fournir de la vraie liquidité
- [ ] Jamais un fonds pour tiers sans validation d'un avocat (AIF régulé)

---

## Les 3 verrous (dôme)
1. **Aucune diffusion publique** sans `arm_campaign` (feu vert opérateur).
2. **Aucune exécution de trade** sans validation humaine (Telegram/Tangem).
3. **Aucun encaissement** avant feu vert juridique.

## Ce qui débloque quoi
```
Fondation testée + 1er A-Z réel  →  autorise Phase 1
Produit parfait + track record réel + roadmap validée  →  autorise le FEU VERT
Feu vert opérateur (arm_campaign)  →  autorise Teaser puis campagne
90 % de confiance prouvée  →  autorise le pacte (argent réel)
```
