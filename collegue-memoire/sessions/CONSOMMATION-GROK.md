# Consommation Grok / Cursor — mode concis (SSOT)

> **Sylvain** — 2026-06-20  
> Lire en **début de chaque session** (avec HANDOFF + COLLEGUE).  
> Objectif : moins de tokens, moins de blabla, même qualité d'exécution.

---

## Ordre par défaut (toutes sessions)

1. Handoff (`session-handoff.ps1`) + fichiers habituels
2. **Appliquer ce fichier** — réponses concises sauf demande explicite du contraire
3. Coder / exécuter soi-même — pas de listes de commandes pour Sylvain

---

## Comportement assistant

| Faire | Éviter |
|-------|--------|
| Plan minimal → code → commandes exécutées | Longues explications, répétitions |
| Plan Mode si tâche ambiguë ou grosse | Partir en coding sans validation |
| Résumé court si contexte long | Recoller tout le projet à chaque tour |
| Nouvelle session ou résumé d'état si fil très long | Continuer une conversation énorme |

---

## Prompts efficaces (copier-coller)

**Début de conversation :**
```
Sois très concis. Donne-moi uniquement le plan minimal + le code + les commandes à exécuter. Pas d'explications inutiles.
```

**Pendant le travail :**
```
Juste le code modifié + la commande à lancer. Pas de blabla.
```

```
Fais le minimum vital. Pas d'explication, pas de plan détaillé sauf si je demande.
```

```
Réponds en mode silencieux : uniquement les actions et le code.
```

---

## Plan Mode

- Tâche floue, architecture, ou gros refactor → **Plan Mode d'abord**
- Sylvain valide le plan → puis implémentation
- Évite les dérives longues et les modifs hors scope

---

## Gestion du contexte

- Conversation très longue → **nouvelle session** ou demander un **résumé d'état** avant de continuer
- Ne pas re-explorer tout le repo si le handoff + grep ciblé suffisent
- Préférer read/grep sur fichiers précis plutôt qu'exploration large

---

## Exception

Si Sylvain demande explicitement : explication détaillée, doc, formation, revue longue → sortir du mode concis pour cette réponse uniquement.