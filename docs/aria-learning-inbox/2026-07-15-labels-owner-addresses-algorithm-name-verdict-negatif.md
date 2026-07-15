[VPS Research]

# `labels.owner_addresses.algorithm_name` (Dune) — verdict négatif, vérifié par requête réelle

## Contexte

Dernier point ouvert du rapport précédent (`2026-07-15-graphsense-verifie-
negatif-dune-labels-pivot.md`) : le schéma de `labels.owner_addresses`
contient un champ `algorithm_name`, dont la seule présence suggérait que
certaines lignes pourraient être le produit d'un algorithme de clustering
communautaire déjà calculé (par exemple une variante des heuristiques
Victor FC2020) — piste non creusée à l'époque, juste une déduction sur le
nom du champ. Vérification demandée sur le **contenu réel**, pas le
schéma seul.

**Verdict en une phrase, avec preuve** : **`algorithm_name` est vide
partout** — sur les 52,4 millions de lignes de la table, ce champ vaut
**`NULL` à 100%**. Aucun algorithme de clustering documenté n'est exposé
par ce champ. **Verdict négatif, même discipline que pour GraphSense :
vérifié par une vraie requête, pas juste supposé à partir du nom du
champ.**

## Requête et résultat réels

```sql
SELECT algorithm_name, source, count(*) as n, count(DISTINCT owner_key) as n_owners
FROM labels.owner_addresses
GROUP BY algorithm_name, source
ORDER BY n DESC
```

Résultat complet (5 lignes seulement, la table entière tient dans ce
regroupement) :

| algorithm_name | source | n (lignes) | n_owners (entités distinctes) |
|---|---|---|---|
| `NULL` | `NULL` | 52 412 496 | 1 372 |
| `NULL` | `Forta` | 5 605 | 5 605 |
| `NULL` | `forta` (casse différente) | 1 269 | 639 |
| `NULL` | `Manual: Found` | 13 | 6 |
| `NULL` | `Manual: Transaction` | 1 | 1 |

Coût de la requête : 0,111 crédit (négligeable, sur le quota de
2500/mois).

## Lecture du résultat

- **`algorithm_name` : jamais renseigné, sur aucune des 52,4M lignes.**
  Le champ existe dans le schéma (probablement prévu pour un usage futur
  ou hérité d'une version antérieure du modèle de données Dune) mais
  **n'est actuellement peuplé par aucune source de labels** — ni les
  labels statiques en masse (52,4M lignes / seulement 1 372 entités
  distinctes — donc un petit nombre de très grosses entités, cohérent
  avec des exchanges/protocoles majeurs ayant chacun des milliers
  d'adresses de dépôt), ni les contributions "Forta"/"forta", ni les
  entrées manuelles.
- **Le champ `source` est la vraie information exploitable ici**, pas
  `algorithm_name` : la grande majorité des lignes (52,4M) n'a **aucune
  source déclarée** (`NULL`) — donc pas de traçabilité sur comment ces
  labels ont été produits, à traiter avec la prudence habituelle (donnée
  non sourcée ≠ donnée fausse, mais pas vérifiable non plus).
- **Découverte adjacente, non demandée mais notée en passant** :
  **Forta** apparaît comme source réelle sur ~6 900 lignes couvrant
  ~6 200 entités distinctes. Forta Network est un réseau réel de
  détection de menaces en temps réel sur smart contracts (bots de
  surveillance décentralisés) — pas la même famille d'outil que le
  clustering Sybil (c'est de la détection d'exploits/anomalies de
  contrat, pas du regroupement d'adresses par financement partagé), donc
  **hors sujet direct pour ce chantier**, mais une piste potentiellement
  intéressante pour un axe différent (sécurité de contrat en temps réel,
  proche de GoPlus déjà diligencié) — banquée, pas creusée.

## Conclusion pour le chantier Sybil

Ce champ précis **ne fournit aucun raccourci supplémentaire** pour le
clustering d'entité au-delà du pairwise — verdict négatif, symétrique à
celui de GraphSense. **Les deux raccourcis réels identifiés restent ceux
du rapport précédent** : `addresses.stats.first_funded_by` (heuristique
de financement partagé, peuplée et vérifiée) et `cex.addresses` (labels
d'exchange réels et vérifiés sur Base). `labels.owner_addresses` reste
utile pour son contenu **non-algorithmique** (les colonnes `custody_owner`/
`account_owner`/`contract_name` peuvent toujours contenir des labels
manuels utiles), mais pas comme source d'un algorithme de clustering déjà
calculé — cette piste précise est fermée.

## Branches ouvertes (banquées, pas creusées)

- Forta Network comme piste de sécurité de contrat en temps réel
  (détection d'anomalies/exploits), distincte du clustering Sybil — à
  évaluer séparément si un besoin de ce type se présente, dans le même
  esprit que GoPlus/Webacy déjà diligenciés.
- Les 1 372 entités couvrant 52,4M de lignes sans source déclarée —
  identifier lesquelles (probablement les plus grands exchanges/ponts)
  pourrait renforcer la confiance dans `cex.addresses`/`labels.owner_*`
  sans dépendre d'un `algorithm_name` qui n'existe pas — non fait ce soir.

## Sources

- Requête Dune réelle exécutée ce soir (via `mcp__dune__*`, serveur déjà
  configuré) : `labels.owner_addresses`, agrégation complète par
  `algorithm_name`/`source` — voir tableau ci-dessus, résultat brut
  intégral (5 lignes, toute la table)
- Contexte session : `docs/aria-learning-inbox/2026-07-15-graphsense-verifie-negatif-dune-labels-pivot.md`
  (rapport précédent, où `algorithm_name` avait été repéré comme piste
  ouverte à partir du schéma seul)

## Frontières confirmées respectées

Aucun compte créé, aucune clé activée (serveur MCP `dune` déjà configuré
par l'opérateur, usage en lecture seule). Coût de cette passe : 0,111
crédit sur 2500/mois. Aucun code ARIA modifié — une seule requête SQL en
lecture seule sur une table publique. Aucune approche de `wallet_guard`/
`permission_mode`/`config.toml`/auto-modification/capital réel.
