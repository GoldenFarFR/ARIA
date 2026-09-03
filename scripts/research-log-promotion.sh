#!/usr/bin/env bash
# research-log-promotion.sh -- relit le journal de veille continue et
# promeut les trouvailles actionnables dans le backlog ARIA (CLAUDE.md /
# docs/aria-learning-inbox/). Decision operateur explicite (18/07). Voir
# CLAUDE.md "Automatismes en place".
#
# Remplace une tentative initiale via le mecanisme de "Routine" de l'app
# desktop -- abandonnee car ce mecanisme ne peut pas atteindre le
# filesystem du VPS (confirme par test operateur : fonctionne sur un
# dossier local du PC, echoue systematiquement sur /opt/aria, case
# "Worktree" verrouillee -- signe qu'il ne peut meme pas y creer un
# worktree la-bas). Meme patron que scripts/devils-advocate-review.sh et
# /opt/aria-data/research-loop/run.sh : cron VPS reel, independant de
# toute app/PC ouvert.
#
# Outils volontairement plus larges que la veille continue (Read/Write/
# Edit/WebSearch/WebFetch + Bash SCOPE A "git *" uniquement -- jamais un
# shell arbitraire) car cette tache doit committer/pousser sur main. Le
# blast radius reste borne : Bash ne peut executer QUE des sous-commandes
# git (aucun rm/curl-exfiltration/execution de code arbitraire possible
# meme en cas d'injection de prompt via un contenu du journal), et la
# protection de branche GitHub (force-push/suppression bloques pour tout
# le monde, cf. CLAUDE.md 18/07) reste un filet meme si Bash tentait un
# git push --force.
set -uo pipefail

# Backlog #275 (10/08, CVE-2026-22708 audit) -- Bash(git *) already stops the
# agent from ever successfully running a PATH/LD_PRELOAD-poisoning command
# itself (Claude Code matches the FULL command, each chained sub-command must
# independently match "git *" -- confirmed against the official permissions
# doc, not assumed). The one residual gap the audit found: that scoping does
# NOT isolate the shell environment across calls, so an ALREADY-poisoned
# PATH/LD_PRELOAD (from an unrelated host-level compromise, never from this
# agent's own allowed actions) would still be inherited. Cron's own ambient
# env is already minimal (no .bashrc/.profile sourced), so this is
# defense-in-depth against a scenario outside this script's own reach, not a
# fix for a live exploit -- pinned explicitly rather than trusting whatever
# environment cron happens to hand this script.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset LD_PRELOAD LD_LIBRARY_PATH

RUN_LOG="/opt/aria-data/promotion-loop/run.log"
mkdir -p /opt/aria-data/promotion-loop
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- demarrage passage promotion ===" >> "$RUN_LOG"

cd /opt/aria || { echo "cd /opt/aria a echoue" >> "$RUN_LOG"; exit 1; }

# --- Garde-fou de budget CLAUDE.md (03/09, apres incident reel) --------------
# Le 03/09 ce passage a ajoute 8 entrees de ~500 caracteres directement dans
# CLAUDE.md, qui a franchi son plafond CI de 100 Ko et bloque le push de trois
# sessions. Le prompt a ete corrige pour router le detail vers docs/, mais une
# INSTRUCTION DANS UN PROMPT N'EST PAS UNE GARANTIE : on mesure donc avant et
# apres, mecaniquement.
#
# Ce garde-fou ne REPARE jamais le fichier lui-meme -- un "git checkout
# CLAUDE.md" ecraserait le travail d'une session soeur en cours (il y en avait
# une ce jour-la). Il detecte, signale, et sort en non-zero. La reparation
# reste une decision humaine.
CLAUDE_MD_BUDGET=102400
CLAUDE_MD_SIZE_BEFORE=$(stat -c %s /opt/aria/CLAUDE.md 2>/dev/null || echo 0)
CLAUDE_MD_HEADROOM=$(( CLAUDE_MD_BUDGET - CLAUDE_MD_SIZE_BEFORE ))
echo "budget CLAUDE.md : ${CLAUDE_MD_SIZE_BEFORE}/${CLAUDE_MD_BUDGET} octets, marge ${CLAUDE_MD_HEADROOM}" >> "$RUN_LOG"

PROMPT=$(cat <<'PROMPT_EOF'
Tu es la session commandement du projet ARIA, qui tourne directement sur le
VPS de production (/opt/aria, repo git GoldenFarFR/ARIA). Contexte : un cron
VPS independant (voir /opt/aria/CLAUDE.md, section "Automatismes en place")
fait tourner une veille de recherche toutes les 3h qui accumule des
trouvailles dans un fichier journal. Ta tache aujourd'hui : relire ce
journal et decider, avec un vrai jugement critique (jamais une promotion
automatique/aveugle), ce qui merite de rejoindre le plan de travail reel du
projet.

ETAPES :

1. Lis /opt/aria/CLAUDE.md en entier d'abord (fichier de contexte du
   projet -- priorites actuelles, garde-fous, backlog deja connu). Cherche
   en particulier la derniere section "Session ..." ou "recap" pour
   connaitre le contexte le plus recent.

