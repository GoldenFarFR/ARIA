# Stratégie d'investissement d'ARIA — SSOT

> Comment ARIA investit (en **suivi/paper** aujourd'hui, argent réel plus tard selon
> `docs/protocole-argent-reel.md`). Facts-only, risk-first, tout se prouve sur le
> track-record avant d'être cru. Ce document est la source unique de vérité de la
> stratégie ; le code (`vc_predictions`, `safety_screen`, `ta_levels`, heartbeat) l'implémente.

---

## 1. Allocation : 85 / 15

- **85 % — VC, moyen/long terme.** Le cœur : projets on-chain analysés en profondeur,
  thèse tenue sur semaines/mois.
- **15 % — spéculation small-cap.** Tactique, court terme, sur des small-caps **qui
  passent le filtre de sécurité** (archétype $HOLO). Jamais du hype à l'aveugle :
  « même notre spéculation est disciplinée ».

Constante code : `vc_predictions.STRATEGY_ALLOCATION = {"vc": 0.85, "spec": 0.15}`.

## 2. Le pool entraînable : filtre → sélection honnête

1. **Classifier / filtrer les scams.** Chaque contrat candidat passe `safety_screen`
   (`skills/safety_screen.py`) : adresse valide + paire DEX + liquidité ≥ seuil +
   `security_score` ≥ 70 + verdict de scan `SAFE`. **« Passé le filtre » ≠ « 100 %
   sûr »** (un contrat propre peut rug plus tard — indétectable on-chain, on ne le
   prétend pas). Attrape : honeypot, mint/blacklist, ownership non renoncé, LP non
   locké, concentration, liquidité faible. N'attrape pas : rug futur, fraude off-chain.
2. **Constituer le pool** des contrats qui passent (persisté, rafraîchi périodiquement —
   un contrat propre aujourd'hui peut se dégrader).
3. **Tirer les 20 au sort** dans ce pool (loterie interne) → échantillon **non biaisé**
   (pas de cherry-pick) ET **tradeable** (a passé le filtre).

## 3. Boucle d'entraînement (walk-forward)

- **Lundi** : 20 tokens tirés du pool → analyse → **20 pronostics horodatés** (direction
  + niveaux entrée/invalidation/cible + horizon). Falsifiables.
- **Vendredi** : **résolution auto au prix OHLCV réel** + **rapport hebdo** (calibration
  de la semaine, ratés autopsiés, leçons apprises → recalibrage).
- **Multi-horizon** : la résolution hebdo juge surtout la **poche 15 % (1h/4h)**. Les
  verdicts **VC (85 %) restent ouverts** et sont photographiés à **1 sem / 1 mois /
  3 mois** (une thèse long terme ne se juge pas en 5 jours).
- **Garde-fou vitesse** : 20/sem accélère l'apprentissage, mais le pacte exige ~6 mois
  ET des **régimes de marché variés**. 4 semaines de bull ≠ preuve.

## 4. Régime de marché / sentiment → modulation d'exposition  🟠 *à valider*

Les événements internationaux et les émotions peuvent pousser le marché à l'inverse des
fondamentaux. ARIA intègre un **overlay de régime** (sentiment, Fear & Greed, macro —
tâche #14) qui module l'exposition.

**Reco (risk-first, cohérente avec « l'IA qui dit non ») :**
- Distinguer **tendance haussière saine** (exposée) de **euphorie extrême / blow-off top**
  (on **allège** — c'est au sommet que les krachs arrivent, pas le moment de charger).
- **Peur/panique** : très stricte sur la qualité, mais **commence à préparer les entrées**
  (les meilleures affaires VC s'y forment).
- 🟠 **Décision opérateur en attente** : risk-first (ci-dessus, recommandé) vs pro-cyclique
  pur (charger en euphorie). Codé comme **curseur ajustable** ; le track-record tranche.

## 5. Entrées / sorties : DCA branché sur les niveaux réels

- **Poche VC (85 %) → DCA.** Accumulation **en tranches sur les niveaux TA réels**
  (voûte 1 / `ta_levels`) : ex. 1/3 au support, on ajoute s'il tient, invalidation sous
  le support, sortie 1/3 à chaque résistance/cible. Réduit le risque de timing.
- **Poche spéculation (15 %) → tactique.** Entrée/sortie plus binaire sur le momentum.
- La règle DCA est **dérivée des niveaux**, jamais un objectif fabriqué ; le track-record
  dit si le DCA bat l'entrée sèche.

## 6. Monter la note (90 → 95) : brancher des outils, MESURÉS

L'architecture (`docs/architecture-extensibilite.md`) accepte de nouveaux outils sur des
seams `include_<x>` (fact-check, macro, radar X, comparables…). Règle honnête : un outil
ne monte la note **que s'il améliore la calibration PROUVÉE** sur le track-record. On
branche, on mesure, on garde les bons. La note se **gagne**, ne se décrète pas.

## 7. Exécution & garde-fous

- Aujourd'hui : **100 % suivi (paper)**, aucun argent réel, aucun wallet, rien à signer.
- Argent réel : seulement après le pacte (`docs/protocole-argent-reel.md`), petit et œil
  ouvert. **Signature finale toujours humaine (Tangem)** — ARIA autonome dans le cerveau,
  jamais sur le bouton qui déplace l'argent. Pas de clé chaude autonome (honeypot + dôme).
