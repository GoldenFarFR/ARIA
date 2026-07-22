# HANDOFF — Coinbase CDP (agent wallet, capital réel)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.
> Capital réel — toujours vérifier l'état actuel (`/agentwallet`,
> `agent_wallet_log.list_transactions()`) avant de supposer quoi que ce soit au-delà de la
> date de ce fichier.

[CONFIG] Sujet    : Wallet CDP financé
Date : 2026.07.16  /  Probleme : —
Solution : 1 USDC + ETH gas. Découvert au passage : le compte CDP standard n'a pas d'intégration Paymaster/Smart Account, chaque swap/transfert consomme du vrai ETH

------------------------------------------------------------

[DEPLOYE] Sujet    : Pilote agent-wallet activé en prod
Date : 2026.07.18  /  Probleme : —
Solution : boucle de décision autonome câblée (réutilise le pipeline momentum), sizing 3% du solde réel plafonné 15$ — agent_wallet_pilot.py (0d3f5933)

------------------------------------------------------------

[DEPLOYE] Sujet    : Swap réel bloqué à répétition
Date : 2026.07.19→21  /  Probleme : 5 tentatives de swap, toutes échouées (erreur Pydantic gasFee) — bug du SDK CDP officiel, pas de notre code
Solution : tx_hash toujours vide, aucune perte de fonds (fail-closed) ; cooldown structurel 7 jours pour ne pas boucler sur un token cassé — agent_wallet_pilot_cycle.py (b00d108c)

------------------------------------------------------------

[CONFIG] Sujet    : Authentification CDP rejetée (401)
Date : 2026.07.21  /  Probleme : allowlist IP côté CDP restreinte à l'ancienne IP, après migration VPS
Solution : nouvelle IP ajoutée à l'allowlist — config CDP dashboard, pas de commit

------------------------------------------------------------

[CONFIG] Sujet    : Export de clé privée réalisé proprement
Date : 2026.07.21  /  Probleme : besoin d'exporter la clé privée du wallet pour un usage ponctuel
Solution : via une clé API TEMPORAIRE séparée (scope Export uniquement), jamais la clé de prod ; supprimée après usage ; clé privée du wallet jamais vue par Claude Code — config CDP dashboard, pas de commit

------------------------------------------------------------

[DEPLOYE] Sujet    : Wallet mal résolu — solde affiché à 0$
Date : 2026.07.22  /  Probleme : /agentwallet affichait 0 USDC alors que le vrai solde on-chain était 12,80$ — get_or_create_account(name=...) résolvait vers un 2e compte CDP vide, créé par erreur le 21/07 lors de la régénération de clé API
Solution : WALLET_NAME corrigé vers le nom réel du wallet historique ("aria-wallet") ; vérifié empiriquement avant de committer ; aucune perte de fonds à aucun moment — agent_wallet_cdp_adapter.py (6cddf739)

------------------------------------------------------------

[DEPLOYE] Sujet    : Nom de wallet dupliqué dans un 2e fichier
Date : 2026.07.22  /  Probleme : x402_cdp_signer.py avait sa propre copie de WALLET_NAME, jamais synchronisée avec le fix ci-dessus
Solution : import depuis agent_wallet_cdp_adapter.py (source unique) — ne peut plus se désynchroniser — x402_cdp_signer.py (6cddf739)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : État du wallet
Date : 2026.07.22  /  Probleme : —
Solution : 0xF046...37Ef ("aria-wallet"), ~12,80 USDC + 0,0166 ETH. Second wallet 0x584b...57Ef gardé comme secours, jamais utilisé par le code

------------------------------------------------------------

