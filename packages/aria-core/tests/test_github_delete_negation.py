"""looks_like_repo_delete ne doit PAS se déclencher sur une négation ("ne supprime pas ce
repo") -- action IRRÉVERSIBLE (suppression réelle d'un repo GitHub). Bug trouvé en auditant
les fonctions is_*/looks_like_* du codebase (09/07), même classe que le garde-fou de
négation de web_verify.py. Un repo sandbox non listé dans github_protected_repos aurait pu
être effacé malgré une demande explicite de le garder."""
from __future__ import annotations

from aria_core.skills.github_skill import looks_like_repo_delete


def test_negated_delete_request_not_detected():
    assert not looks_like_repo_delete("Ne supprime pas le repo test.")
    assert not looks_like_repo_delete("Ne supprime surtout pas ce repo là.")
    assert not looks_like_repo_delete("Don't delete the test repo.")
    assert not looks_like_repo_delete("Garde le repo test, ne le supprime pas.")
    assert not looks_like_repo_delete("Pas besoin de supprimer ce repo.")


def test_real_delete_request_still_detected():
    assert looks_like_repo_delete("Supprime le repo test-experiment-3.")
    assert looks_like_repo_delete("Peux-tu supprimer le repo experiment-old ?")
    assert looks_like_repo_delete("Delete the sandbox repo please.")
