[Session cloud — commandement]

# ClawHub / OpenClaw — vérification ponctuelle (pas une veille large-spectre)

## Contexte

L'opérateur a partagé deux liens (clawhub.ai, openclaw.ai) en pensant qu'il
s'agissait d'un lieu de discussion entre agents IA où ARIA pourrait avoir
« son propre profil ». Vérification directe (WebSearch, quelques requêtes
ciblées, pas un passage Research complet) plutôt que de supposer.

## Ce que c'est réellement

ClawHub n'est PAS un espace de discussion — c'est le magasin officiel de
« skills » (extensions/plugins) pour **OpenClaw**, un framework d'assistant
IA personnel open-source concurrent (~250k étoiles GitHub, ex-Clawdbot,
renommé janvier 2026). ClawHub fonctionne comme un « npm pour agents IA » :
des développeurs y publient des packages de compétences réutilisables,
installables dans un OpenClaw. Aucune notion de profil/conversation entre
agents — l'idée initiale de l'opérateur ne s'applique donc pas telle
quelle, mais la vérification a fait remonter des points réellement utiles.

## ⚠️ Alerte sécurité réelle sur ClawHub (catégorie finance/crypto) — à ne jamais oublier si le sujet revient

- The Register (29/04/2026) : « 30 ClawHub skills secretly turn AI agents
  into crypto swarm ».
- ClawHub a purgé **2 419 skills suspects**, dont **1 184 distribuaient un
  malware voleur de wallet** — déguisés en bots Polymarket, intégrations
  ByBit, outils « crypto wallet ».
- Straiker (recherche sécu) documente une chaîne d'attaque agent-à-agent
  construite sur ClawHub et propagée via un site tiers (« Moltbook »).

**Conclusion actée** : ne jamais installer un skill ClawHub, surtout dans
la catégorie crypto/wallet/finance — exactement la catégorie où le
malware a été trouvé, et le domaine où ARIA opère. Renforce la prudence
déjà en place sur tout code/paquet tiers touchant au capital.

## Idée d'architecture à considérer (jamais en installant leur package)

**Capability Evolver** (skill le plus téléchargé, 35k installs) : moteur
**déterministe, sans LLM**, qui analyse les logs runtime (fréquence,
timing, sévérité des erreurs) pour détecter des régressions, en
sub-100ms et sans coût de token. Complémentaire (pas un remplacement) de
ce qu'ARIA a déjà (`claude_mentor.py`/`knowledge_inbox.py`, qui utilisent
le LLM sur des données de performance réelles) — un détecteur
déterministe de régressions sur les logs serait gratuit et quasi-
instantané. Piste : coder un équivalent maison en Python si retenu,
**jamais importer/exécuter leur package** (cf. alerte sécurité ci-dessus).

## Comparaison heartbeat (pas une piste à copier, un choix déjà validé)

Le heartbeat OpenClaw lit un fichier `HEARTBEAT.md` en langage libre et
l'envoie comme un tour de conversation dans la MÊME session que le chat
normal — flexible mais moins auditable. ARIA (`heartbeat.py`) utilise des
tâches codées en Python, déterministes et auditables (vc_crawl, resolve,
etc.). Conclusion : notre choix de rigueur reste justifié pour un agent
qui touche (un jour) du capital réel — pas une piste à adopter, une
confirmation que l'architecture actuelle est le bon compromis.

## Branches ouvertes (banquées, pas creusées maintenant)

- **Multi-canaux** : OpenClaw route simultanément sur 20+ canaux
  (WhatsApp/Baileys, Discord, Slack/Bolt, Signal/signal-cli, SMS, Voice
  Call, Nostr, Matrix, Teams...) via un modèle « un canal = un plugin ».
  ARIA est aujourd'hui Telegram + widget site uniquement. Si l'opérateur
  souhaite un jour élargir la portée (WhatsApp au quotidien, Discord pour
  une communauté publique), ce modèle de routage centralisé est une
  référence d'architecture à étudier — chantier réel, pas un ajout rapide.
- **Capability Evolver maison** : si retenu, dimensionner un détecteur de
  régression déterministe sur les logs ARIA réels (quels signaux : taux
  d'échec LLM, latence, erreurs API répétées) avant de coder quoi que ce
  soit.

## Sources

- [The Register — 30 ClawHub skills secretly turn AI agents into crypto swarm](https://www.theregister.com/2026/04/29/30_clawhub_skills_mine_crypto/)
- [Straiker — Built on ClawHub, Spread on Moltbook: The New Agent-to-Agent Attack Chain](https://www.straiker.ai/blog/built-on-clawhub-spread-on-moltbook-the-new-agent-to-agent-attack-chain)
- [ClawDocx — Capability Evolver: The #1 OpenClaw Skill That Lets Your Agent Rewrite Itself](https://clawdocx.com/blog/capability-evolver-self-improving-openclaw-agent)
- [docs.openclaw.ai — Chat channels](https://docs.openclaw.ai/channels)
- [openclaw/openclaw (GitHub)](https://github.com/openclaw/openclaw)
- [OpenClaw Heartbeat Guide](https://claw.mobile/blog/openclaw-heartbeat-guide)
