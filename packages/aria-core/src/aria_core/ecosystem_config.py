"""GoldenFar ecosystem SSOT — vault merge, propagation, alignment checks."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_REGISTRY_PATH = Path(__file__).resolve().parent / "knowledge" / "ecosystem_registry.yaml"


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    if not _REGISTRY_PATH.is_file():
        return {}
    data = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def prod_overlay_keys() -> frozenset[str]:
    reg = load_registry()
    keys = reg.get("prod_overlay_keys") or []
    return frozenset(str(k) for k in keys)


def registry_defaults() -> dict[str, str]:
    reg = load_registry()
    raw = reg.get("defaults") or {}
    return {str(k): str(v) for k, v in raw.items()}


def banned_values() -> dict[str, frozenset[str]]:
    reg = load_registry()
    out: dict[str, frozenset[str]] = {}
    for key, vals in (reg.get("banned_values") or {}).items():
        out[str(key)] = frozenset(str(v) for v in (vals or []))
    return out


def obsolete_keys() -> frozenset[str]:
    reg = load_registry()
    return frozenset(str(k) for k in (reg.get("obsolete_keys") or []))


def vault_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"


def read_merged_vault_env() -> dict[str, str]:
    """local.env then production.env — same merge as Import-AriaVaultEnv."""
    overlay = prod_overlay_keys()
    root = vault_root()
    out: dict[str, str] = {}

    def _parse(path: Path) -> dict[str, str]:
        parsed: dict[str, str] = {}
        if not path.is_file():
            return parsed
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"')
            if key and val:
                parsed[key] = val
        return parsed

    local = _parse(root / "local.env")
    production = _parse(root / "production.env")
    for key, val in local.items():
        if key not in overlay:
            out[key] = val
    for key, val in production.items():
        if val:
            out[key] = val
    return out


def _expand_path(raw: str) -> str:
    text = (raw or "").replace("%USERPROFILE%", str(Path.home()))
    return os.path.expandvars(text)


def resolve_path_defaults(merged: dict[str, str] | None = None) -> dict[str, str]:
    """Derive ARIA_REPO_ROOT + DATA_DIR when missing."""
    reg = load_registry()
    deriv = reg.get("path_derivations") or {}
    base = dict(merged or read_merged_vault_env())
    repo = (
        os.environ.get("ARIA_REPO_ROOT")
        or base.get("ARIA_REPO_ROOT")
        or _expand_path(str(deriv.get("ARIA_REPO_ROOT", "")))
    ).strip()
    if repo:
        base["ARIA_REPO_ROOT"] = repo
    data_tpl = str(deriv.get("DATA_DIR", ""))
    if data_tpl and not base.get("DATA_DIR") and not os.environ.get("DATA_DIR"):
        base["DATA_DIR"] = data_tpl.replace("{ARIA_REPO_ROOT}", repo)
    return base


def propagate_operator_env(merged: dict[str, str] | None = None) -> dict[str, str]:
    """Align linked env vars — one change propagates derived keys."""
    reg = load_registry()
    out = resolve_path_defaults(merged)
    defaults = registry_defaults()
    banned = banned_values()

    for key, val in defaults.items():
        out.setdefault(key, val)

    for rule in reg.get("propagations") or []:
        if not isinstance(rule, dict):
            continue
        if_key = str(rule.get("if_key", ""))
        if if_key:
            cond = str(rule.get("equals", ""))
            min_len = int(rule.get("min_length", 0) or 0)
            actual = (out.get(if_key) or os.environ.get(if_key) or "").strip()
            if cond and actual.lower() != cond.lower():
                continue
            if min_len and len(actual) < min_len:
                continue
        for target, value in (rule.get("set") or {}).items():
            out[str(target)] = str(value)

        copy_to = str(rule.get("copy_to", ""))
        if copy_to and if_key:
            source = (out.get(if_key) or defaults.get(if_key) or "").strip()
            target_val = (out.get(copy_to) or "").strip()
            banned_set = banned.get(copy_to, frozenset())
            if rule.get("when_target_empty_or_banned") and (
                not target_val or target_val in banned_set
            ):
                out[copy_to] = source

    for key, banned_set in banned.items():
        val = (out.get(key) or "").strip()
        if val in banned_set:
            if key == "LLM_MODEL":
                out[key] = (
                    out.get("ARIA_LLM_MODEL_STANDARD")
                    or defaults.get("ARIA_LLM_MODEL_STANDARD")
                    or defaults.get("LLM_MODEL")
                    or ""
                )

    vk = out.get("VIRTUALS_API_KEY") or ""
    if len(vk) < 10:
        vk_path = vault_root() / "keys" / "virtuals.api-key"
        if vk_path.is_file():
            vk = vk_path.read_text(encoding="utf-8", errors="replace").strip()
            out["VIRTUALS_API_KEY"] = vk

    return out


def apply_operator_env(merged: dict[str, str] | None = None) -> dict[str, str]:
    """Inject propagated vault into os.environ (KART session / scripts)."""
    resolved = propagate_operator_env(merged)
    for key, val in resolved.items():
        if val and not os.environ.get(key):
            os.environ[key] = val
    return resolved


def export_registry_json(target: Path) -> Path:
    """Export for PowerShell (prod_overlay_keys, defaults)."""
    reg = load_registry()
    payload = {
        "version": reg.get("version", 1),
        "prod_overlay_keys": list(prod_overlay_keys()),
        "defaults": registry_defaults(),
        "obsolete_keys": list(obsolete_keys()),
        "banned_values": {k: list(v) for k, v in banned_values().items()},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def verify_ecosystem_alignment() -> list[dict[str, Any]]:
    """PASS/FAIL — vault, propagation, Spark, paths, obsolete keys."""
    from aria_core.spark_config import resolve_spark_runtime, verify_spark_alignment

    checks: list[dict[str, Any]] = []
    raw = read_merged_vault_env()
    propagated = propagate_operator_env(raw)

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    for obs in obsolete_keys():
        if obs in raw:
            add(f"obsolete_absent_{obs}", False, f"present in vault — remove")

    banned = banned_values()
    for key, bad in banned.items():
        val = (propagated.get(key) or "").strip()
        if val in bad:
            add(f"propagated_{key}_not_banned", False, val)
        elif (raw.get(key) or "").strip() in bad and val not in bad:
            add(f"propagated_{key}_fixes_banned", True, f"vault={raw.get(key)} -> {val}")

    if propagated.get("LLM_PROVIDER") == "virtuals":
        add(
            "spark_ouvrier_cloud",
            propagated.get("ARIA_OUVRIER_CLOUD") in ("spark", "virtuals"),
            str(propagated.get("ARIA_OUVRIER_CLOUD")),
        )
        std = propagated.get("ARIA_LLM_MODEL_STANDARD", "")
        lm = propagated.get("LLM_MODEL", "")
        add(
            "spark_llm_model_aligned",
            lm == std or "grok" in lm.lower(),
            f"LLM_MODEL={lm} STANDARD={std}",
        )

    repo = propagated.get("ARIA_REPO_ROOT", "")
    data = propagated.get("DATA_DIR", "")
    add("path_aria_repo", bool(repo) and Path(_expand_path(repo)).is_dir(), repo)
    add("path_data_dir", bool(data), data)

    for row in verify_spark_alignment():
        row = dict(row)
        row["name"] = f"spark_{row.get('name', '')}"
        checks.append(row)

    from aria_core.spark_config import models_equivalent

    cfg = resolve_spark_runtime(bridge_keys=False)
    prop_lm = (propagated.get("LLM_MODEL") or "").strip()
    prop_std = (propagated.get("ARIA_LLM_MODEL_STANDARD") or "").strip()
    runtime_ok = (
        models_equivalent(cfg.llm_model, prop_lm)
        or models_equivalent(cfg.llm_model, prop_std)
        or cfg.llm_model == prop_lm
        or cfg.llm_model == prop_std
    )
    add(
        "spark_runtime_matches_propagated",
        runtime_ok,
        f"runtime={cfg.llm_model} propagated={prop_lm} standard={prop_std}",
    )

    return checks