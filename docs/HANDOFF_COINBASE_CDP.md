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

------------------------------------------------------------

[CONFIG] Sujet    : Protection MEV pour le pilote — évaluée, DIFFÉRÉE (backlog #14/#216)
Date : 2026.07.22  /  Probleme : le stress-test VC (Codex Partie 11) proposait une protection MEV pour le pilote agent-wallet, touchant potentiellement l'exécution de capital réel — évaluation demandée AVANT tout code, jamais un chantier lancé à chaud.
Solution : DIFFÉRÉ après recherche sourcée (pas construit). Base n'a pas de mempool public (séquenceur centralisé FCFS) — le vecteur sandwich attack classique n'a structurellement rien à observer ; une étude dédiée sur les L2 à mempool privé confirme des rendements nets négatifs pour ce type d'attaque. Le slippage 10% déjà en place borne le pire cas absolu à ~1$ sur un swap de 10-15$, un montant qui ne couvre généralement même pas le coût de gas d'un attaquant. Solutions existantes écartées : Flashbots Protect/MEV-Blocker ne couvrent pas Base (doc officielle) ; RPC protégés tiers (BlockRazor/dRPC/GetBlock) payants et ajoutent une dépendance externe ; 1inch Fusion exigerait une réécriture complète du flux de signature (intention hors-chaîne + dépendance résolveurs tiers, intérêt économique incertain sur un ordre de quelques dollars) ; le Smart Account CDP (Spend Permissions/Paymaster, piste séparée #216/#225) n'apporte aucune protection MEV documentée. **À revisiter si** : le capital du pilote grossit significativement au-delà de quelques centaines de dollars, OU Base bascule vers un séquençage partagé/décentralisé (jalon 2026, Espresso/Flashbots — déjà en veille CLAUDE.md), OU une perte anormale est observée dans `agent_wallet_log.py` (prix attendu vs exécuté). Aucun fichier modifié — recherche uniquement.

------------------------------------------------------------

[CONFIG] Subject  : Smart Account migration — step 1, both smart accounts created (owner = existing EOAs)
Date : 2026.07.23  /  Problem : n/a — deliberate infrastructure step, not a bug. Prior note above ("Smart Account CDP... RIEN construit") is now partially superseded by this entry.
Solution : Two `EvmSmartAccount`s created via `cdp.evm.create_smart_account(owner=..., enable_spend_permissions=True)` (cdp-sdk 1.47.1, confirmed real Python API — `create_smart_account(owner: BaseAccount, name, enable_spend_permissions=False)`). A Smart Account requires an EOA owner by design — the two existing EOAs were reused as owners rather than freed up, which reverses an earlier assumption in this same conversation that they'd become available for another purpose (they remain in active use as the signing key behind each contract). Mapping: `aria-smart-wallet-one` (address `0x81e26A7552e15D3B4cE50b505A382773b1CA0089`) owned by `aria-wallet` (`0xF04625162b616c5ad9788811b7be8CDd425B37Ef`); `aria-smart-wallet-two` (address `0xDa1a87f38E78Eb9564D135804414E089519C2B1c`) owned by `aria-agent-wallet-pilot` (`0x584b2B35dac347B2317da0d21b95063de51257Ef`). Created with a dedicated new CDP API key (Manage-only permission, IP-allowlisted to the VPS) — separate from whatever key/scope the two owner EOAs were originally created under. `enable_spend_permissions=True` only turns the CAPABILITY on at the contract level — no concrete spend policy/limit has been configured yet, that remains a separate step. **Not yet wired to any ARIA code** (`agent_wallet_cdp_adapter.py` still operates on the original EOAs directly) — this is purely the wallet-creation step of the migration; rewiring the pipeline to actually transact through the Smart Accounts, funding them with USDC, and configuring real spend-permission policies are all still open, deliberately sequenced steps.

------------------------------------------------------------

[CONFIG] Subject  : Smart Account migration — step 1 REVISED same day: owners switched from CDP EOAs to operator's Tangem hardware wallets, purpose-named (VC vs swing-trading)
Date : 2026.07.23  /  Problem : n/a — design revision before any funding happened (both prior smart accounts were still empty), not a fix. The two CDP-EOA-owned smart accounts from the entry above were superseded within the same session, before any transfer was ever executed against them.
Solution : Operator decided the two smart accounts should be owned by physical Tangem hardware wallets instead of CDP-managed EOAs, for offline/emergency control independent of CDP, AND split by PURPOSE rather than arbitrary numbering (VC investment pocket vs swing-trading/momentum pocket) — matching the project's existing 85% VC / 15% trading split. `create_smart_account` only reads `owner.address` internally (verified in cdp-sdk 1.47.1 source — `owners = [owner.address]`, no signature required from the owner at creation time), so the Tangem's PUBLIC address alone was sufficient; no private key ever transited through Claude Code or the VPS.

The two prior CDP-EOA-owned smart accounts (`aria-smart-wallet-one` owned by `aria-wallet-X402`, `aria-smart-wallet-two` owned by `aria-wallet-transfert`) were renamed to `aria-smart-wallet-orphan-one`/`aria-smart-wallet-orphan-two` and abandoned (no delete capability exists in the CDP SDK for a deployed smart-account contract — same precedent as the earlier orphaned EOA documented above). Never funded, so no migration was needed.

New mapping:
- `aria-smart-st` (address `0x800027f61363EF304c5C2Afee811d9d4074B474c`) — owned by Tangem `0x33783cCb570Cb279C25F836806B5c4C3C8309777` ("tangem-01"). **This same address is `agent_wallet_pilot.ALLOWED_TRANSFER_ADDRESS`** (Exception #4, existing hardcoded transfer destination) — operator confirmed this reuse is intentional (same physical device), not a mix-up.
- `aria-smart-vc` (address `0x9C72AedD2836Edc24566E8B0Fd1825e0E1eFbF07`) — owned by a second, distinct Tangem address `0x85e3D8128a9b7be14065A4E36C1845041BF65d7F`.

Design refined in the same conversation (not yet built): on `aria-smart-st`, only SWAPS should run unattended (via a granted CDP Spend Permission — see `cdp.spend_permissions`/`EvmSmartAccount.use_spend_permission`, verified real in the SDK: grant requires one Tangem-signed transaction via `EvmClient.create_spend_permission`, actual spending afterward is signed by the granted "spender" — a separate CDP-managed EOA — with NO Tangem tap needed per trade); any TRANSFER (funds leaving the wallet to another address) requires a manual Tangem confirmation on BOTH `aria-smart-st` and `aria-smart-vc`, no exception. `aria-smart-vc` itself requires a Tangem tap for every action, investments included — matches (and hardware-enforces, beyond a Telegram click a code bug could bypass) the project's existing absolute rule that VC real capital always needs human validation.

**Open question, not yet verified**: whether the CDP Spend Permission mechanism can scope the granted spender to ONLY call a swap/DEX-router-style interaction (as opposed to a generic token transfer) at the contract level, or whether that swap-vs-transfer distinction still needs to be enforced in application code (same pattern as the existing separate `attempt_swap`/`attempt_transfer` functions for the current EOA-based pilot) rather than guaranteed by the on-chain permission alone. Needs checking before any real spend-permission policy is configured and relied upon.

Still open, nothing built: (1) fund `aria-smart-st`/`aria-smart-vc` (still empty), (2) verify the swap-vs-transfer scoping question above, (3) configure a real spend-permission policy for the `st` swap path, (4) write the code that executes via `use_spend_permission` instead of direct owner signing, (5) design the Tangem-gated direct-owner-tx path for `vc` (and for transfers on `st`), (6) rewire `agent_wallet_cdp_adapter.py` once the wave-2 translation pass is done (the file is in its batch). Real capital involved at every step — operator confirmation required before each one goes live.

------------------------------------------------------------

[DEPLOYE] Subject  : ALLOWED_TRANSFER_ADDRESS (Exception #4) changed to aria-wallet-transfert
Date : 2026.07.23  /  Problem : n/a — deliberate operator-directed change to a named guardrail constant, not a bug. The prior address turned out to be the operator's personal Tangem (`tangem-01`, meanwhile also reused as `aria-smart-st`'s owner, same day) — operator decided the pilot's allowed transfer destination should instead be a dedicated CDP wallet.
Solution : `agent_wallet_pilot.ALLOWED_TRANSFER_ADDRESS` changed from `0x33783cCb570Cb279C25F836806B5c4C3C8309777` to `0x584b2B35dac347B2317da0d21b95063de51257Ef` (`aria-wallet-transfert`, the renamed ex-"aria-agent-wallet-pilot" EOA — see the Smart Account migration entries above for its full history). Single source of truth confirmed before editing (`grep` across the codebase): only one constant declaration, every call site references it by name, no hardcoded duplicate to miss. `tests/test_agent_wallet_pilot.py` imports `pilot.ALLOWED_TRANSFER_ADDRESS` rather than hardcoding the value, so it needed no changes and passed unmodified — `agent_wallet_pilot.py` (commit pending). Full suite for the 3 affected test files (92 tests) green after the change.

------------------------------------------------------------

[CONFIG] Subject  : aria-smart-st / aria-smart-vc funded with small real test amounts
Date : 2026.07.23  /  Problem : n/a — deliberate first funding step, not a bug.
Solution : 2 USDC + ~$2 of ETH sent to EACH smart account from `aria-wallet-X402` (`account.transfer(token=..., network="base")`, cdp-sdk 1.47.1) — ETH amount (0.00104253 ETH/wallet) computed from a live spot price fetched just before the transfers ($1918.435/ETH, Coinbase public spot API). Confirmed via `list_token_balances` after the transfers: both `aria-smart-st` (`0x800027f61363EF304c5C2Afee811d9d4074B474c`) and `aria-smart-vc` (`0x9C72AedD2836Edc24566E8B0Fd1825e0E1eFbF07`) hold exactly 2 USDC + 0.00104253 ETH each. Source wallet (`aria-wallet-X402`) balance before this: 0.0166 ETH + 12.728007 USDC — plenty left for gas on future transactions. No spend-permission policy configured yet, no swap/transfer code wired to these wallets — purely a funding step ahead of the still-open items in the entry above (#41: verify swap-vs-transfer scoping, configure real spend-permission policy, write `use_spend_permission`-based execution code, rewire `agent_wallet_cdp_adapter.py`).

------------------------------------------------------------

[DEPLOYE] Subject  : Live pilot wallet-name bug fixed in prod + duplicate EOA incident + final orphan naming convention
Date : 2026.07.23  /  Problem : discovered live via `agent_wallet_monitor.py`'s own Telegram alerts ("SORTIE NON INITIÉE PAR ARIA") + a new "aria-wallet" EOA (`0x8e71C3e9396ded76AdA6EA56cD3c315C3D67D79b`) auto-created today by the STILL-RUNNING prod container, which had the OLD `WALLET_NAME`/`ALLOWED_TRANSFER_ADDRESS` baked into its already-loaded code — editing the source file alone never reaches a running container (per this project's own established fact: rebuild required, `git pull` + restart isn't enough). Exact same failure mode as the 21/07 incident, this time self-inflicted by the same-day rename rather than an API key change. Separately, the two "TOKEN SUSPECT" alerts seen in the same Telegram feed at the same time are a REAL address-poisoning attack (lookalike addresses `0x80005B5aCEB5cc0164c069Db15E0af4A1B7B474C` / `0x9c727dA07Ec5e785a4926860250FC7Aebc9Fbf07` mimicking `aria-smart-st`/`aria-smart-vc`'s real prefixes/suffixes) — correctly caught and labeled by the monitor, no action needed beyond never manually copying an address from transaction history without full character-by-character verification.
Solution : Committed + pushed (`d19b0513`) + deployed (`./vanguard/deploy.sh`, blue-green, verified live through nginx) the fix already made to `agent_wallet_cdp_adapter.py`/`agent_wallet_pilot.py` earlier the same day. The accidental duplicate EOA and the two now-superseded CDP-EOA-owned smart accounts were renamed to a clear "abandoned" convention (final form, after CDP's naming regex rejected underscores — alphanumeric + hyphens only, `^[A-Za-z0-9][A-Za-z0-9-]{0,34}[A-Za-z0-9]$`): `0-OLD-wallet-aria` (was `0x8e71C3e9...`), `0-OLD-smart-wallet-one` / `0-OLD-smart-wallet-two` (were `aria-smart-wallet-orphan-one`/`-two`). No delete capability exists in the CDP SDK for either EOAs or smart accounts (both are real on-chain addresses/contracts) — renaming to an unambiguous "0-OLD-" prefix, which sorts to the top of the dashboard list, is the best available substitute for deletion. **Lesson for any future CDP account rename**: a source-level fix to a hardcoded account name is NOT sufficient by itself — the running production container must also be redeployed before the rename is safe, or the live process will repeat this exact incident on its next heartbeat cycle.

------------------------------------------------------------

[CONFIG] Subject  : Smart Account design refined -- swap-vs-transfer split now resolved via CDP Policy Engine (not just Spend Permission)
Date : 2026.07.23  /  Problem : the open question from the entries above (can a Spend Permission be scoped to swap-only vs generic transfer at the contract level) was investigated properly (real ABI inspection + web research + real SDK method/field inspection, never guessed). Finding: `SpendPermissionManager.spend()` is PURELY a "transfer up to `value` of `token` to the spender" primitive (confirmed via `cdp.spend_permissions.SPEND_PERMISSION_MANAGER_ABI`) -- it has NO concept of "swap," and Coinbase's own docs confirm this explicitly ("this approach does not enable apps to make arbitrary external calls from user accounts"). So a Spend Permission alone cannot guarantee "swap-only" -- the withdrawn value briefly sits in the spender's own custody before the spender executes the actual swap and (if the code is correct) returns the output to the smart account. This is a real, distinct risk from bad-token/rug risk: a code bug or a compromised spender key could misdirect funds during that window, independent of token quality.
Solution (design, nothing built yet) : combine TWO CDP mechanisms rather than relying on Spend Permission alone. (1) Spend Permission on `aria-smart-st` still governs value/token/period (operator wants this UNCAPPED for swaps specifically, capped/blocked for transfers -- see below). (2) A CDP Policy (`cdp.policies.create_policy`, scope="account", confirmed real in cdp-sdk 1.47.1 -- `PoliciesClient.create_policy/delete_policy/get_policy_by_id/list_policies/update_policy`, rule type `SendEvmTransactionRule` with `EvmAddressCriterion`/`EvmDataCriterion` letting a rule match on destination contract address AND call data/method) attached to the SPENDER account, allowlisting ONLY the specific DEX router address + swap method selector, rejecting everything else including a raw ERC-20 `transfer()` call. This is enforced by CDP's own signing-layer infrastructure (Trusted Execution Environment per Coinbase's own docs), not application code discipline -- closes the gap the Spend-Permission-alone design left open.
Operator's actual intent (23/07, clarified after the above): `aria-smart-st` — swaps UNCAPPED in volume/frequency (Spend Permission allowance effectively unlimited for the swap path specifically, restricted to only ever be usable for swaps via the Policy above), but any TRANSFER to another wallet always requires the Tangem owner directly (never delegated to the spender at all -- no Spend Permission/Policy path should ever authorize a raw transfer). `aria-smart-vc` — no delegation of any kind; every action (swap or transfer) requires the Tangem owner's direct signature, no spender, no policy needed.
Still open, nothing built: (1) create the actual spender CDP account (new, dedicated, never reused), (2) create the Spend Permission on `aria-smart-st` (account=aria-smart-st, spender=new account, token=USDC, allowance=effectively unbounded per operator's explicit choice after being warned of the tradeoff, period=TBD), (3) create + attach the CDP Policy on the spender account allowlisting only the intended DEX router/swap method, (4) verify end-to-end on a tiny real amount before trusting this for the live pilot, (5) write the `use_spend_permission`-based execution code in `agent_wallet_cdp_adapter.py` (still pending wave-2 translation completion on that file), (6) confirm whether `cdp.policies.create_policy` needs the "Manage" API-key permission (Non-custodial section) -- not yet tested, prediction only.

------------------------------------------------------------

[CONFIG] Subject  : Option A vs Coinbase Agentic Wallets -- deep comparison completed (3-agent research workflow), design refined with real cdp-sdk source citations
Date : 2026.07.23  /  Problem : n/a -- operator asked to "compare and simulate" before committing further engineering time, given the design above was still speculative on several points (exact custody mechanics, whether the Policy Engine could really enforce swap-only, whether a ready-made Coinbase product might be a better fit).
Solution : Real research (reading the installed cdp-sdk 1.47.1 source directly, file:line citations, not docs summaries) confirmed and refined the plan above, and evaluated Coinbase's separate "Agentic Wallets" product (docs.cdp.coinbase.com/agentic-wallet/welcome, launched ~Feb 2026) as an alternative. Full reports archived in this session's workflow transcript; key confirmed facts below.

**Verdict: Agentic Wallets REJECTED for this design.** It is a genuinely separate product built on plain CDP Server Wallet v2 EOAs (MPC + AWS Nitro Enclave custody), NOT a layer on top of the EVM Smart Accounts already built today. It has no hardware-wallet-owner concept at all -- no field, no mechanism, nothing analogous to `EvmSmartAccount.owner`. It cannot express "transfer requires a physical Tangem tap" at any price -- this is a missing product concept, not a missing config option. Adopting it would mean abandoning the already-created, already-funded `aria-smart-st`/`aria-smart-vc` for new wallets (email-OTP onboarding) plus a real on-chain fund migration, plus bolting a Node.js-only CLI/MCP toolchain onto an all-Python backend (no Python SDK exists for this product). Not recommended.

**Option A (Smart Account + Spend Permission + Policy Engine) confirmed as the only viable path, with the exact mechanics now nailed down from source:**
- Smart Accounts support only ONE owner today (`create_evm_smart_account_request.py`: "Today, only a single owner can be set... array to allow setting multiple owners in the future" -- no `add_owner` method exists in 1.47.1).
- The CDP Policy Engine can ONLY attach to a plain EOA account (`account_policy` field exists on `create_evm_account_request.py`/`update_account`), never directly to a Smart Account (no such field on `create_evm_smart_account_request.py`/`update_evm_smart_account_request.py`). This is *why* the Policy must live on the spender EOA, not on `aria-smart-st` itself -- forced by the SDK, not a stylistic choice.
- Direct smart-account swaps DO keep custody inside the smart account (confirmed: `send_swap_operation.py` -- the smart account is the on-chain taker, output lands back in it) -- but every such call requires a LIVE owner signature via `owner.unsafe_sign_hash(...)` (`send_user_operation.py:79-81`), called synchronously, inline, every time, with zero session-key/cached-authorization escape hatch in the SDK. For `aria-smart-st`/`aria-smart-vc`, `owner` is the Tangem address -- meaning direct smart-account swaps would require a physical Tangem tap per swap, defeating the automation goal entirely.
- **Spend Permission (`spend()` on the SpendPermissionManager, confirmed contract address `0xf85210B21cC50302F477BA56686d2019dC9b67Ad`) is therefore not optional -- it's the ONLY value-movement path in the SDK that does NOT call the owner's signature on every use.** Granting it (one-time) still requires the Tangem's live signature; using it afterward (the spender pulling funds) does not.
- **NEW, previously-unidentified hard prerequisite: a "Tangem-to-`BaseAccount` signing bridge" must be built** -- a custom Python class satisfying `eth_account.signers.base.BaseAccount`'s interface (`.address`, `.sign_message`, `.unsafe_sign_hash`, `.sign_transaction`) that, on each call, gets a real signature from the physical Tangem device (most likely via a WalletConnect v2 session to the operator's phone -- Tangem's consumer app supports standard `eth_signTypedData_v4`/`eth_sendTransaction` this way, but nothing off-the-shelf bridges it to cdp-sdk). Nothing like this exists yet. Needed for: the one-time Spend Permission grant on `aria-smart-st`, AND 100% of every action on `aria-smart-vc` (by design). **This is the single largest unbuilt piece of the whole plan (2-4 days of the 5-9 day total estimate), bigger than the Policy/Spend-Permission wiring itself.**
- Policy Engine mechanics for "swap yes, transfer no", confirmed from `cdp/policies/types.py`: no native "this is a swap" operation exists for our account type (`CreateEndUserEvmSwapRule` exists but is scoped to CDP's *End User/embedded wallet* product line, not applicable here). Must instead combine `EvmAddressCriterion` (allowlist the DEX router CDP's swap backend routes through -- **not hardcoded in the SDK, must be verified empirically per real quotes on Base before trusting it, and re-checked if it ever changes**) with `EvmDataCriterion` (decode calldata against `KnownAbiType.erc20/erc721/erc1155` -- e.g. an explicit `reject` rule matching ERC-20 `transfer`/`transferFrom` regardless of destination, as defense-in-depth). Rule evaluation is top-down first-match, default-deny if nothing matches.
- **Critical gap confirmed in the CURRENT "unlimited" design decision (23/07)**: the Policy Engine and Spend Permission together only ever gate on WHO (spender) and WHERE (destination/method) -- **neither gates on AMOUNT**. With the allowance set to "unlimited" per the operator's own explicit choice, a sizing bug that attempts to swap 100% of the wallet instead of an intended 2% position is NOT stopped by anything in this design -- it would succeed (bounded only by whatever the wallet actually holds, never more, no leverage possible, and a raw transfer-out is still blocked). **A real numeric cap on the Spend Permission allowance (not "unlimited") is needed for this design to actually deliver "a real safety net against errors," which was the operator's explicitly stated goal when this comparison was requested.**
- Concrete build sequence confirmed (see full research for exact code): (1) build the Tangem bridge first, test on a trivial gesture, (2) one-time Spend Permission grant via the bridge (try `cdp.evm.create_spend_permission()` first, but the manual `approve()` FunctionCall via `send_user_operation` is the safer fallback -- unverified whether the convenience wrapper even works for an externally-owned account), (3) create the dedicated spender EOA (never reused), (4) create + attach the Policy to the spender, (5) runtime loop: spender pulls funds via `use_spend_permission`, swaps with `spender.swap()` (plain EOA swap, simpler than the smart-account path), then MUST explicitly transfer the output back to `aria-smart-st` (a real, distinct code step -- if missed/buggy, funds sit stranded in the spender, not stolen, but misplaced and needing manual recovery), (6) transfers on `aria-smart-st` always go through `aria_smart_st.transfer()` directly (Tangem bridge, every time, by design), (7) `aria-smart-vc` never uses Spend Permission/Policy at all -- every action through the Tangem bridge directly.
- Effort estimate: ~5-9 engineering days total, dominated by the Tangem bridge (least confident number, most novel piece, should be prototyped early and cheaply before committing to the rest).

**Recommendation adopted**: proceed with Option A, but do NOT configure the Spend Permission as "unlimited" as originally planned -- replace it with a real, calibrated numeric ceiling before this is trusted for live use. Next concrete steps, in order: (1) prototype the Tangem↔BaseAccount signing bridge on a trivial gesture, (2) decide and configure a real Spend Permission ceiling (not unlimited), (3) create the dedicated spender + Policy, (4) end-to-end test on a tiny real amount, (5) wire the execution code into `agent_wallet_cdp_adapter.py` (blocked on wave-2 translation completing on that file). Nothing built yet beyond this session's research and design.

------------------------------------------------------------

[CODE] Subject  : Smart Account swing pocket -- Model B chosen, safety-envelope foundation built (spend-permission cap)
Date : 2026.07.23 / Problem : the migration design (entries above) was fully researched but nothing was built. Operator confirmed the direction explicitly: the SWING pocket (aria-smart-st) must trade autonomously via the delegated spender (no Tangem tap per swap), while every TRANSFER out and every VC action still requires the Tangem owner. Operator also settled the safety-critical open decision from the research above: the Spend Permission allowance is NOT unlimited -- $50/week auto-renewing ("prudent to start, scale once proven"), which restores the "real safety net against errors" the research flagged as missing.
Solution : new `agent_wallet_smart_swing.py` (DORMANT, gate `ARIA_SMART_SWING_ENABLED` OFF, wired to nothing) holding (1) the verified on-chain identities read from the DEPLOYED `agent_wallet_monitor.MONITORED_WALLETS` (never memory): aria-smart-st `0x800027f61363EF304c5C2Afee811d9d4074B474c`, aria-smart-vc `0x9C72AedD2836Edc24566E8B0Fd1825e0E1eFbF07`, spender `0x8e71C3e9396ded76AdA6EA56cD3c315C3D67D79b` (**already created**, the research's "create spender" step is done), Tangem owners `0x33783cCb...`/`0x85e3D812...`; (2) `build_spend_permission_input()` -- pure, network-free, encodes the $50/week cap as a STRUCTURAL invariant (`_MAX_SANE_ALLOWANCE_USD=10_000` guard makes an unlimited/absurd allowance impossible to produce even from a careless edit -- a real raise is a deliberate operator decision, never silent). 19 tests. Full suite green, test_coherence.py green (not yet committed at the time of this entry).
**SDK surface verified against the really-installed cdp-sdk 1.47.1 (never guessed, this is the reference for the next build step):**
- `SpendPermissionInput(account, spender, token, allowance:int-atomic, period_in_days)` builds/validates; `SPEND_PERMISSION_MANAGER_ADDRESS=0xf85210B21cC50302F477BA56686d2019dC9b67Ad`.
- `EvmClient.create_spend_permission(spend_permission, network, paymaster_url?, idempotency_key?) -> EvmUserOperation` (the one-time grant, needs the Tangem owner's signature).
- The spender (`EvmServerAccount`, plain CDP-managed EOA, NO Tangem) has `use_spend_permission(spend_permission, value:int, network) -> str` (pulls funds, NO owner signature) AND `swap(AccountSwapOptions) -> AccountSwapResult` AND `transfer(...)` (the last is what the Policy must box in).
- **Swap output lands in the SPENDER, not in a chosen recipient** -- `AccountSwapOptions` has NO recipient field. So the spender MUST transfer the output back to aria-smart-st, and since the OUTPUT token varies per trade, the Policy's return-transfer carve-out must allowlist "an ERC-20 transfer whose recipient == aria-smart-st, token-AGNOSTIC" (via `EvmDataCriterion` on the `to` parameter), NOT a fixed token contract. This is the single most delicate, safety-critical piece -- its real enforcement MUST be validated against live CDP on a tiny amount before any grant is trusted.
- Policy shapes confirmed: `cdp.policies.CreatePolicyOptions(scope, description, rules)`, `SendEvmTransactionRule(action:'accept'|'reject', operation:'sendEvmTransaction', criteria)`, `EvmAddressCriterion(type, addresses, operator)`, `EvmDataCriterion(type, abi, conditions)`; top-down first-match, default-deny. The DEX router `to` address is obtainable dynamically from a real quote (`QuoteSwapResult.to`) -- must be confirmed STABLE across several real quotes before hardcoding it into the allowlist.
**Still open, in order (next chunk, hardware/verification session):** (1) build `build_swap_only_policy(router_address)` (rule1 allow swap router, rule2 allow token-agnostic transfer only to aria-smart-st, default-deny) + validate it against live CDP on a tiny amount; (2) build the guarded execution path (`use_spend_permission` -> swap -> return-to-SA) with the same app-layer guards as agent_wallet_pilot.py (per-tx cap, real-balance check, forced 10% slippage, /stop, logging); (3) the one-time Spend Permission grant (Tangem tap) -- still needs the Tangem<->BaseAccount signing bridge (the 2-4 day piece, unbuilt); (4) fund aria-smart-st with real USDC; (5) end-to-end test on a tiny real amount before flipping the gate. `agent_wallet_smart_swing.py` (new) / `test_agent_wallet_smart_swing.py` (new).
