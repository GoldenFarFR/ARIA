# collegue-memoire

Mémoire partagée Sylvain × assistant IA — **un fichier** : [`COLLEGUE.md`](COLLEGUE.md)

## Nouveau PC (une fois)

```bash
git clone https://github.com/GoldenFarFR/collegue-memoire.git "%USERPROFILE%\projets\collegue-memoire"
```

Puis copier la règle Cursor (si pas déjà en place) :

```
copy "%USERPROFILE%\projets\collegue-memoire\.cursor\rules\collegue-memoire.md" "%USERPROFILE%\.cursor\rules\"
```

Pour Grok : même règle dans `%USERPROFILE%\.grok\rules\` (copier depuis ce repo).

## Avant de travailler

```bash
cd "%USERPROFILE%\projets\collegue-memoire"
git pull
```

## Après une session utile (l'assistant le fait)

```bash
git add COLLEGUE.md projets/
git commit -m "Mise à jour mémoire collègue"
git push
```