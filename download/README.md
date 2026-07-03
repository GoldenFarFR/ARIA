# Download — boîte de dépôt Sylvain

Dépose ici des fichiers **réels** (pas de raccourcis `.url` Messenger).

## Comment déposer

1. Télécharger l’attachement (ou copier depuis `Downloads`)
2. Coller le fichier dans ce dossier
3. Ouvrir une session Grok/Cursor — l’ouvrier traite **automatiquement** au démarrage

## Ce que l’ouvrier fait

| Type | Action |
|------|--------|
| Code (`.py`, `.ts`, …) | Lire, intégrer ou proposer PR selon ta demande implicite |
| Docs (`.pdf`, `.docx`, `.md`) | Résumer, extraire décisions, mettre à jour mémoire si pertinent |
| Images | Analyser (avatar, bannière, maquette) |
| Archives (`.zip`) | Extraire, trier, traiter le contenu |
| `.url` / liens blob | **Rejet** — pas de contenu lisible |

Fichiers traités → `processed/` · invalides → `rejected/`

## État

Voir `INBOX-STATE.json` (historique traitements).