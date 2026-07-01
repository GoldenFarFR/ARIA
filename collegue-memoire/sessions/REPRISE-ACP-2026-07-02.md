# Reprise — Intégration ACP v2 ARIA (2026-07-02)

> **Lire en priorité** si la session reprend sur ACP / Virtuals / marketplace.

---

## Où on s'est arrêté (2026-07-02 ~00h01)

### Fait ✅

| Étape | Détail |
|-------|--------|
| Code aria-core | `acp_cli.py`, `acp_provider_skill.py`, `acp_client_skill.py`, YAML config/offerings |
| Brain | Skill `acp_marketplace` — `acp status`, `traiter jobs acp`, browse |
| Tests | `test_acp_skills.py` — **7/7 OK** |
| Windows fix | `acp_cli` utilise `acp.CMD` (subprocess) |
| Listener | `vanguard/operator/acp-events-listener.ps1` — **mode legacy** (v2 → HTTP 500 Virtuals) |
| Poll local | `ARIA_ACP_PROVIDER_ENABLED=true` dans vault `local.env` + `sync-local.ps1` |
| Bot local | API `:8000` redémarrée — health `aria_acp` OK, heartbeat `acp_provider_poll` **enabled** |
| Smoke | Chat `acp status` + `traiter jobs acp` → 0 event (normal) |
| Pitfalls | `acp-keychain-local-only`, `acp-events-listen-v2-500` |

### Pas fait ⏳

| Étape | Action reprise |
|-------|----------------|
| **Commit + PR** | Tout le lot ACP est **local non commité** (git dirty) |
| **Deploy prod** | Volontairement reporté |
| **Job payant test** | Attendre feu vert Sylvain |
| **Parser legacy events** | À ajuster quand 1er vrai event arrive |
| **Deliverables riches** | Scan heuristique seulement — brancher vrai audit |

---

## État runtime PC-SYLVAIN

```
Agent ACP : Aria Vanguard ZHC (019f0522-b57b-7e8e-a70a-aab2070e070e)
Offerings : analyse_lite_x1 (1.99), analyse_full_x1 (4.99)
Events file : %LOCALAPPDATA%\GoldenFar\acp-events.jsonl
Listener log : %LOCALAPPDATA%\GoldenFar\acp-listener.log
```

Relancer listener si besoin :
```powershell
& "$env:USERPROFILE\GitHub-Repos\ARIA\vanguard\operator\acp-events-listener.ps1" -Background -Mode legacy
```

Relancer bot local :
```powershell
cd "$env:USERPROFILE\GitHub-Repos\ARIA\vanguard\operator"
.\sync-local.ps1
cd ..\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## Prochaine session (ordre recommandé)

1. `git status` — vérifier fichiers ACP vs bruit (site.config, render.yaml…)
2. **Commit + PR** lot ACP uniquement
3. Smoke prod **non** — rester local jusqu'à 1 job test validé
4. Si job arrive : vérifier format event legacy → `acp_provider_skill.py`
5. Enrichir deliverables avant jobs payants réels

---

## Fichiers clés (non commités au 2026-07-02)

- `packages/aria-core/src/aria_core/skills/acp_*.py`
- `packages/aria-core/src/aria_core/knowledge/acp_*.yaml`
- `packages/aria-core/tests/test_acp_skills.py`
- `vanguard/operator/acp-events-listener.ps1`
- `skills/scripts/prepare-acp-v2-integration.ps1`
- `collegue-memoire/ARIA_ACP_v2_Integration_Prompt.txt`

Prompt préparation : `skills/scripts/prepare-acp-v2-integration.ps1`