[CODE] Sujet    : Migration vers Smart Account CDP (Spend Permissions)
Date : 2026.07.22  /  Probleme : plafond de dépense actuel existe seulement en Python, aucune protection au niveau du contrat lui-même
Solution : direction actée par l'opérateur, RIEN construit — explicitement différé à une session dédiée (pas enchaîné juste après l'incident ci-dessus)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Coinbase Agentic Wallets retenu face à MetaMask Agent Wallet
Date : 2026.07.14 vers 15  /  Probleme : choisir un produit d'agent-wallet pour le futur pilote capital réel (~10-15$) — MetaMask Agent Wallet nommément compatible Claude Code mais accès anticipé jamais ouvert.
Solution : Coinbase Agentic Wallets retenu (gratuit à créer, testable dès ~20$ USDC, MCP officiel compatible Claude, x402 natif, accessible immédiatement) — constat commun aux 4 options comparées (MetaMask/Coinbase/Trust Wallet/Cobo) : toutes fonctionnent sur plafond + liste blanche accordés une fois, jamais une confirmation humaine par transaction individuelle — plan complet docs/pilote-agent-wallet-10usd.md, rien codé à cette date (construit plus tard, voir la suite du fichier).

------------------------------------------------------------

[CONFIG] Sujet    : Clé API CDP créée par le dashboard avec 4 permissions par défaut (Transfer inclus)
Date : 2026.07.15 / Probleme : le dashboard Coinbase Developer Platform coche par défaut les 4 permissions (View/Trade/Transfer/Receive) sur une nouvelle clé API - Transfer actif aurait permis à tout outil connecté à cette clé (skill, MCP) de déplacer des fonds sans passer par aucun garde-fou applicatif, avant même que le wrapper de sécurité du pilote (plafond, slippage, kill-switch) n'existe.
Solution : clé corrigée en direct à View (lecture seule) uniquement avant toute validation - réflexe à répéter pour toute future clé créée via une interface web tierce, une permission cochée est un pouvoir actif immédiatement, indépendant de ce que le code fait aujourd'hui - docs/pilote-agent-wallet-10usd.md (cf. historique git 15/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Wallet agent CDP rejetait l'authentification (401) apres migration VPS
Date : 2026.07.21 / Probleme : le compte CDP restreint la cle API a une IP precise (allowlist) - l'ancienne IP (VPS pre-migration du 20/07) etait restee seule autorisee, toute authentification echouait silencieusement depuis la bascule.
Solution : allowlist IP mise a jour avec l'IP reelle du VPS actuel (detail dans aria-ops, prive) - cf. historique git 21/07

------------------------------------------------------------

[DEPLOYE] Sujet    : Collision de nom de wallet apres regeneration de cle API CDP
Date : 2026.07.22 / Probleme : get_or_create_account(name="aria-agent-wallet-pilot") resolvait vers un SECOND compte vide cree automatiquement lors de la regeneration de cle du 21/07, distinct du wallet historique (adresse 0xF04625162b616c5ad9788811b7be8CDd425B37Ef) qui detient le vrai solde - /agentwallet affichait 0 USDC alors que le solde reel on-chain etait non nul. x402_cdp_signer.py avait sa propre copie dupliquee du nom de wallet, non synchronisee.
Solution : WALLET_NAME = "aria-wallet" fixe en dur dans agent_wallet_cdp_adapter.py (verifie empiriquement, resout la bonne adresse) ; x402_cdp_signer.py importe desormais ce nom depuis ce meme module plutot que de le dupliquer - ne peut plus se desynchroniser. Aucune perte de fonds (le solde restait sur la blockchain, seule la lecture/signature pointait au mauvais endroit). Second wallet (vide, 0x584b2B35dac347B2317da0d21b95063de51257Ef) garde comme secours, jamais supprime - agent_wallet_cdp_adapter.py, x402_cdp_signer.py (commit 6cddf739)

------------------------------------------------------------

