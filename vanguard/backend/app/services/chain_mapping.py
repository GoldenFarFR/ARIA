DEXSCREENER_TO_GECKO: dict[str, str] = {
    "solana": "solana",
    "ethereum": "eth",
    "eth": "eth",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "polygon": "polygon_pos",
    "avalanche": "avax",
    "optimism": "optimism",
    "fantom": "ftm",
    "cronos": "cro",
    "sui": "sui-network",
    "ton": "ton",
    "blast": "blast",
    "scroll": "scroll",
    "linea": "linea",
    "zksync": "zksync",
    "mantle": "mantle",
}


def to_gecko_network(chain_id: str) -> str | None:
    return DEXSCREENER_TO_GECKO.get(chain_id.lower())