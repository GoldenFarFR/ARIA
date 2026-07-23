"""Signal « substance GitHub » — juge la qualité RÉELLE du développement,
pas sa fréquence.

Point faible du stress-test (Codex Partie 11) : `project_activity.py` juge la
FRAÎCHEUR (jours depuis le dernier commit), jamais leur SUBSTANCE — un projet
qui spam des commits cosmétiques (README, formatage, un caractère changé)
passerait le même signal qu'un vrai développement technique.

Design évalué contre une proposition externe, vérifié techniquement avant
d'implémenter (23/07) :
- **Coût réel confirmé par appel direct** : l'endpoint liste des commits
  (`GET /repos/{o}/{r}/commits`) ne renvoie JAMAIS les statistiques (lignes
  ajoutées/supprimées) ni les fichiers modifiés — il faut un appel SÉPARÉ par
  commit (`GET /repos/{o}/{r}/commits/{sha}`) pour ça. Plafonné à
  `_MAX_COMMITS_ANALYZED` (échantillon des plus récents), jamais une analyse
  exhaustive d'un historique potentiellement long.
- **Authentifié** via `GITHUB_TOKEN` (déjà existant, jamais un nouveau secret)
  — confirmé par appel réel qu'il peut lire N'IMPORTE QUEL dépôt PUBLIC tiers
  (pas seulement `GoldenFarFR/ARIA`), rate limit authentifié 5000/h (vs 60/h
  anonyme, ce que `project_activity._fetch_github` utilise aujourd'hui — gap
  de sobriété distinct, noté mais hors scope de ce signal).
- **Jamais de LLM** pour "lire" le code (trop cher/lent pour un signal
  systématique appelé à chaque scan `/vc`) — heuristiques déterministes
  uniquement (extension de fichier, taille de diff, dispersion temporelle).

Signal purement consultatif (même doctrine que les autres signaux de ce
chantier : insider_wallets/deployer_history/sybil_cluster), jamais un véto.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"

# Fenêtre d'analyse (jours) et plafond de commits analysés EN DÉTAIL (chaque
# commit coûte un appel réseau séparé, voir doctrine ci-dessus).
_WINDOW_DAYS = 90
_MAX_COMMITS_ANALYZED = 30

# Extensions considérées comme du CODE technique réel (vs cosmétique).
_TECHNICAL_EXTENSIONS = {
    ".sol", ".vy", ".rs", ".go", ".py", ".ts", ".tsx", ".js", ".jsx",
    ".java", ".c", ".cpp", ".h", ".rb", ".php", ".sh",
}
# Fragments de nom de fichier considérés comme purement cosmétiques.
_COSMETIC_NAME_FRAGMENTS = ("readme", "license", "changelog", ".md", ".txt", ".rst")
_TEST_PATH_FRAGMENTS = ("test", "spec", "__tests__")
# Messages de commit génériques, sans substance descriptive.
_GENERIC_MESSAGES = {"update", "fix", "wip", "commit", "changes", "misc", "."}
_MIN_MESSAGE_LENGTH = 10

# Sous ce nombre de commits TECHNIQUES analysés, l'échantillon est trop faible
# pour juger honnêtement (score -> indisponible, jamais un score fabriqué).
_MIN_TECHNICAL_COMMITS = 8
# Garde-fou précoce (revue croisée externe, 23/07) : sous ce nombre de commits
# BRUTS dans la fenêtre (avant même de savoir combien sont techniques), le
# dépôt est manifestement trop peu actif -- inutile de payer le moindre appel
# détail (un par commit, coût réel vérifié) pour un repo quasi mort.
_MIN_RAW_COMMITS_BEFORE_DETAIL = 5

# Catégories fonctionnelles (revue croisée externe, 23/07) -- plus honnête
# qu'un simple compte d'extensions distinctes : deux extensions de la MÊME
# couche (ex. .py et .sh d'un même backend) ne doivent pas compter comme deux
# signaux de diversité indépendants.
_CATEGORY_CONTRACT_EXTENSIONS = {".sol", ".vy", ".rs", ".move", ".cairo"}
_CATEGORY_JS_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}
_CATEGORY_BACKEND_EXTENSIONS = {".py", ".go", ".java", ".rb", ".php", ".c", ".cpp", ".h", ".sh"}

# Normalisation (voir judge_github_substance) : au-delà de ces valeurs, le
# critère est considéré "plein score" -- pas une science exacte, un repère
# raisonnable pour éviter qu'un seul projet exceptionnel écrase l'échelle.
_DIFF_SIZE_FULL_SCORE = 50.0
# 5 catégories possibles (contract/tests/js_ts/backend/other_tech) -- 4 déjà
# atteintes est un signe de projet multi-couches réel (contrat + frontend +
# scripts + tests, par exemple), plein score sans exiger la 5e.
_DISTINCT_CATEGORIES_FULL_SCORE = 4


@dataclass(frozen=True)
class GithubSubstanceFacts:
    commits_analyzed: int = 0
    technical_commits: int = 0
    code_ratio: float | None = None            # part de lignes techniques vs cosmétique (0-1)
    avg_diff_size: float | None = None         # lignes changées / commit technique
    has_tests: bool = False
    distinct_categories: int = 0               # catégories fonctionnelles distinctes (contract/tests/js_ts/backend/other_tech)
    regularity_score: float | None = None      # 0-1 : dispersion temporelle (1 = réparti, 0 = dump)
    message_quality_score: float | None = None  # 0-1 : part de messages descriptifs
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class GithubSubstanceVerdict:
    signal: str  # positive / neutral / weak / unknown
    score: float | None = None  # 0-100
    points: list[str] = field(default_factory=list)


def judge_github_substance(facts: GithubSubstanceFacts) -> GithubSubstanceVerdict:
    """Jugement pur et déterministe — même doctrine que les autres signaux de
    ce chantier. Jamais un rejet automatique, un score consultatif de plus."""
    if not facts.available:
        return GithubSubstanceVerdict(signal="unknown", points=[facts.error or "activité GitHub non analysable"])
    if facts.technical_commits < _MIN_TECHNICAL_COMMITS:
        return GithubSubstanceVerdict(
            signal="unknown",
            points=[
                f"seulement {facts.technical_commits} commit(s) technique(s) sur {_WINDOW_DAYS}j "
                f"(< {_MIN_TECHNICAL_COMMITS} requis) -- échantillon trop faible pour juger honnêtement"
            ],
        )

    code_ratio = facts.code_ratio or 0.0
    diff_component = min(1.0, (facts.avg_diff_size or 0.0) / _DIFF_SIZE_FULL_SCORE)
    test_component = 1.0 if facts.has_tests else 0.0
    diversity_component = min(1.0, facts.distinct_categories / _DISTINCT_CATEGORIES_FULL_SCORE)
    regularity_component = facts.regularity_score or 0.0
    message_component = facts.message_quality_score or 0.0

    score = 100.0 * (
        0.30 * code_ratio
        + 0.20 * diff_component
        + 0.15 * test_component
        + 0.15 * diversity_component
        + 0.10 * regularity_component
        + 0.10 * message_component
    )
    score = round(score, 1)

    detail = (
        f"{facts.technical_commits}/{facts.commits_analyzed} commits techniques, "
        f"{code_ratio * 100:.0f}% code réel, diff moyen {facts.avg_diff_size or 0:.0f} lignes, "
        f"tests {'oui' if facts.has_tests else 'non'}, {facts.distinct_categories} catégorie(s) fonctionnelle(s)"
    )

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"
    return GithubSubstanceVerdict(signal=signal, score=score, points=[f"substance {score:.0f}/100 -- {detail}"])


def _is_technical_file(filename: str) -> bool:
    lower = filename.lower()
    if any(frag in lower for frag in _COSMETIC_NAME_FRAGMENTS):
        return False
    return any(lower.endswith(ext) for ext in _TECHNICAL_EXTENSIONS)


def _is_test_file(filename: str) -> bool:
    lower = filename.lower()
    return any(frag in lower for frag in _TEST_PATH_FRAGMENTS)


def _file_extension(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx != -1 else ""


def _file_category(filename: str) -> str | None:
    """Catégorie FONCTIONNELLE d'un fichier technique (contract/tests/js_ts/
    backend/other_tech) -- plus honnête qu'un simple compte d'extensions
    distinctes (deux extensions de la MÊME couche, ex. .py et .sh d'un même
    backend, ne doivent pas compter comme deux signaux de diversité
    indépendants). ``None`` si le fichier n'est pas technique (appelant déjà
    filtré via `_is_technical_file`, mais robuste si appelé isolément)."""
    if _is_test_file(filename):
        return "tests"
    ext = _file_extension(filename)
    if ext in _CATEGORY_CONTRACT_EXTENSIONS:
        return "contract"
    if ext in _CATEGORY_JS_TS_EXTENSIONS:
        return "js_ts"
    if ext in _CATEGORY_BACKEND_EXTENSIONS:
        return "backend"
    if ext in _TECHNICAL_EXTENSIONS:
        return "other_tech"
    return None


def _message_is_descriptive(message: str) -> bool:
    first_line = (message or "").strip().splitlines()[0].strip().lower() if message else ""
    if len(first_line) < _MIN_MESSAGE_LENGTH:
        return False
    return first_line not in _GENERIC_MESSAGES


async def _default_fetch(path: str) -> object | None:
    """GET api.github.com AUTHENTIFIÉ (GITHUB_TOKEN, déjà existant) -- rate
    limit 5000/h, confirmé par appel réel capable de lire un dépôt public
    tiers. Dégradation gracieuse : toute panne -> None, jamais une exception."""
    import httpx

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{_GITHUB_API}{path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=headers)
    if r.status_code != 200:
        return None
    return r.json()


async def gather_github_substance_facts(
    repo_url: str | None, *, fetch=None, now: datetime | None = None,
) -> GithubSubstanceFacts:
    """Récolte best-effort la substance du développement récent. ``fetch``
    injectable pour les tests offline (défaut : appel authentifié réel).
    Toute indisponibilité -> ``available=False``, jamais une donnée inventée."""
    from aria_core.services.project_activity import parse_github_repo

    parsed = parse_github_repo(repo_url)
    if not parsed:
        return GithubSubstanceFacts(available=False, error="URL GitHub introuvable ou invalide")
    owner, repo = parsed
    fetch = fetch or _default_fetch
    now = now or datetime.now(timezone.utc)

    since = (now - timedelta(days=_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        commits_list = await fetch(
            f"/repos/{owner}/{repo}/commits?per_page={_MAX_COMMITS_ANALYZED}&since={since}"
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, jamais bloquant
        logger.info("github_substance: liste commits %s/%s échouée (%s)", owner, repo, exc)
        return GithubSubstanceFacts(available=False, error=f"liste des commits indisponible ({exc})")
    if not isinstance(commits_list, list) or not commits_list:
        return GithubSubstanceFacts(available=False, error="aucun commit trouvé ou dépôt inaccessible")

    shas = [c.get("sha") for c in commits_list if isinstance(c, dict) and c.get("sha")]

    # Garde-fou précoce (revue croisée externe, 23/07) : sous ce nombre de
    # commits BRUTS (avant même de savoir combien sont techniques), le dépôt
    # est manifestement trop peu actif -- inutile de payer le moindre appel
    # détail (un par commit) pour un repo quasi mort.
    if len(shas) < _MIN_RAW_COMMITS_BEFORE_DETAIL:
        return GithubSubstanceFacts(
            available=False,
            error=f"seulement {len(shas)} commit(s) brut(s) sur {_WINDOW_DAYS}j (< {_MIN_RAW_COMMITS_BEFORE_DETAIL} requis)",
        )

    technical_commits = 0
    total_technical_lines = 0
    has_tests = False
    categories: set[str] = set()
    descriptive_count = 0
    commit_days: set[str] = set()
    earliest: datetime | None = None
    latest: datetime | None = None
    analyzed = 0

    for sha in shas:
        try:
            detail = await fetch(f"/repos/{owner}/{repo}/commits/{sha}")
        except Exception as exc:  # noqa: BLE001 — un échec isolé n'invalide pas les autres
            logger.info("github_substance: détail commit %s échoué (%s)", sha, exc)
            continue
        if not isinstance(detail, dict):
            continue
        analyzed += 1

        commit_meta = detail.get("commit") or {}
        message = commit_meta.get("message") or ""
        if _message_is_descriptive(message):
            descriptive_count += 1

        date_str = (commit_meta.get("committer") or {}).get("date") or (commit_meta.get("author") or {}).get("date")
        if date_str:
            try:
                dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                commit_days.add(dt.date().isoformat())
                earliest = dt if earliest is None or dt < earliest else earliest
                latest = dt if latest is None or dt > latest else latest
            except (ValueError, TypeError):
                pass

        files = detail.get("files") or []
        technical_lines_this_commit = 0
        is_technical = False
        for f in files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if _is_test_file(filename):
                has_tests = True
            if _is_technical_file(filename):
                is_technical = True
                technical_lines_this_commit += int(f.get("additions") or 0) + int(f.get("deletions") or 0)
                category = _file_category(filename)
                if category:
                    categories.add(category)
        if is_technical:
            technical_commits += 1
            total_technical_lines += technical_lines_this_commit

    if analyzed == 0:
        return GithubSubstanceFacts(available=False, error="aucun détail de commit accessible")

    code_ratio = (technical_commits / analyzed) if analyzed else None
    avg_diff_size = (total_technical_lines / technical_commits) if technical_commits else None
    message_quality_score = (descriptive_count / analyzed) if analyzed else None

    regularity_score = None
    if earliest is not None and latest is not None:
        span_days = max(1, (latest - earliest).days + 1)
        regularity_score = min(1.0, len(commit_days) / span_days)

    return GithubSubstanceFacts(
        commits_analyzed=analyzed,
        technical_commits=technical_commits,
        code_ratio=code_ratio,
        avg_diff_size=avg_diff_size,
        has_tests=has_tests,
        distinct_categories=len(categories),
        regularity_score=regularity_score,
        message_quality_score=message_quality_score,
        available=True,
    )
