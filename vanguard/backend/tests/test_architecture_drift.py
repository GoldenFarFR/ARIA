"""CI guard — cerveau ARIA = aria-sandbox, pas app/aria legacy host."""
from pathlib import Path


def test_app_aria_removed():
    legacy = Path(__file__).resolve().parents[1] / "app" / "aria"
    assert not legacy.is_dir(), f"SSOT violation: {legacy} must not exist"


def test_requirements_install_aria_core():
    """aria-core vit dans le MONOREPO (packages/aria-core) depuis la consolidation —
    plus un dépôt "aria-sandbox" séparé installé via git+subdirectory. requirements.txt
    documente ça en commentaire (rien à pip-installer depuis un autre repo) ; c'est le
    Dockerfile qui fait l'install locale reproductible (COPY + pip install du chemin)."""
    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    req_text = req.read_text(encoding="utf-8")
    assert "aria-core" in req_text
    assert "aria-sandbox" not in req_text, (
        "aria-sandbox n'existe plus — aria-core est le monorepo local (packages/aria-core)"
    )

    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    docker_text = dockerfile.read_text(encoding="utf-8")
    assert "packages/aria-core" in docker_text, (
        "Le Dockerfile doit installer aria-core depuis le chemin local du monorepo"
    )