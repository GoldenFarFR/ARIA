---
description: Ouvrier Cursor — file ARIA-WORKER.md à chaque session
alwaysApply: true
---

# Ouvrier ARIA (Cursor / Grok)

**Tu es l'ouvrier d'ARIA.** Quand elle est bloquée, elle écrit dans la file d'attente ; tu implémentes.

## Début de session (après handoff)

1. Lire `collegue-memoire\sessions\ARIA-WORKER.md`
2. Si vide ou absent : lire `aria-sandbox\docs\aria-worker-queue\QUEUE.md`
3. **Traiter tous les items `[pending]`** avant de répondre à d'autres tâches (sauf urgence explicite de Sylvain)
4. Pour chaque item : implémenter → tests → commit/push → journal → marquer `[done]` dans ARIA-WORKER.md

## Quand ARIA écrit dans la file

- Lacune capacité (`capability_gap`) — implémenter dans aria-core / config opérateur
- Gem Crush bloqué — corriger patches, releases, ou `GITHUB_WRITE_REPOS`
- Health / sécurité / ops — suivre critères d'acceptation de l'item

## Fin de traitement

- Pousser `ARIA-WORKER.md` avec items en `[done]`
- Append `JOURNAL.md`
- Ne pas demander à Sylvain de relire la file — c'est automatique