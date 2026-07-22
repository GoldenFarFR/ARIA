"""Réputation du déployeur — a-t-il déjà déployé un contrat qui a rug ?

`dev_wallet.py`/`insider_wallets.py` jugent le comportement du déployeur SUR ce
token précis. Ce module regarde son HISTORIQUE : d'autres contrats déployés par
la MÊME adresse, et si l'un d'eux est déjà confirmé scam par ARIA elle-même
(`momentum_blacklist.py` — gratuit, zéro appel réseau, donnée de première main,
jamais une source tierce invérifiée).

Limite honnête, documentée plutôt que masquée : l'énumération des contrats créés
par une adresse n'a pas d'endpoint dédié bon marché sur Blockscout (vérifié
22/07 : aucun paramètre `type=contract_creation`, aucun tri "création d'abord").
La recherche passe donc par l'historique de transactions déjà exploré ailleurs
(`get_transactions_bounded`, même doctrine de pagination bornée que le reste du
projet) et ne couvre QUE les transactions les plus RÉCENTES du déployeur —
jamais garanti exhaustif. `truncated=True` le signale explicitement si le
plafond de pages est atteint sans épuiser l'historique.

Signal CONSULTATIF pur (même doctrine que dev_wallet.py/insider_wallets.py) —
jamais un véto dur, même sur une récidive confirmée. Constat important trouvé en
vérifiant ce module sur un cas réel (CNX, 22/07) : une adresse 'creator' peut
être un COMPTE DÉLÉGUÉ (EIP-7702 -- confirmé par appel direct Blockscout, champ
``proxy_type: "eip7702"``), donc pas toujours une identité stable d'un
déploiement à l'autre. Raison de plus pour ne jamais rejeter automatiquement sur
ce seul signal."""
from __future__ import annotations

from dataclasses import dataclass, field

# Pages de transactions du déployeur explorées au maximum (borné, jamais
# exhaustif — voir doctrine ci-dessus). Même ordre de grandeur que les autres
# scans bornés du projet (ex. get_first_funded_by côté Dune).
_MAX_PAGES = 3


@dataclass(frozen=True)
class DeployerHistoryFacts:
    prior_contracts_found: int = 0
    known_rugs: list[str] = field(default_factory=list)  # adresses déjà blacklistées
    truncated: bool = False
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DeployerHistoryVerdict:
    signal: str  # concern / neutral / unknown
    points: list[str] = field(default_factory=list)


def judge_deployer_history(facts: DeployerHistoryFacts) -> DeployerHistoryVerdict:
    """Jugement pur et déterministe, même doctrine que judge_dev_wallet/judge_insider_wallets."""
    if not facts.available:
        return DeployerHistoryVerdict(
            signal="unknown", points=[facts.error or "historique du déployeur non analysable"],
        )
    if not facts.known_rugs:
        note = (
            f"{facts.prior_contracts_found} contrat(s) antérieur(s) du déployeur trouvé(s), "
            "aucun déjà confirmé scam par ARIA"
        )
        if facts.truncated:
            note += " (historique borné aux transactions récentes, pas garanti exhaustif)"
        return DeployerHistoryVerdict(signal="neutral", points=[note])
    n = len(facts.known_rugs)
    return DeployerHistoryVerdict(
        signal="concern",
        points=[
            f"le déployeur a créé {n} contrat(s) déjà confirmé(s) scam par ARIA elle-même "
            "(récidiviste -- signal fort, jamais un rejet automatique à lui seul, l'adresse "
            "'creator' peut aussi être un compte délégué non stable d'un déploiement à l'autre)"
        ],
    )


async def gather_deployer_history_facts(
    creator: str | None,
    *,
    chain: str = "base",
    exclude_contract: str | None = None,
    max_pages: int = _MAX_PAGES,
    client=None,
    blacklist_module=None,
) -> DeployerHistoryFacts:
    """Récolte best-effort : contrats déjà créés par ce déployeur (borné, récent
    d'abord) puis croise chacun contre la liste noire propre d'ARIA. Défensif,
    jamais bloquant. ``client``/``blacklist_module`` injectables pour les tests
    offline (défaut : blockscout_client / momentum_blacklist)."""
    if not creator:
        return DeployerHistoryFacts(available=False, error="déployeur inconnu")
    if client is None:
        from aria_core.services.blockscout import blockscout_client as client
    if blacklist_module is None:
        from aria_core import momentum_blacklist as blacklist_module

    try:
        result = await client.get_transactions_bounded(creator, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 — historique bonus, jamais bloquant
        return DeployerHistoryFacts(available=False, error=f"historique indisponible ({exc})")
    if not result.available:
        return DeployerHistoryFacts(available=False, error=result.error)

    excl = (exclude_contract or "").lower()
    seen: set[str] = set()
    prior_contracts: list[str] = []
    for tx in result.transactions:
        addr = (getattr(tx, "created_contract", None) or "").lower()
        if not addr or addr == excl or addr in seen:
            continue
        seen.add(addr)
        prior_contracts.append(addr)

    known_rugs: list[str] = []
    for addr in prior_contracts:
        try:
            if await blacklist_module.is_blacklisted(addr, chain):
                known_rugs.append(addr)
        except Exception:  # noqa: BLE001 — une entrée illisible n'invalide pas les autres
            continue

    return DeployerHistoryFacts(
        prior_contracts_found=len(prior_contracts),
        known_rugs=known_rugs,
        truncated=result.truncated,
        available=True,
    )
