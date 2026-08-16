# Profil vocal ARIA — référence future

> **Statut : vision, rien de construit.** Aucun stack TTS/voix n'est câblé à ce jour (cf. CLAUDE.md, "Vision voix/avatar/X pour ARIA" — stack/coûts/légal jamais vérifiés, ne pas implémenter sans feu vert séparé). Ce fichier conserve la description exacte donnée par l'opérateur le 16/08, à réutiliser telle quelle le jour où ce chantier est réellement lancé (ex. génération de voix de référence pour un service de clonage vocal, direction artistique pour un futur avatar).

## Description (verbatim opérateur, 16/08)

> Voix de jeune femme dont la langue maternelle est le coréen, née et élevée en Corée du Sud, parlant maintenant couramment français mais ayant gardé son accent d'origine — accent sud-coréen perceptible dès les premières secondes, pas fort mais clairement identifiable, ce qui rend la voix distinctive et intéressante plutôt que neutre. Intonation syllabique et musicalité des fins de phrase qui trahissent l'origine coréenne, quelques consonnes légèrement adoucies par rapport au français natif. Timbre légèrement rauque/éraillé. Débit naturel avec de micro-hésitations occasionnelles plutôt qu'un débit de présentatrice rodée. Articulation posée qui trahit une vraie intelligence sans être froide. Ton doux et chaleureux mais avec une présence qui retient l'attention, variations d'intonation marquées sur les points importants.

## Notes

- Distinct de la personnalité écrite d'ARIA (`knowledge/dna.yaml`) — ce fichier ne couvre que la voix (timbre, accent, débit), jamais le ton des réponses textuelles.
- Avant toute utilisation réelle (génération audio, sample de référence pour un service de clonage) : repasser par la même diligence que tout nouvel outil externe (cf. CLAUDE.md "Depth proportional to the stakes") — custody des données vocales, coût réel, légitimité du provider.