2. Lis /opt/aria-data/research-loop/research-log.md en entier. En tete du
   fichier, cherche une ligne HTML commentee du type
   "<!-- promotion: traite jusqu'au AAAA-MM-JJTHH:MM:SSZ -->" (si absente,
   traite tout le fichier comme non-traite). Ne considere que les entrees
   posterieures a ce marqueur.

3. Pour CHAQUE entree non traitee, juge honnetement si elle est reellement
   actionnable MAINTENANT (pas juste "interessante") : un vrai gap
   technique confirme dans le code d'ARIA, une vraie opportunite
   verifiable (nouvel outil/API/protocole avec un usage concret pour
   ARIA), ou un vrai risque de securite/qualite a corriger. Si tu as un
   doute sur un fait affirme dans l'entree, verifie-le (WebSearch) avant
   de le prendre pour argent comptant -- ne fais jamais confiance
   aveuglement, applique la meme rigueur que pour toute revue croisee
   externe (Gemini/ChatGPT) deja pratiquee dans ce projet.

4. Pour ce qui est reellement actionnable. ROUTAGE OBLIGATOIRE, corrige le
   03/09 apres un incident reel : le detail va TOUJOURS dans docs/, jamais
   dans CLAUDE.md. Le 03/09 ce script a ajoute 8 entrees de ~500 caracteres
   directement dans CLAUDE.md, qui a franchi son plafond CI de 100 Ko et a
   bloque le push de trois sessions. CLAUDE.md est un ROUTEUR : il dit ce
   qui existe, jamais le detail.
   - Point simple et clair (correction ciblee, piste de config) : ecris
     l'entree COMPLETE (source, action dev precise) dans
     /opt/aria/docs/backlog-technique.md. Utilise le prochain numero #N
     disponible (cherche le plus grand #N deja utilise, dans les deux
     fichiers). Dans CLAUDE.md, AU PLUS une ligne d'index de 100
     CARACTERES MAXIMUM au format "- #N (JJ/MM) — <sujet en 6 mots>.
     Detail: `docs/backlog-technique.md`." Si plusieurs entrees sortent
     du meme passage, REGROUPE-LES en une seule ligne d'index
     ("- #N..#M (JJ/MM, detail `docs/backlog-technique.md`) — sujet1 ;
     sujet2 ; ..."). Verifie que le detail existe REELLEMENT dans
     backlog-technique.md avant d'ecrire le pointeur : un pointeur vers
     un detail absent est un mensonge, et il y en a deja eu.
   - Sujet meritant d'etre creuse en profondeur avant d'etre actionnable :
     cree une fiche /opt/aria/docs/aria-learning-inbox/AAAA-MM-JJ-sujet.md
     (lis-en une ou deux existantes pour le format), et ajoute dans
     CLAUDE.md une ligne d'index de 100 caracteres maximum pointant vers
     cette fiche.
   - BUDGET : avant d'ecrire dans CLAUDE.md, mesure sa taille
     ("stat -c %s /opt/aria/CLAUDE.md"). Le plafond CI est 102400 octets.
     Si ton ajout ferait franchir ce plafond, N'ECRIS RIEN dans CLAUDE.md :
     mets tout dans docs/, et signale-le explicitement dans ton rapport
     final. Un backlog complet dans docs/ sans ligne d'index vaut
     infiniment mieux qu'une CI rouge.
   - N'INTEGRE JAMAIS directement dans du CODE ou dans un fichier de
     garde-fou (permission_mode, wallet_guard, regles-uniques,
     config.toml, tout .env). Cette tache se limite a la
     documentation/planification. Une piste qui exigerait un changement
     de code va dans le backlog CLAUDE.md comme item a faire PLUS TARD par
     une session de developpement, jamais codee par toi ici.

5. Pour ce qui n'est PAS actionnable maintenant (trop speculatif, deja
   couvert ailleurs dans CLAUDE.md, hors sujet, ou toucherait un
   garde-fou/capital reel/secret/auto-modification du systeme -- ces
   dernieres sont ecartees sans meme etre discutees) : ne rien ajouter au
   backlog, mais considere quand meme l'entree comme traitee.

6. Mets a jour le marqueur "<!-- promotion: traite jusqu'au ... -->" en
   tete de /opt/aria-data/research-loop/research-log.md avec l'horodatage
   actuel (ce fichier N'EST PAS suivi par git -- modifie-le juste sur
   disque).

