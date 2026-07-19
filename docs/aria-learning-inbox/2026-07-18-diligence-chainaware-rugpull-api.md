[VPS Research]

# Diligence ChainAware.ai — alternative/complément à RugCheck, API réelle vérifiée en direct

## Contexte

Question opérateur (18/07) : autres outils du type RugCheck ? ChainAware avait
déjà été signalé comme piste intéressante dans la réponse précédente (angle
comportemental créateur/LP providers, absent de GoPlus). Diligence demandée
explicitement — méthode standard : vérifier la légitimité, tester l'API en
direct (pas une lecture de doc/marketing seule), chiffrer la couverture
réelle avant toute recommandation.

## Légitimité

- Société suisse (Zürich), **incubée par ChainGPT Labs** (projet Web3 établi,
  pas un shell vide).
- Membre du **AWS Global Fintech Accelerator** (signal de légitimité
  indépendant).
- Documentation API publique réelle et cohérente : `swagger.chainaware.ai`
  répond `200`, expose un vrai OpenAPI 2.0 (`ChainAware Enterprise API
  v1.0.2`), base URL `https://enterprise.api.chainaware.ai`.
- **Testé en direct** : appel réel sans clé sur `/rug/pull-check` →
  `403 {"message":"Forbidden"}` — confirme que l'API est vraiment en
  production et gatée (pas une doc morte).

## Ce que fait réellement `/rug/pull-check` (l'équivalent RugCheck)

Deux couches, vérifiées dans le schéma de réponse réel (pas la doc
marketing) :

1. **Couche statique** (`risk_indicators`) — **quasi identique champ pour
   champ au schéma GoPlus déjà utilisé par ARIA** (`is_honeypot`,
   `honeypot_with_same_creator`, `hidden_owner`, `buy_tax`/`sell_tax`,
   `lp_holders_locked`, `is_open_source`, `selfdestruct`...). Pour Base,
   cette couche n'apporte **aucun signal que GoPlus ne donne pas déjà**.
2. **Couche comportementale** (`probabilityFraud`, `status`,
   `liquidityEvent[].from_fraud_probability`/`from_fraud_status`,
   `forensic_details`) — c'est la vraie différenciation : relie le
   déployeur du contrat et **chaque fournisseur de liquidité individuel**
   à un historique de fraude **cross-chain**. Concrètement : si l'adresse
   qui a retiré la liquidité (ou fourni le LP initial) a un passif de
   fraude ailleurs sur une autre chaîne suivie par ChainAware, ça remonte
   dans le score — un signal que ni GoPlus ni un scan de contrat isolé ne
   peuvent produire (ils ne voient qu'UN contrat, jamais l'historique
   comportemental de ses acteurs).

## Couverture chaîne — écart réel trouvé entre marketing et API vécue

**Attention, deux sources internes à ChainAware se contredisent** — tranché
en lisant le schéma réel plutôt que la page produit :
- La page blog "Token Rank" annonce Ethereum/BSC/**Base**/**Solana**.
- Le endpoint `/rug/pull-check` réel (celui qui fait vraiment le travail de
  RugCheck) déclare dans son `enum` : **`ETH`, `BNB`, `BASE`, `HAQQ`
  uniquement — pas de Solana, pas de Robinhood.**

Conséquence directe pour ARIA : ChainAware couvrirait Base (utile), mais
**ne comble pas le vrai trou du pipeline momentum**, qui est justement
l'absence de couverture de sécurité fiable sur Solana pour les tokens tout
juste lancés (documenté le 17/07 dans CLAUDE.md — GoPlus renvoie souvent
`result` vide sur ces contrats frais). Sur Solana, ChainAware n'aide pas.

## Coût — partiellement vérifié seulement

- `/rug/pull-check` exige explicitement un abonnement **Business ou
  Enterprise** (marqué dans la doc du endpoint) — pas accessible en tier
  gratuit.
- **Montants non confirmés directement depuis chainaware.ai** : la page
  `/pricing` est une SPA JS, aucun chiffre n'apparaît dans le HTML brut
  (vérifié par `curl`) — impossible de la lire sans navigateur piloté.
  Un agrégateur tiers (SoftwareSuggest, pas ChainAware lui-même) avance
  399$/mois (Business) et 999$/mois (Enterprise) — **à traiter comme non
  confirmé**, pas comme un fait.

## Verdict

**Pas un remplaçant de GoPlus, un complément potentiel pour Base
uniquement**, et seulement si le signal "historique de fraude du
déployeur/des LP providers" a une vraie valeur ajoutée au-delà de ce que
`safety_screen`/`momentum_entry` font déjà. N'apporte rien sur Solana (le
chaînon le plus faible actuel), rien sur Robinhood. Coût réel non confirmé,
probablement 400-1000$/mois si le chiffre tiers est juste — à mettre en
balance avec la doctrine vitesse du pipeline #194 (chaque couche de
vérification ajoute de la latence) avant toute décision d'intégration.

**Recommandation** : banquer, ne pas brancher maintenant. Pas assez de
valeur ajoutée démontrée pour justifier coût + latence + complexité
d'intégration tant que Solana (le vrai angle mort) n'est pas couvert.

## Branches ouvertes

- Vérifier si ChainAware propose une couverture Solana sur un AUTRE
  endpoint que `/rug/pull-check` (ex. `/fraud/check`, `/fraud/audit`) —
  seul `/rug/pull-check` a été inspecté en détail ce passage, les 4 autres
  endpoints du swagger (`/fraud/check`, `/fraud/audit`,
  `/segmentation/wallet-segment`, `/users/credit-score`) n'ont pas été
  vérifiés chaîne par chaîne.
- Si un accès navigateur (`claude-in-chrome`) devient disponible, revérifier
  `chainaware.ai/pricing` pour des chiffres réels plutôt qu'une source
  tierce non confirmée.

## Addendum 19/07 (promotion veille Research — session commandement)

Deux frameworks académiques 2026 trouvés (LROO Rug Pull Detector, arXiv 2603.11324 ;
TM-RugPull, arXiv 2602.21529), pertinents SEULEMENT si ARIA construit un jour un
classifieur maison de rug-pull au-delà de GoPlus/ChainAware/RugCheck (pas le cas
aujourd'hui — cf. recommandation ci-dessus, banqué, rien branché). Point méthodologique
à retenir pour ce jour-là : les deux papiers insistent sur la **"leakage-resistance"
temporelle** — un piège classique est d'entraîner/tester un modèle avec des features
qui ont fuité des données POST-retrait-de-liquidité, gonflant artificiellement la
précision mesurée sans que ça marche en conditions réelles ; toutes les features
doivent être extraites STRICTEMENT avant tout retrait de liquidité. `safety_screen.py`/
`momentum_entry.py` n'ont pas ce problème aujourd'hui (ils tournent déjà en temps réel
pré-décision), mais un futur modèle entraîné sur l'historique devrait suivre cette
discipline dès sa conception plutôt que la découvrir après coup. Rien à construire
maintenant — noté pour la prochaine fois que ce sujet est repris.
