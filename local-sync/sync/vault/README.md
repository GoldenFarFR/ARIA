# Coffre chiffre (toutes les cles)

Fichier : `goldenfar-vault.gfv` — archive AES du coffre `%LOCALAPPDATA%\GoldenFar\vault`.

Contient (dechiffre localement uniquement) :

- `production.env`, `local.env`, `vanguard.env`
- `keys/render.api-key`, `keys/ionos.api-key`
- `stripe/recovery-codes.txt`

**Jamais en clair dans Git** — seulement le `.gfv` chiffre (repo prive OK).

Mot de passe : le meme sur les 2 PC (note dans Bitwarden).  
Variable optionnelle : `GOLDENFAR_VAULT_SYNC_PASS` (evite la saisie interactive).