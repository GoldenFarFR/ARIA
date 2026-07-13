[VPS Research]

# Refonte vitrine ARIA (vanguard/src) — références visuelles, widget IA flottant, transitions de page

Recherche/références seulement, aucun code produit ni modifié. Objectif :
donner à l'opérateur des exemples réels et sourcés pour trancher entre deux
directions visuelles avant toute maquette, plus des patterns UX pour le
widget IA flottant (décision déjà prise : vraie conversation) et une mise à
jour sur les librairies de transition de page.

---

## VOLET 1 — Deux directions visuelles

### (a) FUSION — sobriété luxe + futuriste "2200" en filigrane

**Références réelles :**

1. **Cartier Watches & Wonders 2026** (Awwwards, score dev 9.00/10, score
   global 7.53/10) — Three.js + GLSL + Blender pour un rendu 3D haute
   fidélité, mais la mise en scène reste "un voyage immersif à travers des
   univers raffinés" : storytelling au scroll, pacing délibéré, pas de
   surcharge d'information. C'est l'exemple le plus proche du "vaisseau
   amiral discret" demandé : technologie de pointe au service d'une
   présentation qui reste feutrée. [Cartier Watches & Wonders 2026 — Awwwards](https://www.awwwards.com/sites/cartier-watches-wonders-2026)

2. **Meridian** (Refokus INT, Awwwards Honorable Mention) — site de
   private investing avec globe 3D interactif, microinteractions GSAP,
   WebGL. Le globe se révèle au clic plutôt qu'en continu — futurisme
   contenu, pas envahissant. Pertinent car même problématique que ARIA :
   comment rendre un produit financier/technique "high-stakes" attrayant
   sans tomber dans le gadget. [Meridian — Awwwards](https://www.awwwards.com/sites/meridian)

3. **Porsche / NIO** (référence secteur automobile luxe-tech, non datée
   Awwwards mais constante du secteur) — photographie éditoriale, typo
   discrète à fort espacement, animations de transition mais **pas** de
   particules/néon. La caractéristique clé retenue : la retenue elle-même
   est le signal de luxe — every micro-interaction est un feedback discret
   (bouton qui se décale légèrement au survol), jamais un spectacle. [Automotive Website Design Examples](https://azurodigital.com/automotive-website-examples/)

**Ce qui caractérise concrètement cette direction (dénominateur commun) :**
- Palette : 1-2 couleurs de fond neutres (noir profond ou blanc cassé) +
  1 accent unique (jamais un dégradé multicolore).
- Typographie : polices variables à faible contraste, interlignage large,
  tailles de titre grandes mais peu nombreuses — la typo *est* le
  futurisme, pas un ornement en plus.
- Densité d'animation : faible fréquence, haute qualité — une poignée de
  transitions signature (scroll-reveal, hover ciblé) plutôt qu'un mouvement
  permanent à l'écran.
- Présence IA : révélée au clic/interaction volontaire de l'utilisateur,
  jamais imposée dès le chargement.

**Avertissement honnête :** cette direction est la plus *sûre* en
accessibilité — son style repose déjà sur la sobriété, donc
`prefers-reduced-motion` s'y intègre naturellement (désactiver le scroll
storytelling ne casse pas l'identité visuelle, juste le rythme). Risque
principal : mal exécutée, elle devient un site "corporate" générique sans
aucun signal futuriste — la marge entre "discret" et "fade" est fine et
tient entièrement à la qualité d'exécution des quelques animations
retenues, pas à leur nombre.

### (b) BASCULE ASSUMÉE — futuriste spectaculaire, "l'IA dépasse l'humain"

**Références réelles :**

1. **Aether 1** (OFF+BRAND, Awwwards SOTD + CSS Design Awards) — produit
   fictif d'earbuds IA, mais construit spécifiquement comme vitrine de ce
   que peut faire un concierge IA embarqué : WebGL 3D produit, "particle-
   based motion fields", shaders réactifs à l'audio, curseur fluide
   simulé, scroll infini cinématique. Palette "deep" (fond sombre dense)
   + dégradés doux, grand espace négatif malgré la densité d'effets. [Aether 1 — OFF+BRAND case study](https://www.itsoffbrand.com/our-work/aether1)

2. **Nisa — AI Chatbot Landing Page** (Awwwards Honorable Mention, avril
   2025, PeachWeb Builder) — palette exacte confirmée : `#e89f6b`
   (accent chaud, orange/cuivre) sur `#0d0605` (quasi-noir). 3D + WebGL +
   vidéos intégrées dans plusieurs sections (header, UI, interactions) —
   c'est une accumulation d'effets assumée, pas une retenue. [Nisa — Awwwards](https://www.awwwards.com/sites/nisa-ai-chatbot-landing-page)

3. **Neurable AI — Landing Page** (Red Shark Digital, Awwwards Honorable
   Mention) — contraste extrême assumé : palette confirmée `#000000`/
   `#ffffff` uniquement, sans zone intermédiaire. Direction différente de
   Nisa (pas de couleur chaude) mais même logique de bascule : le contraste
   *est* le message, pas un accent en plus.

**Ce qui caractérise concrètement cette direction :**
- Palette : soit noir dense + un accent saturé unique (Nisa), soit
  contraste pur noir/blanc sans nuance (Neurable) — dans les deux cas,
  zéro dégradé pastel, zéro couleur "safe".
- Typographie : titres surdimensionnés, souvent en majuscules ou avec
  un poids extrême, texte de support volontairement technique/dense pour
  contraster avec l'accroche.
- Densité d'animation : mouvement continu (shaders, particules, curseur
  réactif) — l'écran "vit" en permanence, pas seulement au scroll/hover.
- Présence IA : immédiate et centrale dès le chargement, pas une option
  qu'on découvre.

**Avertissement honnête (le plus important des deux) :** cette direction
présente un vrai risque de lisibilité/accessibilité si elle est copiée
telle quelle. Un shader audio-réactif en continu, un scroll "infini
cinématique", et un curseur simulé sont *exactement* le type d'effets que
`prefers-reduced-motion` doit couper — et sans un vrai chemin de repli
(fallback statique équivalent en contenu, pas juste "moins d'animation"),
la page devient fonctionnellement différente pour l'utilisateur qui a
activé ce réglage système, pas seulement plus calme. Les critiques 2026
sur le design "immersif 3D" pointent précisément ce défaut : conçu pour
être admiré, pas pour être lu par un lecteur d'écran, chargé sur un
téléphone milieu de gamme, ou parsé par un agent IA. Si cette direction
est choisie, le fallback `prefers-reduced-motion` doit être conçu *en même
temps* que la version spectaculaire, pas ajouté après coup. [Web Design Trends 2026 — accessibility gaps in 3D](https://www.select-interactive.com/news/web-design-trends-2026-that-survive-performance-and-ai-agents)

### Norme non négociable (rappel, valable pour les deux directions)

`prefers-reduced-motion: reduce` doit désactiver tout mouvement non
essentiel (shaders, parallax, scroll storytelling, curseur simulé) sans
retirer de contenu — c'est un standard W3C/WCAG 2.3.3, pas une option
esthétique. [MDN — prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion) · [WCAG 2.3.3 — Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html)

---

## VOLET 2 — Widget IA flottant conversationnel

**Positionnement — coin vs centre :** le pattern dominant reste le coin
(bas-droite), hérité de l'UX support client (Intercom/Zendesk) — visible
immédiatement, n'entrave jamais la lecture, présent en permanence sans
occuper d'espace de mise en page. Le centre-écran est utilisé
spécifiquement pour les sites où le chat IA **est** le produit ou le
héros de la page (Aether 1 : "chat with an on-page AI concierge" comme
guide narratif central), pas comme add-on. **Recommandation pour ARIA** :
vu que le widget doit porter une vraie conversation (pas décoratif) mais
n'est pas *le* produit de la landing page — un entre-deux crédible est un
point d'entrée compact en coin (visible dès le chargement, jamais bloquant)
qui **s'étend** en overlay centré une fois la conversation engagée, plutôt
que centré dès le départ. [Sendbird — Chatbot UI examples](https://sendbird.com/blog/chatbot-ui) · [Aether 1 case study](https://www.itsoffbrand.com/our-work/aether1)

**Mode d'ouverture :** toujours visible dès le chargement (pas de
révélation au scroll) est la norme confirmée — l'utilisateur ne doit pas
avoir à chercher le point d'entrée. Le compromis identifié pour éviter
qu'il nuise à la lecture : taille réduite au repos, expansion uniquement
sur interaction volontaire (clic), jamais d'auto-ouverture au scroll ou
après délai (pattern jugé intrusif dans la littérature UX consultée).

**Gestion de la latence LLM affichée à l'utilisateur :** le pattern
standard identifié est l'indicateur de frappe ("typing indicator") déjà
généralisé dans les blocs de chat IA modernes (ex. composants shadcn/ui
dédiés) — signal minimal mais suffisant pour couvrir une latence de
quelques secondes. **Point à vérifier plus tard, hors périmètre recherche
actuel** : rien de trouvé de spécifique sur la gestion d'une latence
*longue* (>5-10s, cas plausible si ARIA route vers un fallback LLM plus
lent, cf. finding #117/#135 déjà banqués) — un message d'état explicite
("réflexion approfondie en cours") au-delà du simple indicateur de frappe
serait à concevoir spécifiquement, pas un pattern trouvé tel quel ailleurs.
[React AI Chat Floating Widget — shadcn](https://www.shadcn.io/blocks/ai-chat-floating-widget)

**Comment éviter que le widget nuise à la lecture du contenu produit :**
consensus trouvé — taille de repos volontairement petite, position fixe
qui ne pousse jamais le contenu (overlay, pas de redimensionnement de la
mise en page), et sur les sites où le widget est central (Aether 1), le
chat *remplace* temporairement l'attention plutôt que de coexister avec
le contenu produit à l'écran — ce qui confirme l'approche "point d'entrée
compact → expansion en overlay" plutôt qu'un widget centré en permanence.

**Frontière rappelée et respectée dans cette recherche** : aucune
implémentation proposée ici — exposition publique d'un LLM conversationnel
reste un sujet sensible (doctrine "zéro trace IA", rate-limit, gating) hors
périmètre de ce volet, à traiter par Claude Code au moment de la
construction réelle du widget.

---

## VOLET 3 — Transitions de page (mise à jour maturité/poids/compat)

| Solution | Poids | Maturité 2026 | Compat React | Verdict |
|---|---|---|---|---|
| **View Transitions API** | ~0 (natif navigateur) | Large support navigateurs désormais confirmé | Native, pas de wrapper nécessaire | Apps 2-3x plus fluides sur appareils bas de gamme que les libs JS, accélération GPU native — **premier choix par défaut si le support navigateur cible le permet** |
| **Framer Motion (Motion)** | 34-46 KB gzip | Standard de facto pour animations React en 2026, intégration native avec Next.js/React Server Components | Native React | Meilleur pour layout animations/exit transitions dans une app riche en UI — pas le plus léger |
| **GSAP + Barba.js** | GSAP 23 KB gzip | GSAP : toujours la référence pour séquences complexes/scroll. **Barba.js : dernière activité dépôt principal début décembre 2024** — pas abandonné mais maintenance visiblement ralentie, les mainteneurs sollicitent du sponsoring GitHub | Barba.js pas conçu pour React (architecture DOM-first, s'intègre mal nativement avec le virtual DOM) | GSAP recommandé seul pour le scroll/séquences ; **Barba.js à ne plus recommander comme brique neuve pour une stack React** — c'est un signal de maturité déclinante, pas un verdict "cassé", mais le risque de dépendance qui se fige progressivement est réel |
| **Lenis** | Léger | Devenu "standard de facto" du smooth-scroll en 2026 | Compatible React/Next.js/GSAP/Framer Motion nativement, scroll natif du navigateur (pas de scroll-hijacking comme l'ancien Locomotive Scroll) | Recommandé sans réserve si un smooth-scroll est retenu, quelle que soit la direction visuelle choisie en Volet 1 |

**Verdict global Volet 3, inchangé sur le fond mais confirmé par recherche
fraîche** : View Transitions API en premier choix pour les transitions de
page simples (poids nul, natif) ; Framer Motion pour les animations de
composants/layout dans l'app React ; GSAP pour tout scroll storytelling
complexe (cohérent avec la direction (a) ou (b) du Volet 1) ; **retirer
Barba.js de la liste des candidats neufs** — sa maintenance ralentie et
son inadéquation structurelle à React en font un risque plus qu'un gain
face à la combinaison View Transitions API + GSAP + Lenis.

---

## Sources

- [Cartier Watches & Wonders 2026 — Awwwards](https://www.awwwards.com/sites/cartier-watches-wonders-2026)
- [Meridian — Awwwards](https://www.awwwards.com/sites/meridian)
- [Automotive Website Design Examples 2026](https://azurodigital.com/automotive-website-examples/)
- [Aether 1 — OFF+BRAND case study](https://www.itsoffbrand.com/our-work/aether1)
- [Aether 1 — Awwwards SOTD](https://www.awwwards.com/sites/aether-1)
- [Nisa — AI Chatbot Landing Page — Awwwards](https://www.awwwards.com/sites/nisa-ai-chatbot-landing-page)
- [Neurable AI — Landing Page — Awwwards](https://www.awwwards.com/sites/neurable-ai-landing-page)
- [Web Design Trends 2026 — accessibility gaps in immersive 3D](https://www.select-interactive.com/news/web-design-trends-2026-that-survive-performance-and-ai-agents)
- [MDN — prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion)
- [WCAG 2.3.3 — Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html)
- [Sendbird — Chatbot UI examples](https://sendbird.com/blog/chatbot-ui)
- [React AI Chat Floating Widget — shadcn](https://www.shadcn.io/blocks/ai-chat-floating-widget)
- [GSAP vs Framer Motion vs React Spring 2026](https://lab.good-fella.com/blog/gsap-vs-framer-motion-vs-react-spring)
- [Motion (Framer Motion) — GSAP vs Motion comparison](https://motion.dev/docs/gsap-vs-motion)
- [Barba.js GitHub — releases/activity](https://github.com/barbajs/barba/releases)
- [Lenis — Smooth Scroll](https://www.lenis.dev/)

Frontières confirmées respectées : aucun code produit, aucune maquette
construite, aucune modification de `vanguard/src`. Recherche/références
uniquement, décision de direction laissée à l'opérateur.
