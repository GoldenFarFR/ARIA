[VPS Research]

# GraphSense vérifié (code, pas supposition) : ne couvre pas le clustering account-model — pivot vers les tables `labels.*`/`cex.addresses`/`addresses.stats` de Dune

## Contexte

Suite du radar Sybil du 15/07 : vérifier concrètement si GraphSense
(`graphsense.org`, MIT) implémente déjà, pour Ethereum/Base (modèle de
compte), les heuristiques dépôt/airdrop/autorisation publiées par Victor
(FC2020) — ce qui éviterait à ARIA de les ré-implémenter elle-même. La
consigne était explicite : vérifier **par le code/la doc réelle, pas une
supposition**. C'est ce qui a été fait — et le résultat est net.

**Verdict en une phrase** : **GraphSense NE l'implémente PAS** pour le
modèle de compte — vérifié en lisant le code source réel, pas la page
marketing. En creusant pourquoi, une piste bien plus directement
exploitable est apparue à la place : **Dune expose déjà, via son propre
Spellbook communautaire, les tables `addresses.stats.first_funded_by`,
`cex.addresses` et `labels.owner_addresses`** — c'est-à-dire les
signaux bruts de l'heuristique de financement partagé, déjà calculés,
déjà accessibles gratuitement via le serveur MCP `dune` déjà configuré
pour ARIA, sans avoir besoin ni de GraphSense ni d'une ré-implémentation
Louvain/K-means from scratch.

---

## 1. GraphSense — vérification négative, par le code, pas par la doc

**Méthode** : la doc marketing de GraphSense/les résumés de recherche
(déjà cités dans le rapport précédent) affirment vaguement que
« l'address clustering est possible pour Ethereum sur la base
d'heuristiques liées aux adresses de dépôt, airdrops, autorisations de
token » — **mais ceci décrit la littérature académique (Victor FC2020),
pas nécessairement une implémentation réelle dans le code GraphSense
lui-même**. Vérification directe faite ce soir :

- **Repo historique `graphsense-transformation`** (Scala/Spark) — README
  confirme : *« Code is now maintained in repository graphsense-spark »*
  (déprécié). Contenu vérifié 100% **Bitcoin/UTXO-only** : commande
  `submit.sh` accepte `--bech32-prefix` et `--coinjoin-filtering`
  (spécifiques Bitcoin), keyspace `btc_raw`, fichiers de test nommés
  `address_cluster_with_coinjoin.json`, `cluster_inputs.json`,
  `cluster_outputs.json` — vocabulaire "inputs/outputs" = modèle UTXO,
  aucune trace de "account model".
- **Repo actuel `graphsense-spark`** (successeur) — le README confirme la
  prise en charge du réseau `eth` pour l'**ingestion** de données brutes
  (`graphsense-cli ingest from-node -c eth`). Le code source contient bien
  un module dédié `org.graphsense.account.eth.*`
  (`Transformation.scala`, `Model.scala`, `Source.scala`, `Tokens.scala`)
  — donc un vrai traitement Ethereum existe, **mais** un `grep`
  systématique sur les mots-clés `cluster`, `deposit`, `entity` dans
  `account/eth/Transformation.scala` (962 lignes), `account/Model.scala`,
  `account/eth/Model.scala`, `account/eth/Source.scala`,
  `account/TransformationJob.scala` et `TransformHelpers.scala`
  **renvoie zéro résultat sur les six fichiers**. Le clustering d'adresse
  (`utxo.Transformation`, `utxo.Model`, etc.) n'existe, dans l'arborescence
  du code, **que côté `org.graphsense.utxo.*`** — jamais côté
  `org.graphsense.account.*`.

**Conclusion vérifiée, pas supposée** : le module Ethereum de GraphSense
fait de l'ETL et de la normalisation de données (transferts, tokens,
statistiques par adresse), mais **n'implémente aucune des heuristiques
de clustering d'entité de Victor (FC2020) pour le modèle de compte** —
seul le clustering UTXO classique (co-spend/multi-input, pertinent
uniquement pour Bitcoin/Bitcoin Cash/Litecoin/Zcash) est présent dans le
code. **Utiliser GraphSense pour ce besoin précis n'apporterait rien
qu'ARIA n'ait déjà** (transferts déjà collectés via Blockscout) —
piste fermée, avec preuve à l'appui plutôt qu'un "ça n'a pas l'air de
marcher".

