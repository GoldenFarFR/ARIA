# Boîte de dépôt de connaissance ARIA

> Dépose ici des notes brutes (texte, faits, méthodologie) destinées à enrichir la
> connaissance réelle d'ARIA. Un fichier par note, nom libre (ex. `2026-07-09-methode-x.md`).

## Comment ça marche

1. Tu déposes un fichier `.md` ou `.txt` ici (via un commit, ou en demandant à Claude Code
   de le faire pour toi).
2. Le cycle heartbeat `knowledge_inbox_cycle` (gaté OFF par défaut,
   `ARIA_KNOWLEDGE_INBOX_ENABLED`) le lit et **propose** — jamais n'impose — comment
   l'intégrer dans les vrais fichiers de connaissance d'ARIA (`packages/aria-core/src/aria_core/knowledge/*.yaml`,
   `truth_ledger/canonical_facts.yaml`). La proposition part comme **issue GitHub**
   (label `aria-knowledge-proposal`), jamais un commit ni une fusion autonome.
3. Toi (ou Claude Code) tu relis la proposition, tu valides ou ajustes, puis tu l'intègres
   toi-même dans le fichier de connaissance concerné.
4. Chaque note n'est traitée qu'**une seule fois** (ARIA garde en mémoire ce qu'elle a déjà
   vu) — inutile de la retirer une fois proposée.

## Pourquoi pas automatique de bout en bout

Ces fichiers sont ce qu'ARIA cite comme des faits établis en conversation — une donnée mal
filtrée qui s'y glisse est plus dangereuse qu'un bug de code ordinaire. La revue humaine
reste le filtre, comme pour toute proposition de code d'ARIA (`code_proposal.py`).

## Ce que ce dossier N'EST PAS

- Pas un espace pour `CLAUDE.md` — ce fichier brief Claude Code, jamais ARIA elle-même.
- Pas un espace pour du code — pour ça, `code_proposal.py` (ARIA) ou une session Claude
  Code (toi/moi) directement.
