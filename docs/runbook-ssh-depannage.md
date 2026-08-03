# Runbook — dépannage SSH VPS (clé cassée/perdue/mal copiée)

> **Repo PUBLIC — jamais d'IP/secret/accès en clair ici.** Cette procédure est volontairement
> générique (pas d'IP/nom réel) — extrait de `CLAUDE.md` le 03/08 (passe de compaction).

Si l'accès SSH à un VPS depuis un poste opérateur casse (clé compromise, clé mal copiée, accès perdu),
suivre cet ordre — **ne jamais rien supprimer/révoquer avant d'avoir confirmé qu'un accès de
remplacement fonctionne réellement** (même règle que toute rotation de secret) :

1. **Générer une clé propre** : `ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\<nom>"`. Le nom du
   fichier n'a aucune importance fonctionnelle pour OpenSSH — préférer un nom **sans espace** (voir
   point 5).
2. **Trouver un accès de secours** pour poser la nouvelle clé publique sur le VPS, dans cet ordre de
   préférence : (a) une session Claude Code déjà active sur le VPS (accès shell direct, pas besoin de
   SSH) ; (b) un autre appareil déjà autorisé (récupérer sa clé privée depuis le gestionnaire de mots
   de passe, la rejouer temporairement sur le poste bloqué) ; (c) la console web de l'hébergeur (KVM/
   VNC — passe par le login système/PAM, **indépendant** du réglage SSH `PasswordAuthentication no`,
   sauf si le mot de passe root a aussi été verrouillé au niveau système, auquel cas seul le support de
   l'hébergeur peut aider) ; (d) en dernier recours, contacter le support de l'hébergeur.
   **Ne jamais cliquer sur une option de réinstallation d'image dans un panneau d'hébergeur** — ça
   efface le serveur entier.
3. **Ajouter la nouvelle clé publique** à `~/.ssh/authorized_keys` sur le VPS (append, toujours après
   une sauvegarde `cp` du fichier, jamais d'écrasement direct).
4. **Vérifier que le nouvel accès fonctionne** (nouvelle fenêtre de terminal) avant de retirer quoi
   que ce soit de `authorized_keys`.
5. **Pièges de copier-coller Windows rencontrés en pratique** :
   - Un copier-coller depuis un gestionnaire de mots de passe (champ texte libre/note, pas un type
     "Clé SSH" dédié) peut aplatir une clé privée multi-lignes en une seule ligne (retours à la ligne
     remplacés par des espaces) → `ssh` renvoie `invalid format`. Correctif : extraire uniquement les
     caractères base64 valides et reconstruire les 3 lignes (`BEGIN`/corps/`END`), écrire en ASCII sans
     BOM (`[System.IO.File]::WriteAllText(..., [System.Text.Encoding]::ASCII)`).
   - Un nom de fichier de clé **avec espace** casse `~/.ssh/config` (nécessite des guillemets autour du
     chemin `IdentityFile`) et casse aussi le client SSH interne de Claude Code (point suivant) →
     préférer un nom sans espace dès le départ.
   - Coller un bloc PowerShell multi-lignes (here-string `@"..."@`) dans un terminal peut s'exécuter
     ligne par ligne au lieu du bloc entier et casser le fichier généré → préférer une suite de
     commandes `Set-Content`/`Add-Content` (une ligne = une commande complète), plus robuste au collage.
   - Toujours corriger les permissions du fichier de clé sur Windows avant usage :
     `icacls <fichier> /inheritance:r` puis `icacls <fichier> /grant:r "<utilisateur>:(F)"` — utiliser
     `(F)` et non `(R)`, sinon impossible de corriger le fichier ensuite.
   - Le client SSH intégré à Claude Code (connexions distantes) n'est **pas** OpenSSH natif — il ne lit
     jamais `~/.ssh/config` et ne comprend pas le `~` sur Windows dans le champ "Fichier d'identité" :
     y renseigner le **chemin absolu complet** (`C:\Users\<utilisateur>\.ssh\<fichier>`), jamais `~/...`.
   - Claude Code peut réécrire `~/.ssh/config` en enregistrant sa propre configuration de connexion et
     supprimer une ligne `IdentityFile` ajoutée manuellement — revérifier `config` après tout
     enregistrement dans l'interface de connexion SSH de Claude Code (un agent SSH Windows ayant mémorisé
     la clé peut masquer temporairement le problème — `ssh` marche quand même sans `IdentityFile` tant
     que l'agent tourne, mais ce n'est pas fiable après un redémarrage : remettre la ligne quand même).
6. **Gestionnaire de mots de passe (type Bitwarden)** : stocker une clé SSH dans le type d'élément
   dédié "Clé SSH" (pas une note/champ personnalisé texte libre) pour éviter le point 5 — ce type
   préserve le format correctement à l'export/copie. Si l'outil ne permet que de **générer** une
   nouvelle clé (pas d'import), ajouter cette clé générée au VPS puis migrer dessus (mêmes étapes 2-4),
   plutôt que de forcer un import qui échoue.
7. **Une fois le nouvel accès confirmé et en usage réel**, retirer l'ancienne clé de
   `authorized_keys`, supprimer les fichiers de clé locaux devenus inutiles, et mettre à jour/supprimer
   toute entrée obsolète dans le gestionnaire de mots de passe.

**Rappel sécurité** : si le contenu d'une clé privée a été affiché en clair quelque part (capture
d'écran, chat, log), la traiter comme compromise immédiatement — générer une nouvelle paire, ne jamais
réutiliser l'ancienne au-delà d'un pont temporaire vers son remplacement. **IP et détails d'accès VPS
restent privés, dans `aria-ops`** — cette procédure est volontairement générique (pas d'IP/nom réel).
