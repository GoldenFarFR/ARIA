# Checklist rapide — installation skill template

Cocher avant merge dans `template-grok-cursor`.

## Avant installation

- [ ] URL / source notée dans `references/SOURCE.md`
- [ ] Licence compatible
- [ ] `grok inspect` — inventaire doublons fait
- [ ] Pas de doublon avec bundled (implement, review, check-work, …)

## Sécurité

- [ ] SKILL.md lu en entier
- [ ] Tous les scripts lus
- [ ] Pas d'exfiltration de secrets
- [ ] Pas de commandes destructives sans garde-fou

## Qualité

- [ ] `name` + `description` valides
- [ ] Étapes numérotées et testables
- [ ] Un workflow par skill
- [ ] Texte adapté au template (français)

## Après installation

- [ ] `grok inspect` — skill visible (project)
- [ ] `/nom-skill` testé
- [ ] README template mis à jour
- [ ] Commit `feat(skills): ajouter <nom>`

## Verdict

- [ ] **Approuvé**
- [ ] **Approuvé avec modifications**
- [ ] **Rejeté** — raison : _______________