# Déploiement ARIA — VPS IONOS

Hôte de production : **VPS IONOS `root@31.70.114.74`**.
Déploiement **manuel** : image Docker construite depuis le monorepo `GoldenFarFR/ARIA`.

- Domaine public : `api.ariavanguardzhc.com` (DNS géré chez IONOS → VPS).
- Conteneur : `aria-api` (image locale `aria-api`, build `vanguard/Dockerfile`).
- Données persistantes : `/opt/aria-data` monté sur `/app/backend/data`.
- Variables d'env : `--env-file /opt/aria/vanguard/backend/.env` (jamais commité).

> ⚠️ **AVERTISSEMENT SÉCURITÉ — BINDING DU PORT (à respecter absolument)**
>
> Le conteneur **DOIT** être publié sur **`-p 127.0.0.1:8000:8000`** (boucle locale
> uniquement), **jamais** sur `-p 8000:8000`.
>
> `-p 8000:8000` bind sur `0.0.0.0` → **l'API est exposée à tout Internet** sans
> filtrage. L'historique du VPS montre que la commande a souvent été lancée ainsi
> par erreur. Le conteneur actuel est correctement bindé sur `127.0.0.1` : **préserver
> ce binding.** L'accès public passe par le reverse proxy / TLS en frontal
> (`api.ariavanguardzhc.com`), pas par le port Docker directement.
>
> Vérifier après tout `docker run` :
> ```bash
> docker port aria-api        # doit afficher: 8000/tcp -> 127.0.0.1:8000
> ss -tlnp | grep 8000        # doit être sur 127.0.0.1:8000, PAS 0.0.0.0:8000
> ```

---

## 1. Installation initiale (VPS vierge)

```bash
# 1. Docker
curl -fsSL https://get.docker.com | sh

# 2. Git
apt-get install -y git

# 3. Cloner le monorepo
git clone https://github.com/GoldenFarFR/ARIA.git /opt/aria

# 4. Renseigner les secrets d'env (NON commité) — requis avant le run
#    Chemin attendu par le conteneur : /opt/aria/vanguard/backend/.env
#    (copier depuis le coffre, cf. production.env.example)
#    -> éditer /opt/aria/vanguard/backend/.env

# 5. Build de l'image
cd /opt/aria
docker build -f vanguard/Dockerfile -t aria-api .

# 6. Lancer le conteneur (binding LOCAL obligatoire — cf. avertissement)
docker run -d --name aria-api --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v /opt/aria-data:/app/backend/data \
  --env-file /opt/aria/vanguard/backend/.env \
  aria-api
```

## 2. Mise à jour (nouvelle version) — méthode recommandée

**Utiliser le script de déploiement** `vanguard/deploy.sh`. Il encode la séquence
sûre (build avec commit injecté → suppression de TOUT conteneur `aria-api` avant
de lancer → un seul conteneur en `127.0.0.1` → vérif health) :

```bash
cd /opt/aria && ./vanguard/deploy.sh
```

Le script échoue explicitement si le health ne confirme pas le commit déployé.

### Pourquoi ce script (incidents évités)

- **commit `unknown`** : un `docker build` sans `--build-arg GIT_COMMIT=…` laisse
  le health afficher `commit:"unknown"` → on ne sait plus quelle version tourne.
  Le script passe systématiquement le hash.
- **conteneurs en double** : un ancien conteneur `aria-api` resté actif en même
  temps qu'un nouveau a déjà provoqué un incident (double polling / confusion).
  Le script fait `docker rm -f` sur **tout** conteneur `aria-api` avant le run.
- **exposition publique** : binding **strictement `127.0.0.1`** (jamais `8000:8000`).

### Mise à jour manuelle (repli, si le script est indisponible)

```bash
cd /opt/aria \
  && git pull --ff-only \
  && docker tag aria-api:latest aria-api:rollback 2>/dev/null; \
  docker build -f vanguard/Dockerfile \
       --build-arg GIT_COMMIT="$(git rev-parse HEAD)" -t aria-api . \
  && docker ps -aq --filter name=aria-api | xargs -r docker rm -f \
  && docker run -d --name aria-api --restart unless-stopped \
       -p 127.0.0.1:8000:8000 \
       -v /opt/aria-data:/app/backend/data \
       --env-file /opt/aria/vanguard/backend/.env \
       aria-api
```

Après mise à jour, contrôler :

```bash
docker port aria-api                       # 8000/tcp -> 127.0.0.1:8000
docker logs --tail=50 aria-api             # boot uvicorn OK
curl -s http://127.0.0.1:8000/api/health   # "commit" doit = le hash déployé
```

---

## Écart historique (à connaître)

La procédure d'install retrouvée dans l'historique bash du VPS lançait le `docker run`
initial **sans `-p`** (aucun port publié), et la commande de mise à jour en
`-p 127.0.0.1:8000:8000`. On a **normalisé les deux sur `-p 127.0.0.1:8000:8000`**
pour cohérence et sécurité. Les variantes vues en `-p 8000:8000` sont **à proscrire**
(cf. avertissement sécurité).

## Notes

- **VÉRIFIÉ** : hôte `31.70.114.74`, build `vanguard/Dockerfile`, volume
  `/opt/aria-data`, env-file `/opt/aria/vanguard/backend/.env`, restart policy
  `unless-stopped` — d'après la procédure opérateur.
- **À CONFIRMER (non couvert par la procédure fournie)** : le reverse proxy TLS en
  frontal (nginx/Caddy) qui expose `api.ariavanguardzhc.com` vers `127.0.0.1:8000`,
  et le renouvellement du certificat. À documenter séparément.
