# Journal de bord Aria

> Actions techniques de l'assistant (fichiers, repos, commits).  
> Décisions métier → `COLLEGUE.md` § Journal.

## 2026-06-19

14h00 — Installation skill journal-de-bord (aria-skills)
08h54 — Test journal de bord — skill journal-de-bord
09h04 — Installation journal-de-bord pour Cursor (.cursor/rules + skills)
09h14 — Activation ARIA_LLM_ENABLED + fix routage GitHub (dexpulse)
09h29 — Ajout workflow GitHub Actions render-keep-alive (ping /api/health toutes les 12 min)
09h50 — Politique zéro mention pairs ZHC — dexpulse (narrative, skills, canonical)
09h54 — Clarification /directive = amélioration ARIA — directives.md, narrative.py
10h04 — Indice ARIA niveaux 0-1000 — capability_levels, rubric, /qi Telegram
10h15 — Split aria-core vers aria-sandbox — dexpulse hôte seulement
10h19 — Docs post-split aria-core — AGENTS, canonical, tests, truth-ledger README
10h28 — Migrer tests ARIA vers aria-core + CI anti-dérive (aria-sandbox, dexpulse)
10h31 — Aligner docs split aria-core (README, VISION, SPLIT, ECOSYSTEM-REPOS, COLLEGUE)
10h33 — Clos split aria-core (tests 28/28, pin SHA, bump script, README)
10h35 — Cosmétique split — VISION §8, pin SHA docs, README UTF-8
10h46 — Fix holding_site audit GitHub + garde anti-hallucination deploy
10h52 — implémente patch étoile filante holding_site_skill (aria-sandbox d4ad57f)
10h54 — audit et sync droits ARIA Render (GITHUB_*, protected repos)
11h03 — enregistre X_API_KEY dans production.env (OAuth incomplet)
11h03 — enregistre X_API_SECRET dans production.env
11h03 — enregistre X_ACCESS_TOKEN dans production.env
11h04 — sync OAuth X complet vers Render (4 clés)
11h04 — sync X_BEARER_TOKEN vers Render
11h08 — force redeploy Render + fix sync-render apres env X
11h11 — audit ARIA: check-aria-status, fix sync redeploy, comms posted
11h17 — commit runbook operateur aria-sandbox dexpulse-secrets aria-skills collegue-memoire
11h29 — fix telegram oui/non learn approval aria-sandbox + pin dexpulse
11h32 — fix mode autonome learn + redeploy Render aria-core 242fb1b
11h42 — suppression mock insight X setup curiosity aria-core
11h57 — watchlist X GoldenFarF solvrbot grok aixbt_agent aria-core
11h59 — fix watchlist GoldenFarFR
12h11 — fix comms X compose LLM pas colon francais
12h18 — feat X mentions learn like sans reply aria-core sync-render
12h25 — filtre pertinence memoire cognitive X aria-core
12h28 — learning scope ZHC autonomie marketing canonical_facts
12h32 — feat triage Groq memoire X (aria-sandbox 6bd32bf + pin dexpulse)
12h38 — feat noyau epistemique Phase A (epistemic_core.yaml + brain)
12h40 — audit ARIA + sync-render deploy 5100ef5 + fix script
12h46 — details ops: webhook secret, sync-local, pin 194f8f1, sync-render deploy fix
12h51 — fix epistemic president France + groq factual hors noyau
12h55 — feat Groq calibré toute question (YAML politiques ZHC only)
13h06 — corrige silence Juno/zhcinstitute narrative entrepreneur brain
13h19 — deploie epistemic Phase B web verify calibration /calibrate
13h31 — ajoute Stripe DEXPulse Pro billing dexpulse + pricing Vanguard
13h35 — corrige epistemic date jour + web actu sport
13h43 — fix launchpad methodology sources + intent developpeur aria-core 2910942
13h49 — fix web search sport DDG HTML aria-core 487871d + STRIPE_PRICE_ID
13h52 — feat presentation investor launchpad aria-core 238d37f
13h56 — configure STRIPE_SECRET_KEY test mode production.env + sync-render
13h59 — fix actu sport fallback web e3b096c deploy Render
14h03 — fix alignement tableau launchpad f869277
14h12 — ajoute stripe/recovery-codes.txt dans dexpulse-secrets GH
14h15 — verrouille repos GitHub GoldenFarFR (8/8 privés) + REPO-SECURITY.md
14h15 — commit dexpulse-secrets REPO-SECURITY.md + collegue-memoire
14h23 — live-info format Telegram + pin aria-core cea0783 + fix Dockerfile privé
14h36 — doc stripe/README.md aligné docs.stripe.com + health stripe_webhook_configured
14h53 — migration API holding api.ariavanguardzhc.com + setup-holding-api.ps1
15h02 — fallback onrender Vanguard+webhooks (DNS api absent)
15h02 — commit push dexpulse-secrets scripts holding
15h02 — check-aria-status fallback health OK
15h06 — corrige auth IONOS X-API-Key + register-ionos-api-key.ps1
15h15 — CNAME api IONOS + holding canonique + webhooks api.
15h19 — coffre local GoldenFar vault + migration secrets hors dexpulse-secrets
15h25 — MULTI-PC-VAULT Syncthing Bitwarden export .gfv
15h30 — Syncthing installe + goldenfar-vault + demarrage auto
15h32 — COLLEGUE.md analyse installation multi-PC + regle assistant
16h07 — met a jour STRIPE_WEBHOOK_SECRET + sync-render
16h09 — corrige prix DEXPulse Pro 5 USD (coffre + config)
16h29 — valide test paiement Stripe DEXPulse Pro 5 USD OK
16h43 — aria-core identité avatar autonome + 3 portraits galerie
16h53 — sync avatar ARIA Telegram + X (aria-core)
17h03 — repare Telegram webhook + DNS api + sync Render srv-d8pbu6ok
17h04 — COLLEGUE + MIGRATION-VANGUARD.md fin hote DEXPulse
17h12 — migré API vers aria-vanguard (commit 7749f83, Render aria-api live)
17h12 — mis à jour scripts opérateur dexpulse-secrets (sync-render, migrate-api-vanguard)
17h26 — déplacé VISION.md + ECOSYSTEM-REPOS.md vers aria-vanguard, mis à jour références
17h29 — supprimé repo GitHub GoldenFarFR/dexpulse + dossier local
17h37 — fusionné dexpulse-secrets dans aria-vanguard/operator, repo GitHub supprimé
17h47 — batch sync truth-ledger GitHub (100 fichiers/commit)
17h51 — push aria-sandbox 9e5372c + aria-vanguard f4a91d4 (batch truth-ledger)
17h54 — deploy Render 8d7bdbf — aria_core e236b7d batch truth-ledger live
17h56 — supprime repo GitHub aria-telegram + MAJ canonical_facts
18h02 — fix Telegram webhook — SITE_BASE_URL fallback Render (SSL api holding)
18h12 — repare Telegram webhook redeploy race aria-core fa3a656
18h21 — MAJ template-grok-cursor ecosysteme 2026 aria-vanguard
18h26 — fix avatar sync Telegram flood control aria-core 720644d
18h31 — feat identité visuelle avatar ARIA aria-core 35d7acd
18h36 — feat workflow compose tweet /x compose aria-core 70e9136
18h38 — redeploy Render aria-api c88e39f — workflow /x compose live
18h40 — feat API avatar style Grok Imagine cc1fa67 aria-core + pin 8c81052
18h44 — fix comms_skill publication auto sur propose tweet — pin 22f3f46
18h54 — feat coaching rôle ZHC — pin 5f61a36
18h59 — feat registre handles X alias @veille pin a2175a4
19h02 — fix compose consignes ton personnel pin 3fb1865
19h08 — fix compose brouillon vide + développe pensée pin 35e21a1
19h13 — fix compose brouillon direct écrit tweet pin d7b5a36
19h16 — deploy /handles Telegram — pin f22b12d vanguard 7098162
19h22 — fix compose +veille handles — pin c2a91af
19h26 — fix salutations gm/hello — pin 6feb16b
19h30 — fix politique X — faux positif nfa + policy compose
19h34 — fix salutation gm Vanguard sans DEXPulse
19h38 — fix brouillon X anglais auto — plus blocage français
19h44 — feat compose tweets variés mémoire anti-répétition
19h51 — implémenté mémoire tweets publiés + follow-up compose (aria-core 4b29872, deploy Render)
19h52 — doctrine ZHC identité cognitive + boucle compose→mémoire (ced8608)

