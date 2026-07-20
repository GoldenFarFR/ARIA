# Runbook — migration VPS (checklist + pièges déjà rencontrés)

> Fichier **public** (repo `GoldenFarFR/ARIA`) : jamais d'IP/secret réels ici, même en
> exemple — toujours des placeholders (`<ANCIENNE_IP>`/`<NOUVELLE_IP>`). Détails privés
> (IP réelles, accès) dans `aria-ops`.

À lire **avant** toute migration de VPS (changement de machine physique — pas un simple
redéploiement sur le même serveur, cf. `docs/deploy-rollback-blue-green.md` pour ça).
Écrit après la migration du 20/07 — objectif : ne pas retomber dans les mêmes pièges la
prochaine fois.

## Checklist dans l'ordre (ce qui marche)

**Phase 1 — préparation, aucun risque, aucune dépendance au DNS**
1. Inventaire complet de l'ancien serveur AVANT de commencer : versions Docker/nginx/
   Node/Claude Code CLI, contenu exact de `crontab -l`, services systemd custom
   (`systemctl list-units | grep aria`), règles ufw, jails fail2ban, `docker ps`.
   Répliquer les mêmes versions sur le nouveau plutôt que d'installer "latest" à
   l'aveugle.
2. Installer les paquets de base sur le nouveau serveur (Docker via le script officiel
   `get.docker.com`, Node via NodeSource, Claude Code CLI via npm, ufw/fail2ban).