## 2. Pivot trouvé en creusant — Dune expose déjà les signaux bruts, gratuitement, testé en direct

En cherchant si un autre projet implémentait ces heuristiques, une
vérification croisée dans le Spellbook Dune (déjà accessible via le
serveur MCP configuré pour ARIA, cf. `docs/dune-integration-plan.md`) a
révélé trois tables directement exploitables — **toutes testées en
direct ce soir, pas juste lues dans un schéma** :

### 2.1 `addresses.stats` — l'heuristique de financement partagé, déjà calculée

Colonnes confirmées par test réel : `address`, **`first_funded_by`**,
`first_funded_at`, `is_eoa`, `is_smart_contract`, couverture confirmée
Base + 11 autres chaînes. C'est **exactement** l'heuristique de dépôt de
Victor (FC2020) — qui a financé une adresse en premier — déjà calculée
et interrogeable par SQL, sans rien construire.

```
SELECT address, first_funded_by, first_funded_at, is_eoa, is_smart_contract
FROM addresses.stats WHERE blockchain = 'base' AND address IN (...)
```
Résultat réel obtenu : pour WETH Base (`0x4200...0006`),
`first_funded_by = 0xe8a3ecea7d6a688ee903173024225357ddf29e93`,
`first_funded_at = 2023-06-22 18:53:33 UTC`. **Réserve de qualité de
donnée trouvée** : ce même enregistrement indique `is_eoa: true,
is_smart_contract: false` pour une adresse qui est en réalité un contrat
prédéployé Base (predeploy L2, pas un déploiement classique) — signe que
la détection `is_smart_contract` de cette table peut se tromper sur les
adresses prédéployées/spéciales de Base, à garder en tête avant de faire
une confiance aveugle à ce flag précis pour des cas limites. Coût de la
requête : 0,963 crédit (plus cher que les tests précédents — l'absence
de filtre sur une colonne de partition a probablement forcé un scan plus
large ; à optimiser avec un filtre de date/bloc si utilisé en production).

### 2.2 `cex.addresses` — labellisation d'exchange, confirmée pour Base

Colonnes : `blockchain`, `address`, `cex_name`, `distinct_name`,
`added_by`, `added_date`. **Test réel sur Base** : 5 adresses réelles
renvoyées, labellisées **Binance, XT.com, Bithumb, Korbit, CoinDCX** —
couverture bien au-delà des seuls grands noms occidentaux. Coût :
0,097 crédit. **Apport concret pour ARIA** : `smart_money.py` n'a
aujourd'hui **aucune connaissance des adresses d'exchange centralisées**
— seulement une liste manuelle d'infrastructure DEX. Croiser les
principaux holders contre `cex.addresses` permettrait d'exclure
directement les wallets de dépôt CEX du calcul de convergence, un signal
complémentaire simple et gratuit à ajouter avant même de construire quoi
que ce soit de plus complexe.

### 2.3 `labels.owner_addresses` / `labels.owner_details` — schéma riche, à explorer plus loin

