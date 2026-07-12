[VPS Research]

# Calibrer le juge ARIA sans attendre le volume — recherche redirigée (2026-07-11)

Contexte : redirection opérateur sur la piste "calibration du juge" (voir
`2026-07-11-scan-large-spectre.md`, entrée 12). Diagnostic opérateur confirmé
par lecture du code : `real_money_readiness.py::_check_calibration` retourne
`unknown` tant qu'il y a moins de 2 buckets de calibration ou pas de
`hit_rate` — la vraie cause est un **manque de volume de pronostics `/vc`
clôturés**, pas un manque d'outil de test. DeepEval/Promptfoo testent la
cohérence d'un juge (répond-il pareil à la même entrée, respecte-t-il un
format), **pas sa justesse contre la réalité** — mauvais outil pour ce
problème précis. Nouvelle question : comment calibrer avec PEU de données
réelles, sans attendre passivement.

**Vérifié avant de chercher** : `qi_judge_calibration.py` calcule déjà un
score de Brier (`compute_calibration_stats`) — donc ARIA a déjà une règle de
scoring propre (fréquentiste), pas seulement un hit-rate brut. Aucun code
`bayes`/`beta`/`posterior`/`credible` trouvé dans le repo — pas de doublon
sur l'angle bayésien ci-dessous. `services/ohlcv.py` existe déjà et est
réutilisé tel quel par `pump_dump_autopsy.py` pour relire une série de prix
réelle sur un pronostic clôturé — l'infrastructure de lecture OHLCV
historique existe déjà, ce n'est pas à construire.

---

## 1. Backtesting historique — légitime, méthodologie standard, MAIS un piège spécifique aux juges LLM à ne pas rater

**Légitimité.** Le backtesting / walk-forward validation est une
méthodologie standard et bien documentée en finance quantitative : découper
l'historique en fenêtres, valider hors-échantillon, éviter le surajustement.
Ce n'est pas un outil à évaluer, c'est une pratique — l'angle
légitimité/financement ne s'applique pas.

**Le vrai risque, spécifique à un juge qui est un LLM (pas un modèle
statistique classique) : la fuite de connaissance par les données
d'entraînement.** Un backtest quantitatif classique fonctionne parce que le
modèle testé n'a jamais "vu" le futur. Un LLM, lui, **a potentiellement déjà
vu l'issue réelle d'un token connu dans son corpus d'entraînement** (un token
qui a fait un x50 ou un rug pull célèbre est probablement mentionné dans des
threads/articles indexés par le modèle) — si on lui demande de juger ce
token rétroactivement, un bon verdict peut refléter une mémorisation de
l'issue plutôt qu'un vrai raisonnement sur les données on-chain fournies.
**C'est le point le plus important de cette note** : un backtest LLM naïf
sur des tokens déjà célèbres surestimerait la qualité du juge de façon
invisible. Ce que je n'ai pas trouvé dans la littérature généraliste
(sources consultées : walk-forward optimization/quant finance) car c'est
propre aux juges LLM, pas aux modèles statistiques — à traiter comme un
raisonnement de première main, pas une citation externe.

**Comment le neutraliser (proposition, pas une conclusion) :**
- Choisir des tokens obscurs/de faible profil (peu de mentions indexables),
  pas les cas emblématiques qui viennent spontanément à l'esprit.
- Anonymiser ce qui est transmis au juge (retirer nom/ticker/date, ne garder
  que les faits on-chain + OHLCV bruts) — dans la mesure où `vc_judge`
  fonctionne déjà sur des faits structurés plutôt que sur du texte narratif
  (cohérent avec le dôme), c'est probablement faisable sans réécrire le juge.
- Ne jamais compter un backtest comme équivalent à un pronostic forward pour
  la case `sample_size` du pacte "argent réel" (n≥80 sur 180 jours) — cette
  case semble volontairement exiger du **forward réel**, précisément pour
  empêcher ce genre de biais rétrospectif de compter comme preuve. Un
  backtest est un signal complémentaire, pas un substitut à ce seuil déjà
  engagé.

**Seam.** Réutiliser `services/ohlcv.py` (déjà câblé) pour tirer l'historique
de tokens antérieurs à la mise en production du juge ; réutiliser le motif
déterministe de `pump_dump_autopsy.py` (détection sans LLM du pattern réel
sur la série de prix) comme "vérité terrain" à comparer au verdict rétroactif
du juge. Pas de nouveau service.

**Upside concret.** Transforme "attendre 6 mois de forward" en "obtenir un
premier signal de calibration cette semaine" — mais un signal **à traiter
comme indicatif seulement**, jamais comme substitut au seuil du pacte, et
seulement si le biais de mémorisation est activement neutralisé (anonymisation).

