# 📘 GUIDE COMPLET DE LA STRUCTURE DE TRAVAIL - PROJET ARIA
**Version :** 1.0  
**Date de création :** 29 juin 2026  
**Objectif de ce fichier :** Permettre à **n'importe quelle personne ou IA** de comprendre rapidement la structure du projet ARIA, les fichiers existants, le contexte déjà établi, et surtout **comment modifier/ajouter des choses** de manière cohérente et propre.

Ce guide est conçu pour être lu au début de chaque session de travail sur ARIA.

---

## 1. Vision Globale & Contexte Actuel (extrait de ARIA_MEMORY.txt)

### Objectif principal
Développer une **IA autonome professionnelle** (ARIA) avec :
- Mémoire puissante et persistante
- Auto-amélioration continue
- Haut niveau de réalisme et de fiabilité
- Compétences en analyse crypto (style VC), développement logiciel, et gestion de tâches complexes

**Objectif Business 2026 (Priorité n°1)** : Générer des revenus rapidement (idéalement d'ici 1-2 mois) de manière propre et professionnelle.

### Les 5 Domaines Prioritaires
1. Mémoire & Auto-amélioration du système
2. Codage / Développement logiciel
3. Gestion financière & Investissement
4. Créativité, Curiosité & Auto-critique
5. Intelligence Sociale & Communication

### Planning des Phases (simplifié)
- **Phase 0** (actuelle) : Structure de base + Mémoire simple
- **Phase 1** : Personnalité Pro uniquement
- **Phase 2** : Aria_Skill_Crypto (lancement visé : **2 juillet 2026**)
- Phases 3-7 : Émotions, Auto-Amélioration avancée, Voice, etc. (plus tard)

### Règles de Gouvernance Importantes
- **GoldenFarFr** = Décision finale sur tout
- **Grok** = Arbitre technique et superviseur
- **Aria** = Droit de proposition uniquement (pas de décision finale sur les sujets importants)
- Grok Build **n'a jamais** le droit de faire des commits ou push → tout doit être fait **manuellement**
- Quand une session atteint ~50 000 tokens → le signaler

### Garde-fous de Sécurité
- Aria ne prend **jamais** de décisions financières à la place de l'utilisateur
- Aria ne modifie **jamais** son propre code sans validation explicite

---

## 2. Fichiers Actuels et leur Signification

| Fichier                        | Rôle                                                                 | Comment le modifier ?                          | Priorité |
|--------------------------------|----------------------------------------------------------------------|------------------------------------------------|----------|
| `ARIA_MEMORY.txt`              | Mémoire persistante principale du projet. Contient toute la vision, les règles, les phases, les risques, etc. | Édition manuelle ou via skill `manage_memory` | Très haute |
| `Invest_Prompt_v4.txt`         | Template de prompt pour l'analyse d'investissement style VC         | Édition manuelle (versionné)                   | Haute    |
| `GUIDE_STRUCTURE_TRAVAIL_ARIA.md` | **Ce fichier** - Documentation vivante de la structure et du workflow | Mise à jour régulière quand la structure évolue | Haute    |

> **Règle d'or** : Avant toute modification importante, **lire ARIA_MEMORY.txt** pour rester aligné sur le contexte.

---

## 3. Structure de Dossiers Recommandée (à créer)

D'après l'alerte critique dans ARIA_MEMORY.txt :

```
ARIA/
├── core/
│   └── personality/
│       └── pro/                    ← Mode Pro uniquement pour l'instant
│           ├── base.md
│           ├── pro_mode.md
│           └── rules.md
├── skills/
│   ├── manage_memory/              ← Skill prioritaire
│   ├── crypto_analysis/            ← Préparer pour le 2 juillet
│   └── ...
├── memory/
│   ├── ARIA_MEMORY.txt             ← Mémoire principale (actuel)
│   └── archives/                   ← Anciennes versions
├── prompts/
│   ├── Invest_Prompt_v4.txt
│   └── ...
├── scripts/
│   └── PowerShell/                 ← Scripts d'orchestration (tool calling, etc.)
└── docs/
    └── GUIDE_STRUCTURE_TRAVAIL_ARIA.md
```

**Action immédiate recommandée :**
```powershell
# Créer la structure propre
New-Item -ItemType Directory -Path "core\personality\pro" -Force
New-Item -ItemType File -Path "core\personality\pro\base.md", "core\personality\pro\pro_mode.md", "core\personality\pro\rules.md"
```

---

## 4. Comment le Système Exécute les Actions quand l'IA Répond ? (Question Importante)

### Réponse claire et honnête :

**Actuellement dans ce setup ARIA KART V4.1-LOCAL :**

Le système n'a **pas encore** de mécanisme d'exécution d'actions/tools complètement implémenté de façon autonome. C'est précisément ce qu'il faut construire en Phase 0.

### Les 3 approches possibles (analyse) :

| Approche                              | Avantages                                      | Inconvénients                              | Recommandation |
|---------------------------------------|------------------------------------------------|--------------------------------------------|----------------|
| **1. Parser des tags spéciaux** `<tool name="xxx">...</tool>` | Simple à implémenter en PowerShell, très lisible, fonctionne avec n'importe quel modèle local | Nécessite un wrapper qui parse la sortie | **CHOIX RECOMMANDÉ pour commencer** |
| **2. Tool calling / Function calling natif** (format OpenAI-like) | Standard, puissant, supporté par beaucoup de modèles (Qwen, etc.) | Plus complexe à parser soi-même si Ollama ne le gère pas nativement | Bon pour plus tard |
| **3. Tout géré par scripts PowerShell qui lisent des fichiers** | Très simple, pas besoin de parser de tags | Moins élégant, moins "agentique", plus de latence | À éviter comme solution principale |

### Recommandation officielle pour ARIA :

**Utiliser l'approche n°1 (tags `<tool>`)** pour les 2-3 prochaines semaines.

**Pourquoi ?**
- Facile à implémenter avec PowerShell
- Très lisible dans les logs
- Permet de commencer rapidement le skill `manage_memory`
- Compatible avec le modèle `aria-qwen32b` actuel

**Exemple de format à utiliser :**
```xml
<tool name="manage_memory">
<action>update_section</action>
<section>Planning des Phases</section>
<content>Phase 2 terminée le 2 juillet...</content>
</tool>
```

Un script PowerShell principal (`aria_orchestrator.ps1`) :
1. Lance le modèle (via Ollama ou l'API hybride)
2. Capture la réponse complète
3. Parse les balises `<tool>...</tool>`
4. Exécute l'action correspondante (lecture/écriture de fichiers, appel d'autres scripts, etc.)
5. Réinjecte le résultat dans le contexte
6. Boucle jusqu'à ce que l'IA ne génère plus de tool calls

---

## 5. Comment Modifier les Choses Concrètement (Workflow PowerShell)

### 5.1 Modifier la Mémoire Principale
```powershell
# Ouvrir pour édition
notepad.exe "ARIA_MEMORY.txt"

# Ou avec VS Code si installé
code "ARIA_MEMORY.txt"

# Toujours faire une sauvegarde avant modification importante
Copy-Item "ARIA_MEMORY.txt" "memory\archives\ARIA_MEMORY_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
```

### 5.2 Créer / Modifier un Prompt ou un Skill
1. Créer le fichier dans le bon dossier (`prompts/` ou `skills/nom_du_skill/`)
2. Documenter son objectif dans `ARIA_MEMORY.txt` (section à ajouter)
3. Mettre à jour ce guide si la structure change

### 5.3 Mettre à jour la Personnalité Pro
```powershell
# Une fois le dossier créé
Set-Location core\personality\pro
notepad.exe base.md
notepad.exe pro_mode.md
notepad.exe rules.md
```

### 5.4 Ajouter un nouveau Skill (exemple : manage_memory)
1. Créer le dossier `skills\manage_memory\`
2. Créer `skills\manage_memory\README.md` qui explique ce que fait le skill
3. Créer le script PowerShell ou le code qui sera appelé par le tag `<tool>`
4. Référencer ce skill dans `ARIA_MEMORY.txt`

---

## 6. Commandes PowerShell Utiles pour le Projet

```powershell
# Lister tout le contenu du projet
Get-ChildItem -Recurse -Depth 2 | Where-Object { $_.Name -notlike ".*" }

# Rechercher du texte dans tous les fichiers .md et .txt
Get-ChildItem -Recurse -Include *.md,*.txt | Select-String "manage_memory"

# Sauvegarder tout le projet rapidement
Compress-Archive -Path . -DestinationPath "backups\ARIA_backup_$(Get-Date -Format 'yyyyMMdd_HHmm').zip" -Force

# Vérifier l'état du GPU (comme dans ta capture)
nvidia-smi
```

---

## 7. Règles de Travail Quotidien

1. **Toujours commencer par lire** `ARIA_MEMORY.txt` + ce guide
2. **Ne jamais** laisser l'IA prendre des décisions importantes seule
3. **Toujours** faire les commits et push **manuellement**
4. Garder le focus sur la **structure propre** et le skill crypto pendant les 2 prochaines semaines
5. Si tu atteins ~50k tokens dans une session → le noter dans la mémoire
6. Toute modification importante de structure doit être documentée dans ce fichier

---

## 8. Prochaines Actions Concrètes Recommandées

- [ ] Créer le dossier `core/personality/pro/` avec les 3 fichiers `.md`
- [ ] Implémenter le premier prototype du script PowerShell qui parse les tags `<tool>`
- [ ] Créer le skill `manage_memory` (priorité absolue)
- [ ] Préparer le contenu du skill Crypto pour le 2 juillet
- [ ] Mettre à jour régulièrement ce guide quand la structure évolue

---

## 9. Contact & Responsabilités

- **Décideur final** : GoldenFarFr
- **Arbitre technique** : Grok
- **Exécutant / Proposeur** : Aria (et toi qui lis ce guide)

---

**Fin du guide**

Ce fichier est **vivant**. Il doit être mis à jour à chaque évolution importante de la structure ou du workflow.

Tu peux maintenant donner ce fichier à n'importe quelle IA ou personne pour qu'elle comprenne immédiatement où on en est et comment travailler proprement sur ARIA.

---

*Créé avec ❤️ pour que le projet ARIA reste clair, structuré et scalable.*