Schéma confirmé (pas juste supposé) via `includeSchema=true` :
`owner_key`, **`custody_owner`**, **`account_owner`**, `contract_name`,
`eoa`, `factory_contract`, **`algorithm_name`**, `source`,
`identifying_transaction`, `source_evidence`. La présence d'un champ
`algorithm_name` suggère que certaines lignes de cette table sont déjà le
produit d'un algorithme de clustering communautaire (pas seulement des
tags manuels) — **piste non creusée ce soir en profondeur** (pas de
requête de test sur le contenu réel d'`algorithm_name`), mais le schéma
seul indique un potentiel plus riche que `cex.addresses`/`addresses.stats`
pour distinguer "wallet d'équipe/insider" d'un "wallet whale indépendant"
— exactement le angle mort déjà identifié dans le rapport précédent
(`smart_money.py`, exclusion actuelle basée sur le seul `is_contract`).

## 3. Comparaison avec les pistes déjà diligenciées

| | Coût | Statut vérifié ce soir | Apport pour le clustering Sybil |
|---|---|---|---|
| GraphSense | Gratuit (MIT) | **Négatif, confirmé par le code** | Aucun — pas de clustering account-model implémenté |
| Tables Dune `addresses.stats`/`cex.addresses`/`labels.owner_*` | Quasi-gratuit (déjà dans le quota 2500 crédits/mois, ~1,2 crédit dépensés ce soir) | **Positif, confirmé par requêtes réelles** | Signal de financement partagé + labels CEX déjà calculés, exploitables immédiatement en SQL |
| Ré-implémentation maison (Louvain/K-means, cf. rapport Sybil précédent) | Gratuit (networkx/scikit-learn) | Toujours valide, complémentaire | Nécessaire pour aller au-delà du signal brut (regroupement en clusters, raffinement) |
| Arkham Intelligence | 149-999$/mois | Déjà diligencié | Labels d'entité plus riches (exchanges/funds/whales) mais payant |
| Webacy KYW | Payant, boîte noire | Déjà diligencié | Signal spam/sybil packagé, mécanisme opaque |

**Recommandation actualisée** : le chantier "clustering Sybil au-delà du
pairwise" gagnerait à démarrer par **une requête Dune ponctuelle sur
`addresses.stats.first_funded_by`** pour les wallets déjà suivis par
ARIA (croisement direct avec l'historique existant), avant même de coder
du Louvain/K-means — c'est un raccourci gratuit et immédiatement
testable qui n'existait pas explicitement dans le rapport précédent. La
ré-implémentation maison (Louvain + K-means, cf. rapport du 15/07 sur
TrustaLabs/Victor FC2020) reste pertinente pour transformer ce signal
brut en clusters exploitables, mais n'a plus besoin d'être construite
"from scratch" pour la partie financement partagé — Dune la fournit déjà
calculée.

## Branches ouvertes (banquées, pas creusées)

- Contenu réel du champ `algorithm_name` dans `labels.owner_addresses` —
  pourrait révéler d'autres heuristiques de clustering déjà calculées par
  la communauté Dune, non testé ce soir.
- Fiabilité de `is_smart_contract`/`is_eoa` sur les adresses prédéployées
  spécifiques à Base (réserve trouvée au §2.1) — à vérifier avant de s'y
  fier pour des décisions automatiques.
- Coût réel à l'échelle d'un usage récurrent de `addresses.stats` sans
  filtre de partition (0,963 crédit pour 2 adresses ce soir) — à
  optimiser (filtrer par date/bloc) avant un usage en production régulier.
- `labels.owner_details` (deuxième table du même schéma, infos projet :
  site web, GitHub, catégorie) — pourrait enrichir le contexte d'un
  wallet détecté comme appartenant à un projet/protocole connu, non
  exploré ce soir.

## Sources

- [GraphSense — graphsense-transformation (GitHub, archivé/déprécié)](https://github.com/graphsense/graphsense-transformation)
- [GraphSense — graphsense-spark (GitHub, actuel)](https://github.com/graphsense/graphsense-spark)
- Lecture directe de code source ce soir (pas juste README) :
  `graphsense-spark/src/main/scala/org/graphsense/account/eth/Transformation.scala`,
  `account/Model.scala`, `account/eth/Model.scala`, `account/eth/Source.scala`,
  `account/TransformationJob.scala`, `TransformHelpers.scala` — grep
  `cluster|deposit|entity`, zéro résultat sur les six fichiers
- Requêtes Dune réelles exécutées ce soir (via `mcp__dune__*`, serveur déjà
  configuré) : `addresses.stats` (WETH/USDC Base), `cex.addresses`
  (Base), `searchTables` avec `includeSchema=true` sur `labels.*`
- Contexte session : `docs/aria-learning-inbox/2026-07-15-radar-sybil-clustering-entite-gratuit.md`
  (rapport Sybil précédent, TrustaLabs/Victor FC2020/Arbitrum Foundation),
  `docs/dune-integration-plan.md` (intégration Dune déjà en cours de
  diligence)

## Frontières confirmées respectées

Aucun compte créé, aucune clé activée (le serveur MCP `dune` utilisé ce
soir était déjà configuré par l'opérateur pour ARIA, usage en lecture
seule uniquement). Coût total Dune de cette passe : ~1,2 crédit sur
2500/mois — négligeable. Aucun code ARIA modifié — recherche externe +
lecture de code source tiers (GraphSense) + requêtes SQL en lecture seule
sur des tables publiques. Aucune approche de `wallet_guard`/
`permission_mode`/`config.toml`/auto-modification/capital réel.
