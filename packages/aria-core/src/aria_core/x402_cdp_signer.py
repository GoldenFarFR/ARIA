"""Implémentation réelle du ``pay_fn`` attendu par ``x402_executor.fetch_paid_resource`` --
signe un paiement x402 via le wallet CDP dédié (même wallet que
``agent_wallet_cdp_adapter.py``, patron identique : import paresseux, aucune clé lue ou
manipulée ici).

Méthode de signature vérifiée dans la doc/le code source officiels avant d'écrire ce
module (jamais devinée) -- deux SDK, aucun ne documente de raccourci direct CDP->x402 :

- ``cdp-sdk`` (package Python officiel Coinbase) expose ``cdp.evm_local_account.
  EvmLocalAccount`` : un wrapper SYNCHRONE compatible ``eth_account.signers.base.
  BaseAccount`` autour d'un ``EvmServerAccount`` -- vérifié en lisant son code source
  (github.com/coinbase/cdp-sdk/blob/main/python/cdp/evm_local_account.py) : sa méthode
  ``sign_typed_data(domain_data=, message_types=, message_data=)`` a EXACTEMENT la même
  signature attendue par la classe ``EthAccountSigner`` du SDK x402 officiel.
- ``x402`` (package Python officiel, x402-foundation/x402) ne fournit AUCUN adaptateur
  CDP prêt à l'emploi -- seulement ``x402.mechanisms.evm.signers.EthAccountSigner``,
  conçu pour n'importe quel objet ``eth_account``-compatible. Le module ``cdp.x402``
  (déjà dans cdp-sdk) est un tout autre outil : c'est un client FACILITATOR (vérifier/
  régler un paiement REÇU), pas un signeur côté client qui PAIE -- ne pas confondre.

Donc : ``EthAccountSigner(EvmLocalAccount(cdp_account))`` est le pont vérifié, construit
à partir des DEUX sources officielles, jamais une supposition sur un raccourci qui
n'existe pas.

**Non testé contre un vrai appel réseau à ce stade** (aucun identifiant CDP dans cette
session, doctrine secrets -- même réserve que ``agent_wallet_cdp_adapter.py``). Avant
toute activation réelle, norme de process du 15/07 (#157) : vérifier au moins une fois
la forme exacte de la réponse contre un vrai appel sur le VPS."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

WALLET_NAME = "aria-agent-wallet-pilot"  # même wallet dédié que agent_wallet_cdp_adapter.py


async def build_x402_payment_header(payment_required: dict[str, Any]) -> str:
    """``pay_fn`` injectable pour ``x402_executor.fetch_paid_resource`` -- signe le
    paiement demandé par ``payment_required`` (le premier ``accepts[0]`` du corps 402)
    et renvoie la valeur du header ``X-PAYMENT`` (base64, protocole x402 v1).

    Lève une exception sur tout échec (import manquant, panne CDP, panne de
    construction du paiement) -- ``x402_executor`` journalise déjà ``status="failed"``
    sur exception, aucune gestion d'erreur silencieuse nécessaire ici."""
    from cdp import CdpClient
    from cdp.evm_local_account import EvmLocalAccount
    from x402 import x402Client
    from x402.http.x402_http_client import x402HTTPClient
    from x402.mechanisms.evm.exact import register_exact_evm_client
    from x402.mechanisms.evm.signers import EthAccountSigner

    async with CdpClient() as cdp:
        account = await cdp.evm.get_or_create_account(name=WALLET_NAME)
        local_account = EvmLocalAccount(account)

    signer = EthAccountSigner(local_account)
    client = x402Client()
    register_exact_evm_client(client, signer)
    http_client = x402HTTPClient(client)

    # ``payment_required`` ici = un seul ``accepts[0]`` (dict brut) -- le SDK x402
    # attend l'enveloppe complète pour la reconstruction du type ``PaymentRequired``.
    # ``x402Version`` par défaut 1 si absent (protocole v1 -- même défaut que
    # services/x402.py::payment_required_response).
    body = {
        "x402Version": payment_required.get("x402Version", 1),
        "accepts": [payment_required],
    }
    parsed = http_client.get_payment_required_response(lambda _name: None, body)
    payload = await client.create_payment_payload(parsed)
    headers = http_client.encode_payment_signature_header(payload)
    header_value = headers.get("X-PAYMENT")
    if not header_value:
        raise RuntimeError(f"encode_payment_signature_header n'a pas produit de X-PAYMENT : {headers!r}")
    return header_value