---

## 2. Calibration bayésienne à petit échantillon — méthode standard, s'insère directement dans le code existant

**Légitimité.** Le modèle Beta-Binomial (prior Beta conjugué à une
vraisemblance binomiale) est la méthode standard, enseignée, pour estimer un
taux de succès avec un intervalle de crédibilité honnête **même avec très
peu de points** — contrairement à un test fréquentiste qui exige un n
minimal avant de dire quoi que ce soit. Littérature confirme : la borne se
resserre progressivement avec n croissant, mais donne un signal dès les
premières observations (juste plus incertain, jamais faux). Ce n'est pas un
outil/projet à adopter, c'est une formule fermée (quelques lignes de calcul).

**Où ça s'accroche, précisément.** `real_money_readiness.py::_check_calibration`
fait aujourd'hui un choix binaire : `len(calib) < 2 or hit is None` →
`unknown`, sinon `ok`/`fail` sur un `hit > 0.5` brut. Un remplacement
Beta-Binomial donnerait, à la place d'un `unknown` plat : un intervalle de
crédibilité sur le vrai taux de succès (ex. "avec 12 BUY clôturés, hit-rate
observé 58%, mais intervalle de crédibilité à 90% = [31%, 81%] — trop large
pour trancher") — **une information honnête et graduelle**, jamais un signal
gonflé, cohérente avec la doctrine "ne jamais transformer un `unknown` en
`ok` par optimisme" puisque la méthode elle-même refuse de donner un faux
sentiment de certitude à petit n (l'intervalle reste large tant que n est
petit — il ne peut pas mentir par construction).

**Distinction importante à ne pas brouiller** : ceci améliorerait la
*qualité de l'information* affichée pendant l'attente, pas le seuil
`REQUIRED_SAMPLE_SIZE = 80` lui-même — ce seuil a l'air d'être un engagement
de politique/pacte pré-signé ("8 cases pré-engagées"), pas seulement une
nécessité statistique. Un signal bayésien à n=12 reste un signal à haute
incertitude, honnêtement montré comme tel — il ne dit pas "80 n'est plus
nécessaire", il dit juste "voici ce qu'on peut honnêtement affirmer
aujourd'hui, et voici à quel point c'est encore incertain".

**Seam.** `real_money_readiness.py::_check_calibration` (calcul remplacé/
complété) ; consomme les mêmes données que `qi_judge_calibration.py`
(Brier déjà calculé) — **aucun nouveau service, aucune nouvelle dépendance
externe**, juste une fonction statistique supplémentaire (`scipy.stats.beta`
ou calcul fermé à la main, les deux triviaux).

**Upside concret.** Le seul gain de cette section qui ne dépend d'AUCUNE
donnée supplémentaire ni d'aucun risque méthodologique (contrairement au
backtest) — applicable dès aujourd'hui sur les données déjà en base, même
avec les quelques pronostics clôturés existants. Priorité haute :
faible effort, zéro risque de faux signal, comble un vrai manque d'affichage
honnête pendant la période d'attente.

---

## 3. Où DeepEval/Promptfoo s'insèrent (rôle réévalué, downgradé)

Confirmé après cette recherche : ces outils **ne s'insèrent PAS** dans le
problème de calibration lui-même. Leur rôle, s'il y en a un, serait
uniquement celui d'un **harnais d'exécution** pour lancer en lot le juge sur
un ensemble de cas (ex. les tokens du backtest de la section 1) et collecter
les sorties de façon reproductible — mais toute la partie qui compte
(sélection anti-biais des cas, neutralisation de la fuite mémorisée,
statistique Beta-Binomial, respect du seuil du pacte) est un travail
statistique/méthodologique sur mesure, **pas une fonctionnalité que ces
frameworks fournissent**. Verdict : pas prioritaire, éventuellement utile
comme plomberie d'exécution si le volume de cas de backtest devient grand,
jamais comme "la solution".

---

## Verdict de cette note

Priorité recommandée, dans l'ordre : **(2) Bayésien petit-échantillon
d'abord** (gratuit, zéro risque, applicable immédiatement sur les données
déjà en base) → **(1) backtesting historique ensuite** (gain réel mais exige
une discipline anti-biais de mémorisation à concevoir avant de lancer quoi
que ce soit, et ne remplace jamais le seuil du pacte) → **(3) DeepEval/
Promptfoo en dernier**, seulement si (1) prend de l'ampleur et a besoin d'un
harnais de lot. Rien décidé ni implémenté ici — pistes banquées pour
arbitrage opérateur.