7. Si tu as modifie CLAUDE.md et/ou ajoute des fichiers dans
   docs/ : verifie D'ABORD que CLAUDE.md tient sous son plafond
   ("stat -c %s /opt/aria/CLAUDE.md" doit rendre moins de 102400) -- si
   non, retire tes lignes d'index de CLAUDE.md avant toute autre chose,
   le detail dans docs/ suffit. Puis relis CLAUDE.md INTEGRALEMENT apres ta
   modification pour verifier la coherence (norme absolue du projet,
   section "Regles absolues"), puis commit et push directement sur main
   (tu as l'autorite de commit etablie en tant que session commandement)
   avec un message clair citant les numeros de backlog ajoutes. Utilise
   toujours "git push origin main:main" (jamais "git push origin
   <branche>" seul) et revrifie apres coup via "git fetch origin main &&
   git show origin/main:CLAUDE.md | tail" que le push a bien atterri.
   AVANT de commit : lance "git log --oneline -3" toi-meme. Si tu vois un
   commit que tu ne te souviens pas avoir fait, ce n'est PAS forcement une
   "execution concurrente" -- c'est plus probablement toi-meme, plus tot
   dans cette meme session (ce fichier est long, une compression de
   contexte a pu te faire perdre le fil de tes propres actions). Verifie
   le contenu de ce commit avant de continuer, ne duplique jamais un
   travail deja fait, et ne rejette/ne re-promeus jamais un item que TU AS
   DEJA TRANCHE plus tot dans cette meme session sans relire d'abord ta
   propre decision precedente dans ce commit.

8. Si RIEN de nouveau/actionable n'a ete trouve ce passage : ne fais aucun
   commit (pas de commit vide), mets quand meme a jour le marqueur de
   progression dans research-log.md.

Termine ta reponse par un resume court : combien d'entrees du journal ont
ete traitees, combien promues (avec leurs numeros de backlog), combien
ecartees et pourquoi (bref).
PROMPT_EOF
)

# #311 (16/08): per-run cost ceiling -- real CLI flag, verified live against
# `claude --help` (a "--max-turns" flag does not exist in this CLI;
# --max-budget-usd is the real mechanism, "only works with --print"). Same
# economic-denial-of-wallet guard as research-loop/run.sh's own cron. $3
# (slightly above research-loop's $2: this pass can judge several log
# entries, each with its own optional verification WebSearch, plus a git
# commit) -- calibrate down once real per-pass cost is observed.
claude -p "$PROMPT" \
  --model sonnet \
  --effort high \
  --allowedTools "Read Write Edit WebSearch WebFetch Bash(git *)" \
  --disallowedTools "Agent Task" \
  --no-session-persistence \
  --max-budget-usd 3 \
  --add-dir /opt/aria-data/research-loop \
  -n research-log-promotion \
  >> "$RUN_LOG" 2>&1

# --- Verification mecanique du budget, apres coup ---------------------------
CLAUDE_MD_SIZE_AFTER=$(stat -c %s /opt/aria/CLAUDE.md 2>/dev/null || echo 0)
echo "CLAUDE.md apres passage : ${CLAUDE_MD_SIZE_AFTER} octets (avant ${CLAUDE_MD_SIZE_BEFORE})" >> "$RUN_LOG"

if [ "$CLAUDE_MD_SIZE_AFTER" -gt "$CLAUDE_MD_BUDGET" ]; then
  OVER=$(( CLAUDE_MD_SIZE_AFTER - CLAUDE_MD_BUDGET ))
  echo "ECHEC BUDGET : CLAUDE.md depasse de ${OVER} octets -- la CI va refuser le push" >> "$RUN_LOG"
  DB="/opt/aria-data/aria.db"
  if [ -f "$DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    DETAIL="CLAUDE.md fait ${CLAUDE_MD_SIZE_AFTER} octets pour un plafond CI de ${CLAUDE_MD_BUDGET} (depassement ${OVER}). Le passage de promotion a ecrit dans CLAUDE.md au lieu de router le detail vers docs/backlog-technique.md. Le fichier n a PAS ete repare automatiquement : une session soeur peut y travailler. Action : deplacer le detail vers docs/, ne garder qu une ligne d index de 100 caracteres maximum."
    sqlite3 -cmd ".timeout 5000" "$DB" \
      "INSERT INTO system_issues (source, title, detail, severity, status, dedup_key, opened_at)
       SELECT 'research-log-promotion', 'Promotion : CLAUDE.md a franchi son plafond CI', '${DETAIL}', 'critical', 'open', 'promotion-claude-md-budget', '${NOW_ISO}'
       WHERE NOT EXISTS (SELECT 1 FROM system_issues WHERE dedup_key='promotion-claude-md-budget' AND status='open');" 2>/dev/null
  fi
  exit 2
fi
EXIT_CODE=$?

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- fin passage (exit ${EXIT_CODE}) ===" >> "$RUN_LOG"

if [ -f "$RUN_LOG" ] && [ "$(stat -c%s "$RUN_LOG" 2>/dev/null || echo 0)" -gt 5000000 ]; then
  tail -n 2000 "$RUN_LOG" > "$RUN_LOG.tmp" && mv "$RUN_LOG.tmp" "$RUN_LOG"
fi
