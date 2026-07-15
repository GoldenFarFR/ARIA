[Session cloud]

# Divergence réelle GoPlus / Quick Intel sur "ownership renounced" — preuve concrète pour #192

Observation opérateur (capture DexScreener, paire VIRTUAL/WETH Base) : sur le
MÊME contrat, au même instant, deux services de sécurité réputés donnent une
réponse OPPOSÉE sur un fait binaire qui compte pour le verdict :

- **GoPlus Security** : "Ownership renounced: **Non**" (+ hidden owner: Oui,
  mintable: Oui, owner can change balance: Oui — 4 issues au total)
- **Quick Intel** : "Ownership renounced: **Oui**" (mais accord sur hidden
  owner: Oui — 1 issue)

Aucune des deux n'est objectivement "fausse" par construction — mais leur
désaccord sur un point dur (renonciation de propriété, qui conditionne
directement si le owner peut encore agir sur le contrat) est une preuve
directe que **s'appuyer sur une seule source de sécurité peut donner un
faux sentiment de certitude**. ARIA (`services/goplus.py`) ne consulte
aujourd'hui que GoPlus, jamais de second avis.

## Piste pour #192 (mandat permanent Research, atouts/points faibles IA)

Quick Intel a une vraie API (`developer.quickintel.io`), mais **payante**
(clé à acheter/provisionner) — contrairement à GoPlus (gratuit, déjà
intégré). Pas d'urgence à l'activer pour le test 1M$ actuel (paper-trading,
GoPlus reste le seul garde-fou décidé). Mais si le taux de faux-négatifs
honeypot de GoPlus seul s'avère un jour un vrai problème (post-mortem d'un
rug pull qui aurait dû être attrapé), un second avis payant type Quick
Intel devient un investissement justifié — chiffrage tarifaire pas encore
fait, à faire si ce jour arrive.

Rien codé, rien activé — diligence + preuve concrète seulement.