3. ufw : `limit 22/tcp` (jamais `allow` seul) + `allow 80,443/tcp` + `--force enable` —
   **toujours retester une connexion SSH juste après** avant de continuer (vérifier
   qu'on ne s'est pas coupé l'accès).
4. fail2ban : jail `sshd` par défaut suffit, `systemctl enable --now`.
5. `rsync -az --delete` le repo git complet (`.git` inclus) vers le nouveau serveur, en
   excluant **explicitement** `.venv`, `node_modules`, `.claude/worktrees` (orphelins,
   jamais utiles), `__pycache__`, `.pytest_cache` — ces dossiers se régénèrent
   (`npm install`/le hook SessionStart pour `.venv`), inutile de les copier (gain de
   temps et d'espace disque significatif, plusieurs Go sur ce projet).
6. `rsync -az` le répertoire de données persistantes (SQLite, mémoire vectorielle,
   etc.) séparément — cette copie sera **légèrement périmée** si l'ancien serveur tourne
   encore en prod pendant la copie (écritures continues) : normal, prévoir un dernier
   delta-sync juste avant la bascule finale si le premier sync date de plus de
   quelques minutes.
7. Copier le fichier `.env` de secrets **directement serveur à serveur** (`rsync`/`scp`,
   jamais `cat`/afficher le contenu dans le terminal ou dans une session Claude Code) —
   vérifier après coup uniquement la taille/le nombre de lignes, jamais le contenu.
8. **Ne pas oublier le build statique séparé du frontend** (`/var/www/<domaine>` ou
   équivalent, généré par le script de déploiement dédié au frontend, PAS par le script
   de déploiement backend) — c'est un dossier hors du repo git ET hors du répertoire de
   données, facile à complètement oublier. Vérifier `du -sh` sur l'ancien serveur pour
   confirmer qu'il existe avant de considérer la migration de données "complète".
9. Copier les clés de déploiement Git (SSH) + le `~/.ssh/config` qui les mappe aux
   alias d'hôte GitHub. **Bootstrap obligatoire sur un serveur neuf** : le premier
   `git fetch`/`pull` échoue avec "Host key verification failed" tant que la clé
   d'hôte GitHub n'est pas ajoutée (`ssh-keyscan -t ed25519 github.com >>
   ~/.ssh/known_hosts`) — normal, pas un bug.
10. Build + lancement **manuel** (pas le script de déploiement blue-green habituel) du
    conteneur applicatif sur le nouveau serveur, lié à `127.0.0.1` uniquement (jamais
    `0.0.0.0`), vérifié via un `curl` local direct (`127.0.0.1:<port>/health`) — voir
    "Piège n°1" ci-dessous pour pourquoi le script de déploiement normal ne peut PAS
    encore tourner à ce stade.
11. Répliquer les crontabs (veille, promotion, surveillance) et les activer sur le
    nouveau serveur **en les désactivant simultanément sur l'ancien** — vérifier D'ABORD
    que ces scripts appellent le domaine public ou opèrent en local (donc indifférents
    à quel serveur les exécute) plutôt que de supposer que c'est sans risque. Ne
    JAMAIS les laisser actifs sur les deux en même temps (double travail, doublons/
    conflits sur les fichiers ou les commits git qu'ils produisent).
12. Copier les configs nginx (sites + upstream) pour référence, **sans les activer** —
    voir "Piège n°1".

**Phase 2 — bascule, dépend du feu vert opérateur**
13. Changement DNS chez le fournisseur (fait par l'opérateur lui-même, jamais un accès
    API qu'une session Claude Code posséderait par défaut).
14. **Attendre la convergence sur les serveurs faisant autorité** (interroger
    explicitement chaque nameserver du domaine, `dig @<nameserver>`, pas seulement le
    résolveur par défaut) avant de considérer le DNS "propagé" — voir "Piège n°4".
15. Réémission des certificats SSL (`certbot certonly --standalone`, après avoir
    arrêté nginx le temps du défi HTTP) — voir "Piège n°2" pour les fichiers
    additionnels que ce mode ne génère pas automatiquement.
16. Activer les sites nginx (symlink `sites-available` → `sites-enabled`),
    `nginx -t`, redémarrer.
17. **Vérifier le trafic réel en forçant explicitement la nouvelle IP**
    (`curl --resolve <domaine>:443:<NOUVELLE_IP> https://<domaine>/...`), jamais un
    simple `curl https://<domaine>/...` sans `--resolve` — voir "Piège n°5".
18. Une fois confirmé sain : **arrêter** (`docker stop`, jamais `docker rm`) les
    conteneurs applicatifs de l'ancien serveur — sinon les deux tournent en parallèle
    sur le même bot Telegram / la même boucle de décision, doublons garantis. L'ancien
    serveur reste up (SSH, données, prêt à redémarrer en une commande) le temps de la
    fenêtre de sécurité voulue par l'opérateur avant décommission définitive.

## Pièges rencontrés (20/07) — cause précise, pas juste le symptôme

**Piège n°1 — le script de déploiement blue-green habituel ne peut PAS tourner tel
quel sur un serveur tout neuf.** Sa dernière étape vérifie le trafic RÉEL à travers
nginx (via le nom de domaine) — qui échoue tant que (a) le site nginx applicatif n'est
pas activé et (b) les certificats SSL n'existent pas encore. Sur un serveur neuf, la
séquence correcte est un bootstrap MANUEL (build + `docker run` direct, healthcheck en
local) avant de pouvoir réutiliser le script normal pour les déploiements suivants.

**Piège n°2 — `certbot certonly --standalone` ne génère PAS les fichiers de config TLS
partagés que `certbot --nginx` (mode interactif complet) crée automatiquement**
(`options-ssl-nginx.conf`, `ssl-dhparams.pem`) — si les configs nginx copiées de
l'ancien serveur y font référence (`include .../options-ssl-nginx.conf;`,
`ssl_dhparam .../ssl-dhparams.pem;`), `nginx -t` échoue avec "No such file or
directory". Corrigé en recréant ces deux fichiers à la main (contenu TLS standard
moderne + `openssl dhparam -out ... 2048`) — pas des secrets, juste des paramètres de
sécurité standards, sûrs à régénérer.

**Piège n°3 — l'upstream blue-green (port alterné 8000/8001) copié de l'ancien serveur
peut ne PAS correspondre au port réellement utilisé par le bootstrap manuel du nouveau
serveur.** Le fichier upstream de l'ancien serveur reflète son état de bascule le plus
récent (ex. `8001` s'il vient d'y basculer), alors qu'un premier bootstrap manuel sur
le nouveau serveur démarre logiquement sur `8000` (le port par défaut du template).
Résultat : `502 Bad Gateway` jusqu'à correction manuelle du fichier upstream pour
qu'il pointe vers le port RÉELLEMENT utilisé par le conteneur qui tourne. Toujours
vérifier `docker ps` (colonne PORTS) contre le contenu du fichier upstream avant
d'activer le site.

**Piège n°4 — la propagation DNS chez un hébergeur à infrastructure anycast (plusieurs
machines physiques derrière un même nom de serveur de noms) n'est pas atomique.**
Interroger deux fois de suite le MÊME nom de nameserver peut donner deux réponses
différentes pendant quelques minutes (le cluster de machines qui répond à ce nom
converge progressivement). Ce n'est pas un bug — attendre et re-vérifier (boucle de
quelques tentatives espacées de ~20s) jusqu'à convergence complète sur TOUS les
nameservers du domaine avant de considérer le DNS "prêt" pour la suite (certbot).

**Piège n°5 — le résolveur DNS local d'une session peut donner un faux résultat de
vérification post-bascule, même après convergence complète côté serveurs faisant
autorité.** Un `curl https://<domaine>/...` lancé DEPUIS L'ANCIEN SERVEUR juste après
la bascule peut encore résoudre vers l'ancienne IP (cache local, TTL pas encore
expiré sur CE résolveur précis) — donnant l'illusion trompeuse que la bascule a
échoué ou n'a rien changé (observé concrètement : un `curl` a renvoyé le commit et le
certificat de l'ANCIEN serveur alors que les nameservers faisant autorité étaient
déjà à jour). Toujours vérifier avec `curl --resolve <domaine>:443:<IP-CIBLE>
https://<domaine>/...` pour forcer la connexion vers le serveur qu'on veut réellement
tester, indépendamment de ce que dit un cache DNS local.

**Piège n°6 (mineur, spécifique IONOS mais généralisable)** : le panneau du
fournisseur DNS peut proposer SON PROPRE produit SSL ("Activer" à côté de "Certificat
SSL") — complètement indépendant et potentiellement en conflit avec une gestion
Let's Encrypt/certbot directement sur le serveur. Ne jamais l'activer si le
certificat est déjà géré côté serveur. Vérifier aussi la présence d'un verrou
"Protection de domaine" qui peut bloquer l'édition des enregistrements DNS avant de
chercher longtemps pourquoi un changement ne prend pas.

## Après la bascule : ce qui reste (décommission différée)

L'ancien serveur reste volontairement UP plusieurs jours après une migration (filet de
sécurité) — checklist avant décommission définitive, à ne faire qu'une fois ce délai
passé et le nouveau serveur confirmé stable dans la durée (pas seulement au moment de
la bascule) :
- Aucune référence à l'ancienne IP qui traîne (alias SSH locaux de l'opérateur,
  configs `~/.ssh/config` sur chaque poste, éventuels webhooks/intégrations tierces).
- Dernière sauvegarde du répertoire de données de l'ancien serveur, même si déjà
  migré (filet de sécurité supplémentaire, jamais un coût élevé).
- Confirmation opérateur explicite avant toute action destructive sur l'ancien
  serveur (arrêt définitif, suppression) — jamais une décision automatique.
