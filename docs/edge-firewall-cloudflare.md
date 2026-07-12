# Pare-feu edge Cloudflare + durcissement anti-bot (#22)

Objectif : protéger `ariavanguardzhc.com` (vitrine) et `api.ariavanguardzhc.com` (API
publique) contre le scraping/bot abusif, sans dégrader l'expérience des vrais visiteurs.

## État constaté (12/07, VPS)

- Les deux domaines résolvent **directement** vers l'IP du VPS (`31.70.114.74`), en A
  record simple — **aucun proxy Cloudflare devant** (pas de nuage orange). Le TLS est
  terminé sur le VPS lui-même par nginx + certbot (Let's Encrypt), pas par Cloudflare.
- Aucun token API Cloudflare dans l'environnement du VPS (`vanguard/backend/.env`) — ce
  dépôt ne peut donc PAS piloter Cloudflare par API (Terraform/wrangler/etc. hors de
  portée sans que l'opérateur fournisse un token dédié, scope minimal : DNS + WAF sur la
  zone concernée, jamais un token "Global API Key").
- Conséquence : le volet **edge** de #22 ne peut pas être codé depuis ce dépôt — il exige
  une action côté opérateur dans le dashboard Cloudflare (ou un token à fournir). Ce
  document sert de checklist pour cette action ; le volet **applicatif** (filet de
  rate-limit complémentaire) a été implémenté séparément et ne dépend de rien ci-dessous
  (cf. [Volet applicatif déjà en place](#volet-applicatif-déjà-en-place-indépendant-de-cloudflare)).
- Découverte annexe (hors #22) : la vitrine (`ariavanguardzhc.com`) est actuellement
  derrière une Basic Auth nginx (`auth_basic_user_file /etc/nginx/.htpasswd-vitrine`)
  configurée à la main sur le VPS, absente de `vanguard/nginx/vitrine.conf` en dépôt —
  probablement une protection pré-lancement délibérée. Non touché ici ; à garder en tête
  si la mise sous Cloudflare change la façon dont ce blocage doit être géré (Basic Auth +
  Cloudflare proxy sont compatibles, aucune action requise de ce côté).

## Ce qui est requis côté opérateur AVANT tout code

1. **Ajouter la zone `ariavanguardzhc.com` à Cloudflare** (compte existant ou nouveau) —
   dashboard Cloudflare, "Add a site".
2. **Changer les nameservers du domaine** chez le registrar vers ceux fournis par
   Cloudflare (seule méthode qui active le WAF/rate-limiting Cloudflare pour un domaine
   entier — un simple CNAME "orange cloud" partiel ne suffit pas pour l'apex).
3. **Recréer les records DNS** dans Cloudflare, en **proxifié** (nuage orange, pas gris) :
   - `A ariavanguardzhc.com -> 31.70.114.74` (proxifié)
   - `A www.ariavanguardzhc.com -> 31.70.114.74` (proxifié) — actuellement absent du DNS,
     à créer si `www` doit rester servi (le nginx vitrine gère déjà `www` en
     `server_name`, seul le DNS manque).
   - `A api.ariavanguardzhc.com -> 31.70.114.74` (proxifié).
4. **Mode SSL/TLS : "Full (strict)"** — Cloudflare vérifie le certificat Let's Encrypt
   existant côté origine (déjà en place via certbot, rien à changer côté VPS). Ne JAMAIS
   utiliser "Flexible" (romprait le chiffrement bout-en-bout et exposerait le trafic
   Cloudflare→VPS en clair).
5. **Restreindre l'origine au trafic Cloudflare uniquement**, une fois le proxy actif et
   vérifié stable (sinon un bot continue de scraper direct sur `31.70.114.74` en
   contournant totalement le WAF) :
   - `ufw allow from <plages IP Cloudflare> to any port 443,80 proto tcp` (liste officielle
     : https://www.cloudflare.com/ips/ — à récupérer au moment de l'appliquer, elle change
     occasionnellement) et bloquer le reste sur 80/443.
   - Alternative plus robuste dans la durée : `Cloudflare Authenticated Origin Pulls`
     (mTLS entre Cloudflare et nginx) — évite de maintenir une liste d'IP à la main.

## Règles à activer une fois le proxy actif

- **Bot Fight Mode** (gratuit) ou **Super Bot Fight Mode** (plan payant) — bloque les bots
  connus/non déclarés sans challenge visible pour un humain normal.
- **WAF managé** (règles OWASP de base) — gratuit sur tous les plans Cloudflare depuis
  2023.
- **Rate limiting rules** (Cloudflare, pas applicatif) ciblées sur les endpoints les plus
  coûteux/scrapables, en COMPLÉMENT du filet applicatif déjà en place (cf. plus bas) —
  utile car un plafond edge bloque une IP AVANT qu'elle n'atteigne le VPS du tout :
  - `api.ariavanguardzhc.com/api/aria/content/*`, `/api/aria/track-record`,
    `/api/aria/relay/recent` : ~30 req/min par IP.
  - `api.ariavanguardzhc.com/api/aria/chat` : ne PAS dupliquer un plafond serré ici —
    consigne opérateur explicite (#22) de ne rien ajouter de plus sur le chat ; si une
    règle edge est mise en place, la caler large (ex. 60/min) pour ne jamais gêner un
    visiteur légitime avant le plafond applicatif existant (40/h par visiteur).
- **Challenge léger (Turnstile)** — à réserver aux endpoints publics à fort coût
  (`/api/aria/community-feedback`, formulaires) si le scraping persiste malgré le WAF +
  rate limiting edge. Ne jamais le poser sur `/api/aria/chat` (consigne opérateur, #22) :
  un challenge sur le point d'entrée principal de la vitrine casserait l'expérience du
  premier contact.
- **Cache statique** : `/assets/*` (bundle Vite) peut être mis en cache agressif côté
  Cloudflare (immutable, hashé) — réduit la charge origine indépendamment de l'anti-bot.

## Volet applicatif déjà en place (indépendant de Cloudflare)

En attendant (ou en complément de) la bascule DNS ci-dessus, un filet anti-scraping
applicatif tourne déjà côté VPS/FastAPI, implémenté dans le même lot que ce document :
`PublicRateLimitMiddleware` (`vanguard/backend/app/auth/middleware.py`) — plafond par IP
partagé sur tous les endpoints `/api/` publics (visiteurs anonymes, sans session Privy)
qui n'avaient jusqu'ici aucune limite (`content/faq`, `holding`, `track-record`,
`exam-status`, `sepolia-status`, `relay/recent`, `/api/health`, `/api/pulse`,
`arena-signal/btc`...). Le chat et `community-feedback` gardent leurs propres limiteurs
existants (par visiteur + par IP) et sont exemptés de ce plafond générique.

Ce filet reste utile même une fois Cloudflare en place : il protège contre un bot qui
contournerait l'edge (IP source légitime réutilisée, faille de config edge, panne
Cloudflare) — défense en profondeur, pas un doublon.