## 2026-06-20
12h14 — créé repo aria-local-sync + collect PCDESS9
12h17 — aria-local-sync: export coffre chiffre .gfv dans collect/apply
12h18 — poussé goldenfar-vault.gfv dans aria-local-sync
12h21 — ajouté SETUP-AUTRE-PC.md dans aria-local-sync
12h26 — TOTP + watch-vault + SECURITE-CLES aria-local-sync
12h27 — active rotation quotidienne vault aria-local-sync (PCDESS9)
12h27 — pousse feat rotation quotidienne aria-local-sync GitHub
12h29 — active TOTP Google Authenticator vault aria-local-sync (PCDESS9)
12h31 — maj SETUP-AUTRE-PC.md liaison 2 PC + TOTP rotation
12h34 — ajoute test-vault-security.ps1 simulation attaque PCDESS9
12h35 — clarifie TOTP manuel vs rotation 03h00 aria-local-sync
12h36 — ajoute test-totp-live.ps1 + param TotpCode collect/apply
12h38 — simule TOTP + collect-local + test-vault-security PCDESS9
12h39 — ajoute simulate-interactive.ps1 fenetre TOTP utilisateur
12h50 — pont TOTP ARIA Telegram deploye + ADMIN_API_SECRET genere
12h51 — test pont TOTP Telegram ARIA OK (PCDESS9)
12h53 — maj SETUP-AUTRE-PC.md guide complet 2e PC + push GitHub
12h55 — ajoute collect-session + session-handoff manifestes Grok multi-PC
12h59 — automatise session-handoff Grok always-on + HANDOFF.md GitHub
13h02 — ajoute SESSION-CHECKLIST.html visuelle + write-session-checklist.ps1
13h12 — ajoute audit-github-security + session Git TOTP 12h session-handoff
13h16 — ajoute alerte Telegram audit GitHub critical + API operator/notify
13h19 — aria-core: self_maintenance + banniere X + classification ordres operateur
13h21 — audit GitHub: critical lie IP/origine + report-machine-ip.ps1
13h23 — corrige test test_capability_gap local_only (monkeypatch github_skill + async notify)
13h23 — commit feat Phase 3 capability_gap aria-sandbox 7bd46dd8
13h23 — pousse feat Phase 3 capability_gap aria-sandbox GitHub
13h25 — deploy Phase 3b self-improve (audit, health, QI, curiosite 6h) aria-sandbox + vanguard + local-sync
13h30 — bump pin aria-core f562275 + sync-render deploy live Phase 3b
13h34 — pousse handoff multi-PC complet aria-local-sync + sessions collegue-memoire (depart PCDESS9)
13h36 — corrige ordre Bitwarden avant bootstrap CHANGEMENT-PC-MAINTENANT.md
13h37 — feat handoff zero-touch ensure-pc-ready + rules/skills (Sylvain ne dit rien)
14h38 — feat culture large + app factory Play Store aria-core VISION
14h44 — feat ARIA Gem Crush POC repo + homepage vanguard aria-core holding
14h51 — simplifie copy POC Gem Crush vanguard (test seul)
14h54 — feat gem crush polish v1 + heartbeat daily improve ARIA
14h57 — feat gem crush candy-tier v7 son tuto swipe confetti indices
14h59 — push gem crush POC aria-vanguard 3d04a80 + aria-sandbox heartbeat
15h05 — gem crush stress 5min heartbeat + patches v8-v20
15h08 — commit aria-vanguard pin aria-core 82855d59 Gem Crush Telegram notify
15h12 — push aria-sandbox build 82855d5 + redeploy aria-vanguard notify Telegram Gem Crush
15h20 — feat gem-crush releases groupées v21-24 aria-sandbox 9428b727
15h22 — feat gem-crush gameplay bundles v25-28 batch commit c7a9d5d4
15h28 — fix gem-crush build engine.ts + badge version v27 aria-vanguard 0d0393a
15h40 — feat gem-crush v29 visuel branché CSS+TS aria-vanguard
15h46 — feat gem-crush mode premium recherche concurrence releases 7j v31-33
16h10 — feat gem-crush cycle 30 min recherche Candy Crush Clash Royale releases massives v30-v38
16h45 — feat aria-worker file ouvrier ARIA-WORKER.md quand ARIA bloquée sandbox 8a4d26a9
17h15 — ouvrier ferme 32 issues aria-sandbox dedup cap-gap 0fe97c36 + pin vanguard f3ac1d7
17h35 — fix clarifier avatar vs ancre identite vs banniere X 3:1 (x_banner.py self_maintenance)
16h16 — commit aria-sandbox 6ae9bd91 — Gem Crush grounded (no web APK)
16h16 — commit aria-vanguard 97f0619 — bump pin aria-core Gem Crush
16h19 — fix x_banner 1500x500 3:1 — commit 24d9de2e + pin vanguard
16h38 — pull-render PC-SYLVAIN — production.env 49 vars
16h42 — sync-render IMAGE_API_KEY — deploy live Render
16h46 — feat visual_autonomy 5276081c — Imagine avatar+banniere auto
16h54 — force visual cycle API — xAI 403 sans credits
17h09 — fix gem crush v31 anchor Presque — commit 1c4699fc
17h22 — commit+push aria-core 3e4a389e optimisation consommation Imagine/Groq
17h24 — sync-render 56 vars + redeploy optimisation consommation
17h30 — fix gem-crush v37 ancre cascade 37b49783 + pin 0470a8b
17h34 — gem-crush UI propre: plus de // visible, cadre candy, v39 prep
17h39 — gem-crush sprint assets v1: GemSprite LevelMap chute + brief v40-v42
17h41 — CONSOMMATION-GROK.md + regles handoff GitHub (collegue-memoire, aria-skills, aria-local-sync)
17h52 — fix ancre v41 gem_crush_premium.py + test (ad73fe1e)
17h52 — pin aria-core v41 fix aria-vanguard (7319f06)
17h52 — ARIA-WORKER gem-crush-error-v41 done
18h00 — aria-sandbox public temporaire 5min (gh repo edit)
18h10 — Phase A gem-crush: backlog + critic + dry-run + micro-ship aria-core
18h10 — aria-sandbox repasse PRIVATE (job 5min echoue)
18h17 — fix audit: parser yaml known_machines + registry PCDESS9 IP
18h20 — retire PCDESS9 trust + sessions + registry
18h31 — Phase 1 Tavily skill aria-core + tests
18h35 — revert Tavily Phase 1 aria-core (DDG seul)
18h37 — tous repos GoldenFarFR passes PUBLIC (analyse Grok)
18h44 — tous repos GoldenFarFR repasses PRIVATE
19h05 — maj HANDOFF.md contexte session + prochaines actions PC-SYLVAIN
19h05 — maj COLLEGUE.md decisions ARIA 2026-06-20 (mono-PC, Gem Crush, DDG)
19h06 — collect-session PC-SYLVAIN manifeste + HANDOFF enrichi
19h06 — push collegue-memoire ff586ad session handoff complet
19h04 — TOTP via agent Grok/Cursor (plus ARIA Telegram) — totp-gate, session-handoff, skills
19h07 — fix propagate TotpCode ensure-pc-ready + diag secret TOTP invalide PC-SYLVAIN
19h10 — regen TOTP setup-totp-vault PC-SYLVAIN (sans Bitwarden)
19h13 — handoff OK TOTP 351348 session Git 12h PC-SYLVAIN
19h24 — feat gem-crush releases illimitées aria-core a895d263 + pin vanguard 59d93f3 + sync-render
19h26 — feat gem-crush min 10 items/release aria-core 5c69ca85 pin 709dbba
19h37 — fix gem-crush cooldown 30min + floor interval aria-core 7a9e01e9
19h39 — fix curriculum cooldown 24h Telegram spam aria-core 72dc99d4
19h45 — feat qi auto_judge ouvrier+heartbeat aria-core 02f2e61d
19h46 — fix curriculum plus notify Telegram opérateur
19h52 — implémente qi_self_judge_shadow + calibration aria-sandbox 1a5e0e0c
19h56 — sync-render OK vars — deploy build_failed pipeline_minutes_exhausted Render
20h09 — politique build-local deploy-render CI PR-only pipeline guard
20h14 — Phase A doc aria-core ARCHITECTURE WHERE-TO-PUT README
20h17 — Phase B aria_core/memory package + vector stub c8a10583+
20h35 — Phase C1-C3 chroma_client store search aria-core
20h38 — Phase C4-C5 smoke OK ingest cognitive + test-vector-memory
20h39 — fin session — Phase C validée 67b28c3a, pause avant Phase D
20h43 — pause session — reprise Phase D prévue 2026-07-02 (A/B/C validées 67b28c3a)
20h46 — memoire reprise 2026-07-02 REPRISE-2026-07-02.md COLLEGUE HANDOFF SESSION-START
20h54 — Phase D llm_context vector recall aria-sandbox ffafe411
20h59 — git pull sandbox+vanguard + push JOURNAL collegue-memoire 0147c24
21h00 — commit push JOURNAL bb60c79 + retire PCDESS9 aria-local-sync 895f3d4
21h02 — handoff fin session HANDOFF 0d2db3e collect-session
21h05 — desactive TOTP Telegram bbfc827c d7ff2fc f06c0ca
21h09 — handoff OK TOTP session Git 12h PC-SYLVAIN
21h13 — test vector memory local OK + bump pin bbfc827 chromadb requirements
21h18 — fix test_truth_ledger isolation + build-local 337 pass deploy bloque quota Render
21h19 — active ARIA_VECTOR_MEMORY=true local.env vault + sync-local
21h21 — lance uvicorn local 127.0.0.1:8000 ARIA_VECTOR_MEMORY=true
21h29 — local ARIA mode operateur ARIA_PUBLIC_MODE=false ACCESS_CODE_ENABLED=false
21h34 — impl pont aria-cursor-bridge skill + script + jsonl
21h40 — deploy-vector-memory.ps1 pret — deploy Render bloque quota pipeline
21h48 — bridge tweet EN anglais Ollama OK + fix pont message seul
21h56 — valide tweet X EN + bridge ARIA (x compose bloque)
21h59 — fix x-compose prevalidated + media X aria-core
22h15 — Option A X prod env sync (likes/curiosity/mentions off)
22h16 — publie tweet X built-in-public + capture GitHub
22h25 — feat x_voice humain sans tics IA aria-core
22h28 — COLLEGUE+HANDOFF mono-PC PC-SYLVAIN seul

