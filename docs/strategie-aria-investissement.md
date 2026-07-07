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

## 1 bis. La thèse VC d'ARIA : QUI on cherche  ⭐

> **De vrais bâtisseurs sur Base, sous le radar, qui ont tokenisé et créé un produit
> à gros potentiel.** (Thèse opérateur, gravée.)

C'est la cible de la poche 85 %. Quatre critères cumulatifs :
1. **Vrai bâtisseur** — une équipe qui construit réellement (produit vivant, commits,
   itérations), pas du vaporware ni un pump. L'**anonymat n'est PAS disqualifiant**
   (beaucoup d'excellents builders Base sont anonymes « comme moi ») — MAIS il doit être
   **compensé par une preuve produit/traction** (un anon sans produit = scam ; un anon
   avec un vrai produit + historique on-chain propre = la cible).
2. **Sur Base** — cohérent avec le Base-only du lancement.
3. **Sous le radar** — encore **peu découvert** : petite capitalisation, faible bruit
   social, jeune. C'est là qu'est l'alpha (entrer AVANT la foule). Un token déjà pumpé
   n'est PAS la cible.
4. **Tokenisé + produit à gros potentiel** — il y a un token investissable ET un produit
   réel avec un marché adressable crédible.

**Conséquence sur le sourcing** (le filtre `safety_screen` retire les scams ; cette thèse
ajoute une **sélection POSITIVE**) : on priorise les tokens **jeunes + faible cap + faible
attention sociale + preuve de produit** (activité GitHub, app live, utilisateurs, revenus).
Le radar (#7) et le moteur de connaissance (#8) alimentent cette détection « pépite cachée ».

**Identité & méthode — chasseuse de performance dans l'ombre on-chain.** ARIA n'est pas
qu'un gardien prudent : c'est une **chasseuse d'asymétrie** qui, quand les FAITS l'alignent,
tranche avec **conviction** (BUY franc + taille) — jamais tiède par défaut, jamais forcé.
Et sa conviction est légitime parce que son analyse est **100 % on-chain, la face cachée**
que les humains peinent à lire (distribution des holders, historique du déployeur, flux
smart-money, entrailles du contrat, mécanique de liquidité) : des **chiffres indiscutables**,
pas des vibes. Là où l'humain voit le prix et le narratif, ARIA voit la donnée dure, 24/7.
**Garde-fou (partie de l'edge)** : l'on-chain montre ce qui s'est passé on-chain, PAS les
intentions off-chain / le juridique / tout le produit réel → indiscutable sur les faits
on-chain, **« donnée insuffisante » assumé** sur le reste. Elle ne prétend jamais que la
chaîne lui dit tout.

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

### 2 bis. Tokens en bonding (pré-graduation) — chemin PARALLÈLE

⚠️ **Angle mort à ne pas oublier.** Les tokens encore sur leur **courbe de bonding**
(Virtuals et autres launchpads, avant « graduation » vers un DEX) **n'ont pas de paire
DexScreener** — donc le pipeline ci-dessus (scan DEX → `best_pair` → OHLCV du pool) les
**exclut totalement**. Or c'est la niche la plus early (poche 15 %).

Ils exigent un **chemin dédié** (tâche #10) :
- **Source distincte** : `services/virtuals.py` (déjà présent) → seam `include_virtuals`,
  pas DexScreener. On lit l'état de la **courbe** (prix = f(offre), progression vers la
  graduation, réserve), pas un pool DEX.
- **Filtre de sécurité adapté** : les barrières changent (la « liquidité » = la réserve
  de la courbe ; les holders sont sur le contrat launchpad ; la mécanique de graduation
  remplace le LP-lock). `safety_screen` DEX ne s'applique pas tel quel → filtre bonding
  dédié.
- **Résolution/OHLCV** : la courbe n'a pas d'OHLCV standard → on suit l'état de la courbe
  et l'événement de graduation.
- **Poche & risque** : bonding = **le plus risqué** (pré-graduation, très rug-prone) →
  strictement dans les 15 %, taille minime, tracké **séparément** du VC gradué.

Séquencement : le pipeline DEX (tokens gradués) est la **v1 (lundi)**. Le chemin bonding
est une **2ᵉ source** qu'on branche ensuite sur son seam — jamais forcé dans le pipeline DEX.

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

## 6 bis. Objectifs de rendement — échelle par palier d'expérience

Objectifs **par palier**, débloqués au fur et à mesure qu'ARIA **prouve** le palier
précédent (lié au pacte `docs/protocole-argent-reel.md`) :

| Palier | Profil de risque | Objectif indicatif / an | Débloqué quand |
|---|---|---|---|
| 0 (départ) | prudent, capital protégé d'abord | viser **~x1,5** (+50 %) | dès le départ (en suivi) |
| 1 | risque supérieur assumé | viser **~x2** | le palier 0 est prouvé sur le track-record |
| 2+ | plus agressif | au-delà | palier précédent prouvé, régimes variés |

**⚠️ Cadrage honnête, non négociable — le mot « certain » n'a pas sa place.**
- **Aucun rendement crypto n'est garanti ni certain.** « +50 %/an safe et certain »
  **n'existe pas** — quiconque le promet fait un Ponzi (c'est LE pitch d'arnaque). ARIA,
  « l'IA qui dit non à la fausse certitude », ne peut pas se le promettre à elle-même.
- Ce sont des **OBJECTIFS** (viser), pas des promesses. Certaines années : +200 % ;
  d'autres : −30 %. Le risque de perte reste réel à chaque palier.
- **« Safe » = la discipline** (filtre sécurité, R/R ≥ 3:1, protection du capital,
  éviter les −100 %), qui **maximise les chances** d'atteindre l'objectif et **minimise
  la ruine** — pas l'absence de risque. Il n'y a **pas** de +50 % sans risque (le sans-
  risque, c'est ~4-5 %/an). Ne pas perdre est 80 % du travail : survivre pour composer.
- L'échelle monte avec l'**expérience PROUVÉE** d'ARIA, jamais avec l'espoir. C'est ce
  qui transforme « j'aimerais que ce soit certain » en « c'est démontré ».

## 7. Exécution & garde-fous

- Aujourd'hui : **100 % suivi (paper)**, aucun argent réel, aucun wallet, rien à signer.
- Argent réel : seulement après le pacte (`docs/protocole-argent-reel.md`), petit et œil
  ouvert. **Signature finale toujours humaine (Tangem)** — ARIA autonome dans le cerveau,
  jamais sur le bouton qui déplace l'argent. Pas de clé chaude autonome (honeypot + dôme).
