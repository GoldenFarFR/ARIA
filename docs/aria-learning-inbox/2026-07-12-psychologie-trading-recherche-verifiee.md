[VPS Research]

# Psychologie/discipline comportementale des traders qui durent — recherche vérifiée, biais du survivant écarté

Angle explicitement retenu par l'opérateur : finance comportementale
**vérifiée** (études comparatives, méta-analyses), pas des portraits de
figures célèbres (biais du survivant — étudier seulement des gagnants ne
prouve rien sur la cause de leur réussite). Chaque trait ci-dessous est
sourcé par une étude qui **compare** deux populations, pas par une
description d'habitudes de gagnants.

## Méthode appliquée contre le biais du survivant

Pour chaque trait retenu : une étude longitudinale à groupe de comparaison
réel (comptes de courtage, cohortes), une méta-analyse, ou une expérience
contrôlée — jamais une source qui décrit uniquement des gagnants sans
comparaison. Rejeté explicitement : tout contenu de type "voici ce que
font les traders qui réussissent" sans groupe témoin.

---

## Trait 1 — Disposition effect (garder les perdants, vendre les gagnants trop tôt)

**Preuve.** Méta-analyse (Cheung, SSRN) : sous conditions de base, la
littérature trouve que les investisseurs réalisent 10 % de plus
d'opportunités de vendre des gagnants que des perdants, alors que le choix
optimal dicte l'inverse. **Étude comparative réelle avec P&L observé** :
analyse de comptes de courtage de 10 000 foyers (1987-1993) — le biais est
présent et **cause des rendements inférieurs de 4,4 % par an**. Répliqué
dans plusieurs pays (Chine, Finlande, Israël, Afrique du Sud, Taïwan,
Tunisie) — pas un artefact culturel isolé. — [Meta-analysis (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4746969), [Rational disposition effects (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S0378426623000821)

**Déjà codé chez ARIA ?** Partiellement — `paper_trader.py::TRAIL_STOP_PCT`
(stop suiveur qui "ne se relâche jamais", 15 % sous le plus haut) applique
mécaniquement une contre-mesure, **mais uniquement dans la boucle de
simulation autonome** (`paper_trader.py` — aucune exécution réelle,
confirmé en lisant le fichier). **Gap réel identifié** : l'analyse `/vc`
human-facing (`vc_analysis.py`) n'inclut aujourd'hui aucune formulation
qui alerte explicitement l'opérateur humain sur ce biais spécifique quand
il lit une position en perte — le garde-fou mécanique existe côté
simulation, pas côté texte lu par un humain qui décide lui-même.

**Où/comment encoder** : ajout au prompt de raisonnement (`vc_analysis.py::_SYSTEM_PROMPT`)
— une règle de style qui demande explicitement de nommer le risque de
disposition effect quand l'analyse porte sur une position déjà en perte
(pas un nouveau mécanisme de code, le calcul n'est pas requis ici).

## Trait 2 — Évaluer le processus au moment T, pas le résultat a posteriori (outcome bias)

**Preuve.** Baron & Hershey (1988), expérience contrôlée : des décisions
médicales identiques sont notées plus favorablement quand elles ont eu un
résultat positif que négatif, **alors que la décision elle-même est
identique** — c'est la définition opérationnelle du biais. **Réplication
récente confirmée** : le biais persiste même chez des participants qui
déclarent explicitement que le résultat ne devrait pas compter dans
l'évaluation — robustesse forte. — [Baron & Hershey original](https://www.researchgate.net/publication/19789598_Outcome_Bias_in_Decision_Evaluation), [Réplication 2024](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12372742/)

**Déjà codé chez ARIA ?** Oui, vérifié en détail — **pas un gap**.
`skills/vc_judge.py::judge_analysis` audite l'analyse VC **contre les
faits on-chain bruts au moment de la décision** (claims non étayés,
cohérence risque/récompense) — jamais contre un résultat de marché
ultérieur. `investment_memory.py` sépare structurellement la thèse
(`open`, au moment T) du résultat (`closed`, transition atomique
irréversible) — le système est déjà construit pour juger le raisonnement,
pas le P&L final. Confirmation, pas une piste à construire.

## Trait 3 — Vitesse de révision de thèse face à une info contraire

**Preuve.** Littérature sur la mise à jour de croyances sous contrainte
cognitive : sur-réaction en environnement complexe/signaux bruités,
**sous-réaction en environnement simple avec signaux précis/confirmatoires**
— point pertinent ici, un token qui semblait "clair" au départ est
justement le cas où la sous-réaction à une info contraire est la plus
probable selon ce modèle. Confirmation biais de confirmation : étude sur
600+ participants, ~85 % disposés à accepter les opinions confirmantes. — [Over/underreaction — belief updating (Ba)](https://cuiminba.com/working-papers/overreaction/), [Confirmation bias trading](https://www.preprints.org/manuscript/202510.1686/v1/download)

**Point de rigueur** : preuve plus théorique/modélisée que les traits 1 et
2 — pas de méta-analyse comparative "traders qui révisent vite vs lentement,
P&L observé" trouvée aussi directement que pour le disposition effect. À
traiter comme un trait bien fondé théoriquement, moins tranché
empiriquement en performance directe — honnêteté demandée par la méthode.

**Déjà codé chez ARIA ? Non — vrai gap, vérifié.** `investment_memory.py`
n'a que deux états : `open` → `closed`, transition unique et atomique
(`close_thesis`, "on ne réécrit jamais l'historique"). **Aucun mécanisme
ne re-vérifie une thèse ouverte contre de nouvelles données** — un token
peut rester en `open` indéfiniment sans jamais être confronté à un
changement de fait (ex. `security_score` qui chute, dev qui commence à
vendre) tant que `close_thesis` n'est pas appelé manuellement/par un autre
flux.

**Où/comment encoder** : ici un vrai calcul est nécessaire (comparer l'état
au moment T0 de la thèse à l'état actuel), donc pas seulement un prompt —
proposition : nouvelle tâche périodique (réutiliser le patron
`HeartbeatTask` de `heartbeat.py`, gated OFF par défaut comme les autres
tâches sensibles) qui relit `list_open_theses()`, relance un scan léger
sur chaque token concerné, et si un signal clé s'est inversé (ex.
`security_score` chute significative, `dev_signal` passe négatif),
injecte une alerte "thèse potentiellement obsolète" dans le contexte
lu par l'opérateur (Telegram) — jamais de clôture automatique de thèse,
juste un signal qui accélère la révision humaine.

## Trait 4 — Taille de position liée au risque de ruine (Kelly)

**Preuve.** MacLean, Thorp & Ziemba (2011, *The Kelly Capital Growth
Investment Criterion*) : une erreur d'estimation de seulement 10 % sur le
rendement espéré peut conduire à parier 50 % de plus que l'optimal en
Kelly plein. Asymétrie documentée : sur-parier est **beaucoup plus
dangereux** que sous-parier (parier 2x l'optimal détruit la croissance
bien plus qu'en parier 0,5x). **Demi-Kelly (0,5×) confirmé comme le choix
le plus répandu en pratique** — "dramatically smoother equity curve,
strong long-run growth" — pas un compromis arbitraire mais un consensus
documenté. — [Kelly Criterion practical guide](https://astuteinvestorscalculus.com/kelly-criterion-position-sizing/), [Dangers of Full Kelly](https://medium.com/@tmapendembe_28659/the-dangers-of-full-kelly-criterion-why-most-traders-should-use-fractional-kelly-criterion-instead-0338e3bcc705)

**Déjà codé chez ARIA ? Oui — confirmation, pas un gap.**
`onchain/sepolia_autonomous.py::KELLY_SAFETY_FACTOR = 0.5` (demi-Kelly,
plafonné, calculé sur calibration réelle via `vc_predictions.metrics`,
jamais sur un montant de test). **Le choix de 0,5 est exactement ce que
la littérature identifie comme le compromis le plus robuste** — aucune
recommandation de changement, juste une validation externe indépendante
d'un choix déjà fait.

---

## Synthèse

| Trait | Preuve trouvée | Statut ARIA |
|---|---|---|
| Disposition effect | Forte (méta-analyse + étude comparative 10k foyers, 4,4%/an) | Partiel — mécanique en simulation, gap côté texte humain |
| Processus vs résultat | Forte (expérience contrôlée + réplication) | **Déjà bien fait** — confirmation |
| Vitesse de révision de thèse | Moyenne (théorique solide, moins de preuve comparative directe) | **Vrai gap** — aucun mécanisme de re-vérification |
| Kelly / risque de ruine | Forte (formel + consensus pratique) | **Déjà bien fait** — 0,5 validé par la littérature |

Deux gaps réels retenus : (1) prompt-level pour le disposition effect côté
texte humain (`vc_analysis.py`), effort faible ; (2) tâche périodique de
re-vérification de thèse (nouveau, réutilise `heartbeat.py`), le seul des
quatre traits qui exige un vrai calcul plutôt qu'une formulation de
prompt — cohérent avec la contrainte posée par l'opérateur en amont.

## Sources

- [Meta-analysis of disposition effect experiments (Cheung, SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4746969)
- [Rational disposition effects: Theory and evidence (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S0378426623000821)
- [Outcome Bias in Decision Evaluation (Baron & Hershey, original)](https://www.researchgate.net/publication/19789598_Outcome_Bias_in_Decision_Evaluation)
- [Replication of Baron & Hershey outcome bias experiment](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12372742/)
- [Over- and Underreaction to Information: Belief Updating with Cognitive Constraints](https://cuiminba.com/working-papers/overreaction/)
- [Overconfidence and Confirmation Bias in Trading](https://www.preprints.org/manuscript/202510.1686/v1/download)
- [Kelly Criterion position sizing — overbetting risk](https://astuteinvestorscalculus.com/kelly-criterion-position-sizing/)
- [Dangers of Full Kelly Criterion](https://medium.com/@tmapendembe_28659/the-dangers-of-full-kelly-criterion-why-most-traders-should-use-fractional-kelly-criterion-instead-0338e3bcc705)
- [Do Professional Traders Exhibit Myopic Loss Aversion? (Haigh & List)](https://www.researchgate.net/publication/4769445_Do_Professional_Traders_Exhibit_Myopic_Loss_Aversion_An_Experimental_Analysis)
- Code ARIA vérifié : `paper_trader.py` (`TRAIL_STOP_PCT`), `skills/vc_judge.py` (`judge_analysis`, `_validate_judge_output`), `investment_memory.py` (cycle thèse), `onchain/sepolia_autonomous.py` (`kelly_fraction`, `KELLY_SAFETY_FACTOR`)
