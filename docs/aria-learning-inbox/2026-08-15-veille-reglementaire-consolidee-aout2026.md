# [VPS Research] Veille réglementaire consolidée — sept nouveaux fronts (13-15/08/2026)

## Contexte et périmètre

Veille juridique pure, pas une proposition de structure — même doctrine que
`2026-08-06-veille-juridique-responsabilite-agents-ia-trading.md` (déjà
existante, ne pas dupliquer). Ce fichier consolide les entrées du journal
`research-log.md` (13-15/08/2026) qui apportent un front réglementaire ou un
précédent RÉELLEMENT nouveau — la classification SEC/CFTC en 5 catégories et
le précédent Coinbase Advisor, re-loggés ces jours-ci en des termes proches,
sont déjà couverts par la fiche du 06/08 et ne sont PAS repris ici (doublon
partiel écarté conformément à la règle de dédup du journal). Pertinent pour
`docs/conformite-dossier-avocat.md` (dossier à valider par un avocat avant
tout encaissement réel) — banque des précédents à verser au dossier le
moment venu, ne tranche rien lui-même. Aucun code, aucune structure
juridique proposée ici.

## Sept fronts/précédents nouveaux (vérifiés dans le journal, sourcés)

1. **FINRA — rapport annuel de supervision réglementaire 2026** (publié
   12/2025, largement commenté mi-2026) nomme les agents IA autonomes SANS
   "human in the loop" comme risque émergent prioritaire pour les firmes
   financières régulées US. Attentes concrètes détaillées : surveillance de
   l'accès système d'un agent, protocole de supervision humaine défini,
   traçabilité systématique de chaque décision/action, garde-fous limitant
   le comportement de l'agent — formalise par écrit, via une autorité
   d'autorégulation des courtiers reconnue, exactement la doctrine de
   gouvernance qu'ARIA applique déjà (décision humaine finale, journalisation
   systématique, `wallet_guard`/plafonds).
2. **Ville de Baltimore poursuit Kalshi et Polymarket** (13/08/2026),
   qualifiant les marchés de prédiction de paris sportifs illégaux —
   cinquième juridiction distincte (après l'État de New York, la CFTC,
   le Conseil municipal NYC, le jugement fédéral du Michigan) et premier
   front au niveau MUNICIPAL, attaquant frontalement la qualification même
   du produit plutôt qu'un aspect périphérique (marketing, licence).
3. **Tribunal fédéral du Michigan (07/08/2026)** — première défaite
   judiciaire EFFECTIVE (pas une enquête) pour Coinbase/Kalshi : un juge
   rejette l'argument de préemption fédérale (loi commodities/CFTC)
   invoqué pour écarter le droit du jeu de l'État sur les contrats sportifs
   Kalshi distribués via Coinbase.
4. **Union européenne — premier front réglementaire non-américain** logué
   dans ce journal : obligations pleines de l'AI Act pour les systèmes à
   haut risque en vigueur depuis le 02/08/2026 (gestion des risques continue,
   traçabilité, supervision humaine, résilience cybersécurité — amendes
   jusqu'à 15M€ ou 3% du CA mondial) — MAIS un report à décembre
   2027/août 2028 serait voté par le Parlement européen selon plusieurs
   cabinets, désaccord non résolu entre sources à re-vérifier. `ariavanguardzhc.com`
   crée une exposition UE non nulle même si l'opérateur n'est pas basé en UE.
5. **Lettre bipartisane de 7 élus (Foster/Sherman en tête, 23/06/2026)**
   au président de la SEC, 13 questions écrites sur la supervision des
   agents IA de trading — angle inédit : risque de "décisions de trading
   corrélées"/"comportement de troupeau" si plusieurs agents entraînés sur
   des données similaires réagissent simultanément, amplifiant la
   volatilité. Premier front LÉGISLATIF (Congrès, pas un régulateur) avec
   un angle de risque SYSTÉMIQUE plutôt qu'individuel.
6. **Effondrement d'ai16z/ElizaOS (04-05/08/2026)** — le "fonds de venture
   autonome piloté par IA" (pic 2,4Md$) déclaré mort par son propre
   fondateur après un accord transactionnel dans une class action fédérale
   (Doe v. Walters, Burwick Law) alléguant que l'agent "Marc AIndreessen"
   était en réalité opéré par des humains, pas autonome comme annoncé —
   trésorerie transférée aux plaignants, token -97%. Premier précédent
   JUDICIAIRE concret et sanctionné sur le risque de revendiquer une
   autonomie IA non réelle en gestion de capital (le framework open-source
   ElizaOS continue, seul le token/la représentation "fonds autonome"
   s'effondre).
7. **Lumenai Innovation Fund** — premier fonds spéculatif institutionnel à
   architecture pleinement "agentique" (agents IA autonomes générant/
   évaluant/gérant des idées d'investissement en continu), lancement
   opérationnel ~juin 2026, actions long-short global. Supervision humaine
   explicitement retenue pour la gouvernance/risque/stratégie — pas pour la
   décision d'investissement elle-même. Précédent institutionnel nommé et
   daté qui formalise publiquement le même partage de rôles qu'ARIA
   revendique en interne.

## Potentiel concret pour ARIA

Sept précédents chiffrés/datés supplémentaires à verser au dossier avocat
(`docs/conformite-dossier-avocat.md`) avant toute validation. Deux lectures
utiles pour la doctrine ARIA déjà en place : (a) le front ai16z est un
CONTRE-exemple direct — sanctionné précisément pour avoir revendiqué une
autonomie qu'il n'avait pas, ce qu'ARIA n'a jamais fait (gouvernance stricte,
décision finale humaine, `docs/protocole-argent-reel.md`) ; (b) FINRA et
Lumenai sont des exemples POSITIFS — une autorité reconnue et un fonds
institutionnel formalisent tous deux la même doctrine "agent autonome +
supervision humaine non-décisionnelle" qu'ARIA applique déjà, repères de
légitimité externe si la doctrine doit un jour être présentée à un tiers.
Aucune action de code — pur repère juridique/stratégique à intégrer au
dossier avocat par une session dédiée à sa mise à jour.

## Branches ouvertes (banquées, pas creusées)

- Statut réel de la date d'application de l'AI Act UE (02/08/2026 vs report
  2027/2028) à trancher avant toute conclusion — sources contradictoires.
- Suivre si la SEC répond formellement à la lettre Foster/Sherman (13
  questions écrites, 23/06/2026) — pas de réponse connue à ce jour.
- Le rapport de force Kalshi/Polymarket (81%/19% du volume début 08/2026,
  logué séparément) remet en question l'hypothèse implicite du choix
  mono-Polymarket pour la poche paper #108 — traité comme piste produit,
  pas juridique, voir le backlog technique plutôt que cette fiche.

## Sources (citées dans les entrées originales du journal, à recroiser au moment de la mise à jour du dossier)

- Rapport de supervision FINRA 2026 (agents IA, "human in the loop").
- Poursuite ville de Baltimore vs Kalshi/Polymarket, 13/08/2026.
- Jugement fédéral du Michigan, 07/08/2026 (préemption CFTC rejetée).
- AI Act UE, obligations haut risque, 02/08/2026 (statut de report à
  reconfirmer).
- Lettre Foster/Sherman à la SEC, 23/06/2026.
- Doe v. Walters (Burwick Law), effondrement ai16z/ElizaOS, 04-05/08/2026.
- Lancement opérationnel Lumenai Innovation Fund, ~06/2026.