## 2026-07-01
22h20 — 22h20 — Mise a jour COLLEGUE.md + regles Grok monorepo ARIA
22h44 — 22h44 — fin session monorepo ARIA + collect/push handoff
22h54 — feat handoff tout — Phase E values + DDG cache + Gem Crush v43-45 d91c33e
23h03 — suppression Gem Crush monorepo local — pas de push
23h05 — Phase F goals aria_goals.yaml + memory/goals.py local
23h11 — commit Phase G reflection a970bd7 — 318 tests OK
23h11 — smoke test-vector-memory.ps1 vector=true OK post-G
23h18 — feat Phase H memory arbitrator — 326 tests OK
23h23 — vector local active — 33 docs Chroma, recall LLM OK
23h28 — optimize Ollama local qwen2.5:14b — PC 8Go VRAM
23h30 — git push origin main 5a52b07 — 10 commits (Gem Crush + memoire F-H + Ollama)
23h32 — fin session — collect/push handoff 92bf562
23h41 — Phase I deploy prod 20864ae aria_core 92bf562 repoint monorepo ARIA
23h41 — sync-render 60 vars ARIA_VECTOR_MEMORY=false prod safe
23h44 — activer ARIA_VECTOR_MEMORY=true prod — sync-render + redeploy dep-d92ojsc live
23h47 — fin session Phase I deploy + vector memory prod — collect/push handoff 8d97e00
23h49 — créé prompt ACP v2 + script prepare-acp-v2-integration.ps1
23h49 — menu interactif prepare-acp-v2-integration.ps1 (afficher/copier/bridge)
23h53 — intégration ACP v2 aria-core — provider/client skills + tests 7 OK
23h56 — fix acp_cli Windows (.cmd) + drain vide provider + smoke test local OK
23h57 — fix listener ACP legacy (v2 HTTP 500 Virtuals) + acp-events-listener.ps1

