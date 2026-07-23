"""Détection de cluster Sybil parmi les holders d'un token — au-delà de la
convergence pairwise déjà en place.

Trouvé par le stress-test (Codex Partie 11) : une distribution factice (ex. 40
wallets financés par la même source, chacun sous le seuil individuel de
concentration) retourne un signal POSITIF aujourd'hui — `_holder_concentration`
(acp_onchain_scan.py) ne regarde que le TOP HOLDER individuel (`top_holder_pct`)
et le cumul des 10 premiers (`top10_holder_pct`), jamais si ces holders
partagent une origine commune (même dépôt, même wallet financeur). 40 wallets à
2% chacun passent chaque barrière individuelle alors que 78% de l'offre est en
réalité concentrée entre les mains d'un seul acteur déguisé en communauté.

Distinct de `smart_money._pairwise_convergence` (scopé aux 1-3 wallets soumis
ENSEMBLE à `/walletscore`, jamais au pool complet de holders d'un token) — ce
module regroupe TOUS les top holders d'UN token par source de financement
commune (heuristique déjà éprouvée : `smart_money._funding_source`, réutilisée
telle quelle, jamais dupliquée).

Coût réseau réel : un appel Blockscout borné (`get_transactions_bounded`) PAR
holder vérifié, plafonné à `max_holders_checked` — nettement plus coûteux que
les autres signaux consultatifs de ce chantier (insider_wallets/deployer_history
ne réutilisent que des données déjà en main). Volontairement PAS câblé sur le
chemin `/vc` par défaut (voir `acp_onchain_scan.py::include_sybil_check`, off) —
la poche VC automatique reste dormante (0% du capital, décision du 15/07), le
coût réseau de ce signal ne se justifie pas tant qu'elle n'est pas réactivée.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Taille de cluster (holders partageant la même source de financement) au-delà
# de laquelle on suspecte une distribution Sybil plutôt qu'une coïncidence
# (deux early-buyers financés par le même exchange n'ont rien d'anormal).
_MIN_CLUSTER_SIZE_FOR_SUSPICION = 5
# Part cumulée de l'offre (%) détenue par ce cluster au-delà de laquelle le
# signal devient significatif -- un petit cluster de dust n'a aucune valeur.
_MIN_CLUSTER_CUMULATIVE_PCT_FOR_SUSPICION = 20.0
# Holders vérifiés au maximum (coût réseau : 1 appel Blockscout borné chacun) --
# le TOP de la distribution, jamais tous les holders (souvent des centaines).
_DEFAULT_MAX_HOLDERS_CHECKED = 15


@dataclass(frozen=True)
class SybilClusterFacts:
    holders_checked: int = 0
    largest_cluster_size: int = 0
    largest_cluster_cumulative_pct: float = 0.0
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class SybilClusterVerdict:
    signal: str  # concern / neutral / unknown
    points: list[str] = field(default_factory=list)


def judge_sybil_cluster(facts: SybilClusterFacts) -> SybilClusterVerdict:
    """Jugement pur et déterministe — même doctrine que les autres signaux
    consultatifs de ce chantier (insider_wallets/deployer_history)."""
    if not facts.available:
        return SybilClusterVerdict(signal="unknown", points=[facts.error or "clustering non analysable"])
    if (
        facts.largest_cluster_size >= _MIN_CLUSTER_SIZE_FOR_SUSPICION
        and facts.largest_cluster_cumulative_pct >= _MIN_CLUSTER_CUMULATIVE_PCT_FOR_SUSPICION
    ):
        return SybilClusterVerdict(
            signal="concern",
            points=[
                f"cluster Sybil suspecté : {facts.largest_cluster_size} holders (sur "
                f"{facts.holders_checked} vérifiés) partagent la même source de financement, "
                f"cumulent {facts.largest_cluster_cumulative_pct:.0f}% de l'offre -- distribution "
                "possiblement déguisée en communauté, pas un cluster confirmé (heuristique de "
                "financement, jamais un rejet automatique)"
            ],
        )
    return SybilClusterVerdict(
        signal="neutral",
        points=[
            f"{facts.holders_checked} holder(s) vérifié(s), plus gros regroupement par source de "
            f"financement commune : {facts.largest_cluster_size} holder(s) ({facts.largest_cluster_cumulative_pct:.0f}% de l'offre)"
        ],
    )


async def gather_sybil_cluster_facts(
    holders: list,
    *,
    exclude_addresses: set[str] | None = None,
    max_holders_checked: int = _DEFAULT_MAX_HOLDERS_CHECKED,
    client=None,
    funding_source_fn=None,
) -> SybilClusterFacts:
    """Regroupe les top holders par source de financement commune. ``holders``
    (liste de `TokenHolder`, déjà récupérée par le scan -- zéro re-fetch des
    holders eux-mêmes) ; ``exclude_addresses`` : pool LP/adresses de burn (même
    exclusion que `_holder_concentration`, jamais comptées comme des Sybils).
    Best-effort, jamais bloquant. ``client``/``funding_source_fn`` injectables
    pour les tests offline (défaut : blockscout_client / smart_money._funding_source)."""
    if not holders:
        return SybilClusterFacts(available=True, holders_checked=0)
    if client is None:
        from aria_core.services.blockscout import blockscout_client as client
    if funding_source_fn is None:
        from aria_core.services.smart_money import _funding_source as funding_source_fn

    excl = {a.lower() for a in (exclude_addresses or set())}
    candidates = [
        h for h in holders
        if (getattr(h, "address", "") or "").lower() not in excl
    ][:max_holders_checked]

    if not candidates:
        return SybilClusterFacts(available=True, holders_checked=0)

    clusters: dict[str, list[float]] = {}
    checked = 0
    for holder in candidates:
        try:
            source, _truncated = await funding_source_fn(client, holder.address)
        except Exception:  # noqa: BLE001 — un échec isolé n'invalide pas les autres holders
            continue
        checked += 1
        if not source:
            continue
        pct = float(getattr(holder, "percentage", None) or 0.0)
        clusters.setdefault(source, []).append(pct)

    if not clusters:
        return SybilClusterFacts(available=True, holders_checked=checked)

    largest_source = max(clusters, key=lambda s: len(clusters[s]))
    largest_pcts = clusters[largest_source]
    return SybilClusterFacts(
        holders_checked=checked,
        largest_cluster_size=len(largest_pcts),
        largest_cluster_cumulative_pct=sum(largest_pcts),
        available=True,
    )
