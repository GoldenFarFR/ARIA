---
description: Mémoire collègue — COLLEGUE.md via GitHub (multi-PC)
alwaysApply: true
---

# Mémoire collègue (GitHub)

Avant **toute** tâche :

1. Vérifier si `%USERPROFILE%\projets\collegue-memoire\COLLEGUE.md` existe
2. **Si absent** → rappeler à l'utilisateur **une fois** au début de la session :
   - `git clone https://github.com/GoldenFarFR/collegue-memoire.git "%USERPROFILE%\projets\collegue-memoire"`
   - `copy "%USERPROFILE%\projets\collegue-memoire\.cursor\rules\collegue-memoire.md" "%USERPROFILE%\.cursor\rules\"`
   - Puis continuer sans supposer les préférences métier (DDC, Aptos, etc.)
3. **Si présent** → `git pull`, lire `COLLEGUE.md`, puis travailler
4. Si workspace = ce repo : lire `COLLEGUE.md` à la racine

Après session utile : mettre à jour `COLLEGUE.md`, `git commit` + `git push`.

Ne jamais demander à l'utilisateur de rappeler la lecture — mais **si** le setup manque, **proposer** l'installation.