## 2026-07-02
00h01 — poll ACP activé bot local — health + chat acp status/cycle OK
00h04 — fin session — REPRISE-ACP-2026-07-02 handoff ACP v2 local validé, commit+PR à faire
00h15 — feat community_worker skill worker_delegate + ton commu chaleureux aria-core
00h16 — impl CommunityWelcomeBanner Vanguard test worker_delegate
00h32 — feat community-feedback form site + triage ARIA queue ouvrier
00h34 — refonte Vanguard minimal stylé FR hero+produit+aria+faq
00h36 — feat visitor-prefs bandeau commu memoire profil membre
00h38 — purge holding site Aria Market Privy Activer acces
00h43 — fix CORS 5174 + relance API feedback commu
00h50 — feat feedback commu: remontée ARIA-WORKER local + tweet X merci
00h52 — feat x_profile sync bio site nom @Aria_ZHC
00h54 — vitrine EN full + widget Google Translate stylé
00h55 — feat feedback X tweet toujours EN + traduction avis
00h56 — fix langue site: picker EN + Google opt-in visiteur
01h05 — sync tokens X Aria_ZHC vault + backend .env
01h06 — ajout import-secure-x-keys.ps1 + pitfall secrets-via-vault
01h14 — fix tweet commu skip_rate_gap + middleware feedback public
01h20 — refonte tweet commu citation exacte + reponse ciblee
01h21 — modération X avis commu + polish orthographe citation
01h33 — file X commu 4h fusion GoldenFarFR trusted + fix spam .com
01h36 — retrait lien Telegram trusted handles + push deploy commu
01h47 — deploy vitrine static rootDir vanguard + API test-1
01h58 — feat(vitrine) Privy login + push monorepo GitHub
02h02 — deploy vitrine Render Privy login ariavanguardzhc.com
02h06 — fix feedback cold-start Render warm health retry
02h13 — fix SSL api.ariavanguardzhc.com + redeploy vitrine feedback
02h20 — redeploy vitrine API canonique api.ariavanguardzhc.com (SSL OK)
07h55 — fin session collect+push manifest 5fb4b9a
20h56 — install letta-orchestrator ARIA v2.4 (Letta 0.6.7, agents OK, test orchestrate)
20h58 — fix start-letta.ps1 port 8283 déjà utilisé
21h24 — branche Letta profil PS + pont Cursor (aria-letta-integration)
22h26 — fix shell aria() détection questions Letta + /letta status
22h28 — shell aria() chat par défaut via Letta (Ollama sur /ollama)
22h31 — Letta sans 32b — tout qwen2.5:14b local + sync_models.py
22h44 — fix sync_models llm_config — Grok/Core passés 14b (plus 19 Go)
22h46 — fix start-letta.ps1 variable PID read-only → procId
22h49 — start-letta.ps1 arrière-plan par défaut (flood logs ADE)
22h50 — alias global start-letta depuis n'importe quel dossier PS
22h52 — fix start-letta Start-Process logs stdout/stderr séparés
22h55 — mode rapide ARIA_LETTA_FAST + commande /fast shell aria
22h57 — Letta perf: classif heuristique+, cascade intelligente, ctx 8k, Groq Grok/Core
23h16 — corrige letta-orchestrator routage identite + extraction Letta 0.6.7
23h29 — retire mention Doursat de COLLEGUE.md (correction Sylvain)
23h32 — vector memoire toujours actif profil + env User + DATA_DIR local.env
23h35 — shell aria-core cerveau vector COLLEGUE remplace Letta par defaut
23h38 — skill ingest-repo preuve cognitive+vector+rapport jsonl
23h46 — fix routage ingest force cerveau + banniere
23h52 — fix shell_chat init_repertoire_db agent_messages
23h57 — fix recall COLLEGUE sans recherche web epistemique

