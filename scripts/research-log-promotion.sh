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

RUN_LOG="/opt/aria-data/promotion-loop/run.log"
mkdir -p /opt/aria-data/promotion-loop
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- demarrage passage promotion ===" >> "$RUN_LOG"

cd /opt/aria || { echo "cd /opt/aria a echoue" >> "$RUN_LOG"; exit 1; }

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

4. Pour ce qui est reellement actionnable :
   - Point simple et clair (correction ciblee, piste de config) : ajoute
     un nouveau bullet numerote au backlog dans CLAUDE.md, style et
     emplacement coherents avec le reste du fichier. Utilise le prochain
     numero #N disponible (cherche le plus grand #N deja utilise).
   - Sujet meritant d'etre creuse en profondeur avant d'etre actionnable :
     cree une fiche /opt/aria/docs/aria-learning-inbox/AAAA-MM-JJ-sujet.md
     (lis-en une ou deux existantes pour le format), et ajoute une ligne
     dans CLAUDE.md pointant vers cette fiche.
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
   docs/aria-learning-inbox/ : relis CLAUDE.md INTEGRALEMENT apres ta
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

claude -p "$PROMPT" \
  --model sonnet \
  --effort high \
  --allowedTools "Read Write Edit WebSearch WebFetch Bash(git *)" \
  --disallowedTools "Agent Task" \
  --no-session-persistence \
  --add-dir /opt/aria-data/research-loop \
  -n research-log-promotion \
  >> "$RUN_LOG" 2>&1
EXIT_CODE=$?

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- fin passage (exit ${EXIT_CODE}) ===" >> "$RUN_LOG"

if [ -f "$RUN_LOG" ] && [ "$(stat -c%s "$RUN_LOG" 2>/dev/null || echo 0)" -gt 5000000 ]; then
  tail -n 2000 "$RUN_LOG" > "$RUN_LOG.tmp" && mv "$RUN_LOG.tmp" "$RUN_LOG"
fi
