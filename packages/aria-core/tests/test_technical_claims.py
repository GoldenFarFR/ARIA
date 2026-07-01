from aria_core.technical_claims import (
    claims_unverified_github_success,
    reject_fake_technical_success,
)


def test_detects_fake_deploy_claim():
    text = (
        "Audit terminé.\nMVP public pages créées.\n"
        "Commit GitHub créé : feat(site): MVP public pages\n"
        "Site déployé : https://ariavanguardzhc.com"
    )
    assert claims_unverified_github_success(text)


def test_allows_verified_skill_data():
    reply = "Commit : https://github.com/GoldenFarFR/aria-vanguard/commit/abc"
    out = reject_fake_technical_success(
        reply,
        "fr",
        skill_used="holding_site",
        data={"committed": True, "github_commit_sha": "abc"},
    )
    assert out == reply


def test_replaces_hallucination_with_honest_message():
    fake = "Audit terminé. Site déployé. Tout semble fonctionner correctement."
    out = reject_fake_technical_success(fake, "fr", skill_used=None, data={})
    assert "projection LLM" in out or "pas exécuté" in out.lower() or "n'ai pas" in out