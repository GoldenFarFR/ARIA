from aria_core.services.forex import forex_client
from fastapi import APIRouter, Query

router = APIRouter(prefix="/forex", tags=["forex"])

# Devises majeures seulement -- Frankfurter (BCE) ne couvre que les devises
# fiat suivies par la Banque Centrale Européenne, jamais de crypto ici.
MAJOR_PAIRS: list[tuple[str, str]] = [
    ("EUR", "USD"),
    ("USD", "JPY"),
    ("GBP", "USD"),
    ("USD", "CHF"),
]


@router.get("/rates")
async def latest_rates(base: str = Query("EUR"), symbols: str = Query("USD")):
    """Taux de reference BCE (Frankfurter), jamais une valeur inventee sur echec."""
    result = await forex_client.get_latest_rates(base, symbols.split(","))
    return {
        "base": result.base,
        "rates": result.rates,
        "date": result.date,
        "available": result.available,
        "error": result.error,
    }


@router.get("/majors")
async def majors():
    """Quelques paires majeures pre-selectionnees, une requete par paire de base
    -- Frankfurter n'a pas d'endpoint "plusieurs bases en un appel"."""
    out = []
    for base, quote in MAJOR_PAIRS:
        result = await forex_client.get_latest_rates(base, [quote])
        rate = result.rates.get(quote) if result.available else None
        out.append(
            {
                "base": base,
                "quote": quote,
                "rate": rate,
                "date": result.date,
                "available": result.available and rate is not None,
            }
        )
    return {"pairs": out}