## 2026-07-03
00h02 — retrait contexte bureau DDC/Aptos COLLEGUE + truth-ledger
00h03 — supprime dossier A FAIRE (doublon letta-orchestrator)
00h12 — fix routage self_context identite/objectifs + socratic_drill
00h15 — retrait DDC/Excel du repo ARIA (socratic, local-sync, projets)
00h18 — fix self_context sans vector (objectifs ARIA lisibles)
00h29 — lot ACP v2 commit (acp_cli provider listener tests)
00h29 — fix acp_cli unwrap data JSON agent list
00h37 — fix start-acp-local.ps1 (logs, health, deps)
00h39 — lance start-acp-local.ps1 + push fix operator c52dce8
00h45 — sync nocturne GitHub: Telegram bandeau, IP monorepo, ARIA-WORKER 7 items, push main
00h46 — fix ensure-pc-ready monorepo SSOT (plus de clone projets/)
00h54 — import secure-keys -> vault + letta + sync-render (IMAGE_API_KEY GROK LLM Telegram X ACP)
14h57 — workflow download inbox + rejet 6 raccourcis Messenger
14h58 — commit push download inbox 815f5d4
15h01 — triage download truth-ledger-scaffold extensions corrigées
15h05 — fix truth-ledger-scaffold black+ruff+ci workflow
15h13 — fix CI ARIA scope projects/truth-ledger black+ruff
15h23 — aria-core truth_ledger/io.py atomic write + filelock
15h30 — regle build-local auto apres modif code (deploy Render manuel)
15h37 — Letta ARIA-Ouvrier agent + 8 outils copie conforme
15h41 — Letta ouvrier intention naturelle + confirmation si doute
16h55 — push boot auto ARIA local fe3739f
17h00 — suspend aria-api Render (runtime local PC)
17h04 — fix ouvrier preflight ping Telegram (preuve live)
17h09 — feat ouvrier preuve systematique vault+runtime (ouvrier_proof.py)
17h40 — feat aria-core ACP offering workflows (templates YAML, create/update CLI, tests 17/17)
17h47 — fix KART v2.8.1 trace silencieuse + bootstrap skip + condense quote tweet feedback
17h49 — feat ouvrier instant — salutations sans preflight/LLM (test_ouvrier_instant 6/6)
17h52 — fix KART v2.8.2 reponse › distincte de traces et preuve
17h57 — fix KART session continuation + preflight avis ACP workflow
17h59 — feat ACP product launch (schemas enrichis, promo X/Telegram, tests 20/20)
18h01 — feat ouvrier fallback grok KO vers groq puis ollama
18h09 — fix ouvrier ACP adhoc direct (plus pavé LLM sur créer workflow)
18h11 — feat ARIA_OUVRIER_CLOUD=groq vault local — ouvrier sans API xAI
18h11 — Création du workflow Test_1 dans ACP avec un prix de 25$ et 99 centimes, proposant un service d'analyse sur la pertinence d'un compte X avec promotion
18h21 — feat ACP adhoc premium + promo X auto (revenus)
18h23 — fix ACP delete workflow direct (ouvrier+brain) + supprime test_1
18h24 — fix tweet ACP policy + test_1 premium 25.99 USDC publié X
18h31 — fix ouvrier ACP direct + delete-all + repair premium YAML
18h38 — 18h38 — fix KART v2.8.6 vision images ouvrier (groq scout + session last_image_path)
18h44 — feat ACP schema.examples auto demande/livrable Virtuals
18h57 — Sprint 1 ouvrier : preflight build_llm_context+arbitre, anti-fallback Ollama, ouvrier_learn
19h03 — Sprint 2 sync-core-to-letta archival + collect-session
19h06 —  — ACP aria_full_access: 3 offres + subscription_ids + fix acp-cli node Windows
19h06 — Sprint 3 Letta-2 critique pending-lessons + collect-session
19h12 — apply leçon Anti-Ollama fallback (exemple) → reflection
19h12 — commit Sprint 4 apply pending-lessons (13 tests, live apply OK)
19h15 — 19h15 — ARIA v3.0 KART unifiee cerveau+ouvrier+ACP client trade/fund
19h17 — memoire: auto ApplyApproved collect-session + /apply-lessons KART + docs COLLEGUE
19h39 — refonte tweet feedback commu format+reponses roadmap
19h42 — confirme policy feedback X EN + correction typos visiteur
19h47 — feat feedback X fil 2 tweets citation+reply developpee
20h40 — fix feedback-x sans lien + citation fidele + deploy prod
21h12 — feat aria-core llm_economy brief/standard/develop + brain integration
21h26 — feat response_cost footer gratuit/payant + tokens
21h38 — feat mode debranchement Grok coding KART (ouvrier_coding_mode + skip cerveau)
21h45 — feat compteur tokens payants dashboard KART + ouvrier record usage
21h47 — fix Get-AriaKartPaidTokens python sans aria_core import
21h49 — fix profil PS hook ARIA avant dash compteur tokens
21h54 — fix routage ACP question conversationnelle revenus (acp_client_skill)
21h57 — coupe notifs Telegram repertoire_grow + entrepreneur_cultivate heartbeat
21h58 — playbook activation revenu entrepreneur_skill + proactive ACP
21h59 — heartbeat 24h + persist last_runs disque (anti-spam redeploy)
22h05 — ACP market intelligence skill + proactive ON + heartbeat scan
22h07 — audit GitHub local points 1-8 : ACP commit, audit monorepo, IP, stashes, legacy archive, session collect
22h14 — commandes locales console + market intelligence ACP commit 48edb29
22h23 — deploy prod audit GitHub — commit 2a7f715, test drain_events_file fix
22h34 — push lot ACP market intel + entrepreneur + session
22h37 — fix bootstrap console: charge cles X vault pour promo ACP
