"""_chain_display_label -- correction 14/07 : le libellé de chaîne affiché par
/walletscore était resté bloqué à 2 entrées ("base"/"ethereum") après
l'extension de blockscout.CHAIN_IDS à 13 chaînes, montrant le slug brut
("arbitrum", "zksync"...) au lieu d'un nom lisible. Dérive maintenant d'une
capitalisation générique + une petite table d'exceptions -- jamais un 2e
registre statique des 13 noms à tenir manuellement à jour."""
from __future__ import annotations

from aria_core.gateway.telegram_bot import _chain_display_label


def test_known_chains_get_readable_labels():
    assert _chain_display_label("base") == "Base"
    assert _chain_display_label("ethereum") == "Ethereum"
    assert _chain_display_label("arbitrum") == "Arbitrum"
    assert _chain_display_label("optimism") == "Optimism"
    assert _chain_display_label("polygon") == "Polygon"
    assert _chain_display_label("celo") == "Celo"
    assert _chain_display_label("gnosis") == "Gnosis"
    assert _chain_display_label("scroll") == "Scroll"
    assert _chain_display_label("rootstock") == "Rootstock"
    assert _chain_display_label("unichain") == "Unichain"
    assert _chain_display_label("soneium") == "Soneium"
    assert _chain_display_label("mode") == "Mode"


def test_zksync_uses_special_case_override():
    # Une simple capitalisation donnerait "Zksync", trompeur -- override dédié.
    assert _chain_display_label("zksync") == "zkSync Era"


def test_unknown_future_chain_degrades_to_capitalized_slug_not_a_crash():
    assert _chain_display_label("some_new_chain") == "Some_new_chain"
