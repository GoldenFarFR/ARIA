# Coffre multi-PC — top gratuit GoldenFar

> Objectif : **memes cles sur tous tes PC**, zero secret dans GitHub, gratuit.

## Stack recommandee (3 couches)

| Couche | Outil | Gratuit | Role |
|--------|-------|---------|------|
| **1 — Sync auto** | [Syncthing](https://syncthing.net/) | Oui | Synchronise `vault` entre PC en P2P chiffre |
| **2 — Mots de passe** | [Bitwarden](https://bitwarden.com/) | Oui | Login GitHub, IONOS, Stripe + mot de passe sauvegarde `.gfv` |
| **3 — Secours** | `export-vault-encrypted.ps1` | Oui | Fichier `.gfv` sur USB si Syncthing indisponible |

**Render** reste la copie prod (24/7) — pas besoin que ton PC soit allume.

---

## PC principal — Syncthing (une fois)

```powershell
winget install Syncthing.Syncthing
cd projets\aria-vanguard\operator
.\setup-syncthing-vault.ps1 -OpenGui
```

Le script démarre Syncthing, ajoute le dossier `goldenfar-vault`, affiche l’**ID appareil** à copier sur l’autre PC.

## PC secondaire — Syncthing

1. Meme installation Syncthing
2. **Ajouter un appareil distant** : coller l'ID du PC principal
3. Quand il propose le dossier `goldenfar-vault` → **Accepter**
4. Chemin local : `%LOCALAPPDATA%\GoldenFar\vault` (identique)
5. Puis :

```powershell
cd projets\aria-vanguard\operator
git pull
.\new-pc.ps1
.\check-aria-status.ps1
```

Les scripts trouvent le coffre tout seuls (`GOLDENFAR_VAULT`).

---

## Sans Syncthing (USB / fichier)

**PC A — export :**

```powershell
.\export-vault-encrypted.ps1
# -> Desktop\goldenfar-vault-YYYY-MM-DD.gfv
```

Copie le `.gfv` sur clé USB. Mot de passe dans **Bitwarden** (note securisee).

**PC B — import :**

```powershell
.\import-vault-encrypted.ps1 -InFile E:\goldenfar-vault-2026-06-19.gfv
.\new-pc.ps1
```

---

## Bitwarden (gratuit) — quoi y mettre

| Entree | Exemple |
|--------|---------|
| Compte GitHub | login + 2FA |
| IONOS | login domaine |
| Stripe | login dashboard |
| Render | login |
| Note securisee | « Passphrase sauvegarde goldenfar-vault » |
| Note securisee | « Syncthing ID PC bureau / PC maison » |

**Ne pas** dupliquer tout `production.env` dans Bitwarden si Syncthing tourne — le coffre fichier suffit pour les scripts.

---

## A eviter (meme gratuit)

| Methode | Pourquoi |
|---------|----------|
| OneDrive / Google Drive **non chiffre** | Le cloud voit tes cles |
| GitHub (meme prive) pour `production.env` | Historique Git, fuites scan |
| Meme cle copiee a la main sur 3 PC | Erreurs, oublis, chat |

---

## Checklist nouveau PC (resume)

1. `git clone` repos GoldenFar (scripts + code)
2. Syncthing **ou** `import-vault-encrypted.ps1`
3. `aria-skills\scripts\install.ps1`
4. `.\check-aria-status.ps1` → OK

Prod deja en ligne sur Render — pas besoin de re-saisir les cles si le coffre est a jour.