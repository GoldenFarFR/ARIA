"""Radar d'opportunités — la détection d'idées à fusionner doit être fiable (FR + EN)."""
from aria_core.opportunity_radar import (
    extract_opportunities,
    format_operator_digest,
    mine_threads,
    rank_opportunities,
)


def test_detects_english_opportunity_language():
    text = "This could enable agents to pay onchain via x402. Someone should build a scanner."
    cands = extract_opportunities(text, source="x:@base")
    assert cands
    hooks = {h for c in cands for h in c.tech_hooks}
    assert "x402" in hooks and "onchain" in hooks


def test_detects_french_opportunity_language():
    text = "Il faudrait un agent qui verifie les tokens onchain. Ce serait genial d'ajouter une attestation."
    cands = extract_opportunities(text, source="manual")
    assert cands
    assert any("attestation" in c.tech_hooks or "onchain" in c.tech_hooks for c in cands)


def test_plain_statement_without_opportunity_is_ignored():
    # Une simple mention technique SANS langage d'opportunité ne doit pas remonter.
    cands = extract_opportunities("Base is a layer 2. The token exists.", source="manual")
    assert cands == []


def test_mines_replies_not_just_root():
    threads = [{
        "handle": "base",
        "text": "We shipped a new agent standard.",
        "replies": [
            {"handle": "dev1", "text": "This could enable an onchain reputation agent, someone should build it."},
            {"handle": "dev2", "text": "gm"},
        ],
    }]
    cands = mine_threads(threads)
    assert cands
    assert any("reply:@dev1" in c.source for c in cands)
    # le commentaire "gm" (bruit) ne produit rien
    assert not any("reply:@dev2" in c.source for c in cands)


def test_ranking_dedupes_and_orders():
    a = extract_opportunities("It would be great if ARIA had an x402 paymaster agent onchain.", source="s1")
    b = extract_opportunities("It would be great if ARIA had an x402 paymaster agent onchain.", source="s2")
    ranked = rank_opportunities(a + b)
    assert len(ranked) == 1  # doublon fusionné
    assert ranked[0].score > 0


def test_digest_is_human_readable():
    cands = extract_opportunities("There's a real opportunity: an onchain verifiable track record agent.", source="x:@base")
    out = format_operator_digest(cands, lang="fr")
    assert "Opportunites Base" in out
    assert "source:" in out


def test_empty_digest_message():
    assert "Aucune opportunit" in format_operator_digest([], lang="fr")
