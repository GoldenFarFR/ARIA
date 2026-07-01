from aria_core.skills.github_skill import (
    _extract_new_repo_name,
    _wants_create_repo,
    looks_like_repo_create,
)


def test_wants_create_repo():
    assert _wants_create_repo("créer un repo aria-demo") is True
    assert _wants_create_repo("create repo my-app") is True
    assert _wants_create_repo("Juste crée le repo sans rien de plus") is True
    assert _wants_create_repo("github status") is False


def test_looks_like_repo_create():
    assert looks_like_repo_create("Aria-demo et utilise le repo template") is True
    assert looks_like_repo_create("bonjour") is False


def test_extract_new_repo_name():
    assert _extract_new_repo_name("créer repo aria-demo") == "aria-demo"
    assert _extract_new_repo_name("create repository My_App") == "my-app"
    assert _extract_new_repo_name("Aria-demo et utilise le repo template") == "aria-demo"