# [VPS Research] Vide juridique sur la responsabilité des agents IA de trading — précédent Coinbase Advisor

## Contexte et périmètre

Veille juridique pure, pas une proposition de structure. Pertinent pour
`docs/conformite-dossier-avocat.md` (dossier à valider par un avocat avant
tout encaissement réel — règle absolue déjà en place dans CLAUDE.md) : ce
fichier banque un précédent concret et chiffré à verser à ce dossier le
moment venu, il ne tranche rien lui-même. Aucun code, aucune structure
juridique proposée ici.

## Le vide confirmé (vérifié WebSearch, sources officielles/cabinets d'avocats)

Le 17/03/2026, la SEC et la CFTC ont publié une interprétation conjointe
classifiant les crypto-actifs en 5 catégories (digital commodities /
collectibles / tools / stablecoins / securities). **Cette classification ne
mentionne les agents IA nulle part** — elle porte sur la nature de l'actif
échangé, jamais sur qui (humain, bot, IA autonome) prend la décision de
trade. Des élus démocrates ont formellement demandé à la SEC de clarifier
les obligations respectives plateforme/développeur/détenteur du compte
quand une IA décide — question encore ouverte au moment de cette veille.

## Le précédent Coinbase Advisor (vérifié réel, 16/06/2026)

Coinbase a lancé le premier agent IA en application à porter simultanément
trois enregistrements réglementaires américains : Registered Investment
Adviser (SEC), Commodity Trading Adviser (CFTC + NFA). Sous l'Investment
Advisers Act de 1940, un RIA est un fiduciaire — l'enregistrement lie donc
légalement l'agent IA à des obligations fiduciaires à chaque recommandation
qu'il génère.

**La tension non résolue** : le propre disclaimer de Coinbase Advisor fait
porter TOUTES les pertes d'investissement sur l'utilisateur final — une
tension fiduciaire (obligation légale d'agir dans l'intérêt du client d'un
côté, transfert contractuel de toute perte de l'autre) qu'aucun tribunal
n'a encore tranchée. Point clé pour ARIA : « le bot l'a fait » n'est pas
reconnu comme défense légale — les tribunaux traitent déjà les systèmes IA
comme des produits engageant la responsabilité de leur concepteur, pas
comme des tiers autonomes déresponsabilisant l'opérateur.

## Potentiel concret pour ARIA

Précédent direct et chiffré à verser au dossier avocat déjà prévu avant
tout encaissement réel. Même un acteur déjà enregistré SEC/CFTC/NFA — donc
avec des moyens juridiques largement supérieurs à ARIA — n'a pas résolu la
question de qui porte la perte quand l'IA décide. Point à anticiper
explicitement dans la structure juridique d'ARIA plutôt qu'à découvrir
après coup, d'autant plus qu'ARIA n'a aujourd'hui aucun enregistrement RIA/
CTA (hors sujet tant que le capital reste paper + pilote 10-15$, mais la
question resurgira au moment de toute vraie mise à l'échelle réelle — cf.
le rappel déjà existant "Réévaluer la publication des paramètres exacts
avant tout mouvement vers du capital réel au-delà du pilote 10-15$",
CLAUDE.md, section test paper-trading 1M$).

## Branches ouvertes (banquées, pas creusées)

- Suivre si la SEC répond formellement à la demande de clarification des
  élus démocrates sur les obligations respectives plateforme/développeur/
  détenteur — pas de réponse connue au moment de cette veille.
- Comparer la structure Coinbase Advisor (RIA fiduciaire + disclaimer de
  transfert de perte) à d'autres précédents d'agents IA financiers
  enregistrés, s'il y en a d'autres, pour voir si le marché converge vers
  un modèle de disclaimer standard ou si des variantes existent.

## Sources

- [SEC and CFTC issue joint interpretation on crypto asset regulation — Norton Rose Fulbright](https://www.nortonrosefulbright.com/en/knowledge/publications/a88b661b/sec-and-cftc-release-joint-interpretation-on-crypto-asset-regulation)
- [Coinbase AI Trading Agent Is Now SEC-Registered: But You Still Bear the Risk — Tech Times](https://www.techtimes.com/articles/318538/20260617/coinbase-ai-trading-agent-now-sec-registered-you-still-bear-risk.htm)
- [SEC and CFTC Issue Landmark Joint Interpretation on Crypto Asset Classification — Jenner & Block](https://www.jenner.com/en/news-insights/client-alerts/sec-and-cftc-issue-landmark-joint-interpretation-on-crypto-asset-classification)
- [SEC and CFTC Clarify When Digital Assets Are—and Are Not—Securities — Ballard Spahr](https://www.ballardspahr.com/insights/alerts-and-articles/2026/03/sec-and-cftc-clarify-when-digital-assets-are-and-are-not-securities)