[DEPLOYE] Sujet    : Surveillance temps reel wallet agent (lecture seule) + commande /agentwallet
Date : 2026.07.16  /  Probleme : Aucune visibilite automatique sur les mouvements du wallet CDP reel (depots/sorties) et aucune commande Telegram pour consulter le solde reel (USDC/ETH/autres tokens).
Solution : agent_wallet_monitor.py (lecture seule, classification known/external_deposit/unexpected_outflow via Blockscout, registre append-only agent_wallet_movement_log, cycle heartbeat 10min gate ARIA_AGENT_WALLET_MONITOR_ENABLED) + commande /agentwallet (solde USDC+ETH+autres tokens avec valeur $ via dexscreener.fetch_tokens_batch) — agent_wallet_monitor.py (commits 16d2a505ce9c, 4c521e37c29e, 04356b851744)
------------------------------------------------------------
[CODE] Sujet    : execute_swap envoyait un montant en dollars au lieu de l'unite la plus petite du token
Date : 2026.07.16  /  Probleme : agent_wallet_cdp_adapter.execute_swap envoyait from_amount=str(amount_in_usd) (un montant en $) alors que l'API CDP attend une quantite en plus petite unite du token (ex. wei) — jamais exerce contre un vrai appel avant d'etre trouve, aurait fait echouer/mal-interpreter chaque swap reel des le premier essai.
Solution : bug trouve en revue avant le codage du declencheur swap/transfert, correction prevue dans le meme chantier (ETH natif exclu comme jambe de swap, aucune convention CDP documentee pour un sentinel ETH) — cf. historique git 16/07, agent_wallet_cdp_adapter.py

------------------------------------------------------------

[DEPLOYE] Sujet    : Pilote agent-wallet réel activé en prod (capital réel, décision autonome)
Date : 2026.07.18  /  Probleme : n/a — activation de fonctionnalité, pas un bug.
Solution : ARIA_AGENT_WALLET_PILOT_ENABLED=true en prod, vérifié en direct sur le conteneur (agent_wallet_pilot_enabled() == True). Doctrine "Option 2" : ARIA décide ET exécute seule, sans clic Telegram, dans les bornes déjà gravées (Règles absolues, Exception #3/#4) — agent_wallet_pilot_cycle.py (commit c9550624). Toute session future doit revérifier l'état réel du wallet/journal (agent_wallet_log) avant de supposer quoi que ce soit.

------------------------------------------------------------

[CODE] Sujet    : SDK CDP officiel — ValidationError Pydantic sur gasFee pour un swap trop petit
Date : 2026.07.19  /  Probleme : le SDK CDP (modèle CommonSwapResponseFees.gas_fee, champ déclaré non-nullable) plante en ValidationError quand l'API CDP omet ce champ — observé systématiquement sur des swaps de quelques centimes (probablement une contrainte de liquidité/quote de gas non calculable pour un montant minuscule), contradiction interne au SDK officiel, pas un bug côté ARIA.
Solution : échec catché et journalisé status="failed" (fail-closed, aucune perte de fonds réels dans les cas observés) — ne jamais fabriquer une valeur de gasFee pour contourner cette validation. Cooldown structurel dédié ajouté (is_structural_swap_failure, détection par sous-chaîne "validation error"/"pydantic", 7 jours — distinct du cooldown transitoire 60min et de momentum_blacklist.py) pour éviter de retenter en boucle un token structurellement cassé — agent_wallet_log.py/agent_wallet_pilot_cycle.py (commit b00d108c). Cause la plus probable identifiée mais non confirmée : solde du wallet trop bas (sizing 3% d'un solde très inférieur au plan initial 10-15$) pour qu'un devis de gas fiable soit calculable.

------------------------------------------------------------

[CODE] Sujet    : Cooldown swap agent-wallet insensible à la casse du contrat
Date : 2026.07.18  /  Probleme : la requête SQL de cooldown après un échec de swap ne matchait pas token_out en ignorant la casse — un même contrat écrit avec une casse différente aurait pu re-déclencher un swap pendant la fenêtre de cooldown, contournant la protection.
Solution : requête corrigée en LOWER(token_out) = ? — agent_wallet_log.py (cf. historique git 18/07, commit c9550624).
