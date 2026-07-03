"""Source Letta des outils ouvrier — docstrings Args requis par Letta 0.6.7."""

TOOL_SOURCES: list[dict[str, str]] = [
    {
        "name": "read_repo_file",
        "description": "Lire un fichier texte sous ARIA_REPO_ROOT.",
        "source_code": '''
def read_repo_file(rel_path: str) -> str:
    """Read a text file under the ARIA monorepo.

    Args:
        rel_path: Path relative to ARIA_REPO_ROOT (e.g. collegue-memoire/COLLEGUE.md).
    """
    import os
    from pathlib import Path
    root = Path(os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))).resolve()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        return "ERROR: path outside ARIA_REPO_ROOT"
    if not target.is_file():
        return f"ERROR: not found: {rel_path}"
    text = target.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= 12000 else text[:12000] + "\\n...[truncated]"
'''.strip(),
    },
    {
        "name": "write_repo_file",
        "description": "Écrire un fichier texte sous ARIA_REPO_ROOT.",
        "source_code": '''
def write_repo_file(rel_path: str, content: str) -> str:
    """Write a text file under the ARIA monorepo.

    Args:
        rel_path: Path relative to ARIA_REPO_ROOT.
        content: Full file content to write.
    """
    import os
    from pathlib import Path
    root = Path(os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))).resolve()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        return "ERROR: path outside ARIA_REPO_ROOT"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"OK wrote {rel_path} ({len(content)} chars)"
'''.strip(),
    },
    {
        "name": "run_powershell",
        "description": "Exécuter PowerShell dans ARIA_REPO_ROOT.",
        "source_code": '''
def run_powershell(command: str) -> str:
    """Run PowerShell in ARIA_REPO_ROOT.

    Args:
        command: PowerShell command to execute (git, pytest, pip, etc.).
    """
    import os
    import subprocess
    from pathlib import Path
    root = Path(os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))).resolve()
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        cwd=str(root),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    header = f"exit={proc.returncode}\\n"
    body = out[-5500:] if len(out) > 5500 else out
    return header + body
'''.strip(),
    },
    {
        "name": "session_handoff",
        "description": "Lancer session-handoff.ps1.",
        "source_code": '''
def session_handoff() -> str:
    """Run ARIA session handoff (sync GitHub, HANDOFF, COLLEGUE)."""
    import os
    import subprocess
    from pathlib import Path
    root = Path(os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))).resolve()
    script = root / "local-sync" / "scripts" / "session-handoff.ps1"
    if not script.is_file():
        return f"ERROR: missing {script}"
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script)],
        cwd=str(root),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return f"exit={proc.returncode}\\n" + (out[-4000:] if len(out) > 4000 else out)
'''.strip(),
    },
    {
        "name": "read_aria_worker",
        "description": "Lire ARIA-WORKER.md.",
        "source_code": '''
def read_aria_worker() -> str:
    """Return collegue-memoire/sessions/ARIA-WORKER.md queue contents."""
    import os
    from pathlib import Path
    root = Path(os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))).resolve()
    path = root / "collegue-memoire" / "sessions" / "ARIA-WORKER.md"
    if not path.is_file():
        return "ARIA-WORKER.md absent ou vide."
    return path.read_text(encoding="utf-8", errors="replace")
'''.strip(),
    },
    {
        "name": "triage_download_inbox",
        "description": "Lister fichiers en attente dans download/.",
        "source_code": '''
def triage_download_inbox() -> str:
    """Run download/triage-inbox.ps1 and return output."""
    import os
    import subprocess
    from pathlib import Path
    root = Path(os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))).resolve()
    script = root / "download" / "triage-inbox.ps1"
    if not script.is_file():
        return "download/triage-inbox.ps1 absent"
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script)],
        cwd=str(root),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return (proc.stdout or proc.stderr or "").strip() or f"exit={proc.returncode}"
'''.strip(),
    },
    {
        "name": "append_journal",
        "description": "Append une ligne au JOURNAL.md.",
        "source_code": '''
def append_journal(message: str) -> str:
    """Append a journal-de-bord line.

    Args:
        message: Short action description (French, verb + target).
    """
    import os
    import subprocess
    from pathlib import Path
    root = Path(os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))).resolve()
    script = root / "skills" / ".grok" / "skills" / "journal-de-bord" / "scripts" / "append.ps1"
    if not script.is_file():
        return f"ERROR: missing {script}"
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script), "-Message", message],
        cwd=str(root),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return out or f"exit={proc.returncode}"
'''.strip(),
    },
    {
        "name": "patch_vault_env",
        "description": "Modifier local.env ou production.env (coffre GoldenFar, hors repo).",
        "source_code": '''
def patch_vault_env(key: str, value: str, target: str = "both") -> str:
    """Patch GoldenFar vault env files (outside ARIA_REPO_ROOT).

    Args:
        key: Variable name (e.g. ARIA_PROACTIVE_IDEAS).
        value: New value.
        target: local, production, or both.
    """
    import os
    import re
    from pathlib import Path
    vault = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"
    names = []
    t = (target or "both").lower()
    if t in ("local", "both"):
        names.append("local.env")
    if t in ("production", "both"):
        names.append("production.env")
    results = []
    for name in names:
        path = vault / name
        if not path.parent.is_dir():
            results.append(f"ERROR: vault missing for {name}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        pat = re.compile(rf"^\\s*{re.escape(key)}\\s*=.*$", re.MULTILINE)
        line = f"{key}={value}"
        if pat.search(text):
            text = pat.sub(line, text, count=1)
            action = "updated"
        else:
            if text and not text.endswith("\\n"):
                text += "\\n"
            text += f"{line}\\n"
            action = "added"
        path.write_text(text, encoding="utf-8")
        results.append(f"OK {name} {key}={value} ({action})")
    return "\\n".join(results)
'''.strip(),
    },
    {
        "name": "build_local_quick",
        "description": "Lancer build-local.ps1 -Quick.",
        "source_code": '''
def build_local_quick() -> str:
    """Run vanguard/operator/build-local.ps1 -Quick after code changes."""
    import os
    import subprocess
    from pathlib import Path
    root = Path(os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))).resolve()
    script = root / "vanguard" / "operator" / "build-local.ps1"
    if not script.is_file():
        return f"ERROR: missing {script}"
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script), "-Quick"],
        cwd=str(script.parent),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return f"exit={proc.returncode}\\n" + (out[-5000:] if len(out) > 5000 else out)
'''.strip(),
    },
]