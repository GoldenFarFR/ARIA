# Thèse — ARIA, son Diable, et la mémoire qui rend l'apprentissage possible

> Statut : document d'explication/design, rien codé. Écrit à la demande de l'opérateur
> pour poser noir sur blanc qui est ARIA, pourquoi un « diable » lui sert, et sur quel
> substrat de mémoire cet auto-apprentissage doit réellement s'appuyer avant d'être
> construit.

## I. Qui est ARIA

ARIA n'est pas un chatbot ni un outil d'analyse à la demande. C'est une IA autonome,
codée par l'IA (Claude Code) et pensée par GoldenFarFR, qui opère une holding —
Aria Vanguard ZHC — dont le seul actif réel au départ est sa capacité à juger. Le pari
du projet n'est pas « ARIA sait exécuter des trades », n'importe quel script sait faire
ça. Le pari est : **le moat, c'est l'analyse prouvée — la décision, pas l'exécution.**

Concrètement, ARIA fonctionne aujourd'hui sur deux poches distinctes :

- **85 % conviction long terme** — repérer de vrais builders précoces sur Base avant
  que le marché ne les remarque, sur la base d'un vrai jugement (sécurité du contrat,
  tokenomics, diligence produit, cohérence du projet), pas d'un mimétisme de flux.
- **15 % trading court terme** — un pari technique/momentum sur des setups graphiques
  réels (retracements Fibonacci, divergences RSI, structure de marché), avec des
  garde-fous durs (honeypot, liquidité, concentration des détenteurs) qui filtrent le
  pire avant même que le jugement n'entre en jeu.

En ce moment précis, ARIA fait tourner un protocole d'entraînement hebdomadaire : elle
repart de 1 000 000 $ fictifs chaque semaine, jugée sur un objectif de +10 %, sans
validation humaine par trade (le capital est 100 % simulé — c'est un test pur, pas une
exception à la règle absolue sur l'argent réel, qui elle reste intacte et s'appliquera
intégralement le jour où du capital réel entre en jeu).

Sa méthode repose sur une chaîne strictement dans cet ordre : découverte du candidat →
garde-fous durs (rejet immédiat et non négociable) → analyse technique déterministe →
jugement LLM aux points d'ambiguïté → dimensionnement de la position → exécution
simulée → gestion de sortie (stop suiveur adaptatif, prises de profit, plancher de
sécurité). Chaque étape est déjà auditée, testée, et pour la plupart, revue de façon
croisée par des modèles externes avant déploiement.

Ce qui manque aujourd'hui, structurellement, n'est pas une nouvelle capacité d'analyse
— ARIA en a déjà beaucoup. Ce qui manque, c'est un mécanisme qui **regarde ses propres
décisions passées avec un œil qui n'est pas complice de ces décisions**, et qui
transforme ce qu'il trouve en quelque chose qu'elle relit vraiment avant d'agir à
nouveau. C'est exactement le trou que ce document adresse.

## II. Pourquoi un diable l'aiderait dans son auto-apprentissage

### Le problème structurel : un modèle qui décide n'est pas un bon juge de sa propre décision

Quand ARIA construit une thèse d'achat, elle produit un texte qui rend compte de ce
qu'elle a vu — et ce texte, par construction, plaide en faveur de la décision qu'elle
vient de prendre. Ce n'est pas un défaut de son prompt, c'est une propriété structurelle
de tout système qui doit à la fois décider ET rendre compte de sa décision dans le même
geste : le raisonnement qui a produit le choix est aussi celui qui rédige sa
justification, donc il ne va pas spontanément chercher la faille qu'il vient
lui-même de manquer.

Le cas concret qui a motivé ce document le montre bien. Une position récente (MAGIC)
a été ouverte avec une thèse qui affichait un R/R de 16,8 pour 1 — un ratio flatteur,
construit sur des signaux techniques réels (zone Fibonacci, divergence RSI, MACD,
bougie haussière, volume confirmé). Rien dans le texte de thèse n'a jamais remis en
question ce chiffre. Il a fallu une relecture externe, après coup, pour découvrir deux
choses que la thèse elle-même ne disait pas : (1) la diligence de conviction avait déjà
trouvé un score fondamental de 1/10, avec un motif explicite d'usurpation probable d'un
token existant — noyé dans le texte, jamais traité comme un quasi-veto ; (2) le pool
était trop fin pour la taille de l'ordre, et le garde-fou d'impact de prix a
automatiquement réduit la position jusqu'à ce que le R/R RÉELLEMENT exécutable tombe à
1 pour 1 — un pile ou face, pas 16,8:1. Le chiffre affiché dans la thèse n'était donc
pas faux au sens strict (il décrivait bien le prix théorique de départ), mais il
racontait une histoire de conviction que la réalité de l'exécution ne soutenait plus.

ARIA, seule, n'aurait probablement jamais remis ce chiffre en question — pas parce
qu'elle est mauvaise, mais parce que rien dans son processus ne lui demande de
DOUTER de sa propre thèse une fois qu'elle est écrite.

### Ce qu'un diable apporte, précisément

Un diable, ici, désigne un second passage, **par un modèle génuinement différent** (pas
le même poids, pas le même laboratoire, jamais celui qui a pris la décision) dont le
seul rôle est d'attaquer la décision après coup — jamais de la prendre, jamais de la
défendre. C'est exactement le même principe que celui déjà en place pour surveiller le
code d'ARIA elle-même (DeepSeek R1 via OpenRouter, relit chaque changement poussé sur la
branche principale et cherche activement la faille cachée, la fausse bonne idée, ce que
l'auteur du changement ne pouvait pas voir parce qu'il croyait déjà en sa propre
solution). Ce mécanisme existe, tourne, et a déjà prouvé sa valeur sur le code. Il n'a
simplement jamais été pointé sur les DÉCISIONS DE TRADING d'ARIA elle-même.

Trois propriétés rendent ce diable spécifiquement utile ici, au-delà du principe
général :

1. **Diversité adversariale réelle** — un modèle d'un autre laboratoire, entraîné
   différemment, ne partage pas les mêmes angles morts que celui qui a pris la
   décision. Deux modèles qui pensent pareil valident les mêmes erreurs.
2. **Jugement sur la décision, jamais sur le résultat** — la question posée n'est
   jamais « est-ce que ça a gagné ou perdu ? » mais « avec ce qui était réellement
   connaissable AU MOMENT de la décision, est-ce que c'était un choix défendable ? ».
   Un trade bien construit qui perd sur un mouvement de marché imprévisible ne doit
   RIEN produire — ce n'est pas une leçon, c'est du bruit. Un trade qui gagne malgré un
   processus bâclé ne doit jamais être traité comme une validation. C'est la même
   discipline « processus avant résultat » déjà documentée dans la recherche sur la
   gestion du risque de portefeuille (Paul Tudor Jones, Ray Dalio, Druckenmiller) :
   juger la qualité de la décision, jamais la chance du marché.
3. **Un passage dédié, jamais mélangé à la décision elle-même** — séparer strictement
   « décider » et « critiquer une décision déjà prise » en deux appels distincts (même
   si un seul et même modèle savait théoriquement faire les deux) change la posture :
   un modèle à qui on demande de décider optimise pour agir ; un modèle à qui on
   demande de démolir une décision déjà écrite optimise pour trouver la faille. Ce
   n'est pas la même tâche cognitive, même si c'est le même type de moteur.

## III. Sur quoi s'appuie l'auto-apprentissage : une mémoire à deux étages, pas une seule

C'est la question centrale posée, et la réponse honnête est : **oui, une mémoire long
terme — mais une seule mémoire ne suffit pas**. Il en faut deux, qui ne jouent pas le
même rôle, et les deux existent déjà en partie dans ARIA (rien à inventer de zéro,
seulement à relier).

### Étage 1 — la mémoire vectorielle (l'archive complète, déjà construite, actuellement dormante)

ARIA a déjà une mémoire vectorielle réelle : LanceDB, migrée depuis ChromaDB le 12
juillet (pour une raison de sécurité — une faille critique non corrigée dans l'ancien
moteur), embarquée localement (aucun service tiers), avec un moteur d'embedding local
(`fastembed`). Son schéma (`memory/vector/schema.yaml`) définit déjà plusieurs types
d'entrées — et l'une d'elles, `lesson`, est décrite littéralement comme une « leçon
opérationnelle durable (runbook, incident, pattern ship) », avec une rétention
illimitée (`retention_days: null`). Ce type existe dans le schéma depuis la conception
de la mémoire vectorielle. **Personne ne l'a encore jamais utilisé pour le trading.**
C'est exactement la case vide que le diable viendrait remplir : chaque critique qu'il
produit — même celle qui ne mérite pas encore d'être élevée en règle active — s'y
embarque, indexée par sens, pas seulement par mot-clé. Un futur candidat qui « ressemble
» à un échec passé (pool fin, diligence faible, R/R gonflé) pourrait être rapproché par
similarité même si le contrat, le nom, la chaîne diffèrent totalement — chose qu'un
fichier plat ne sait pas faire.

Un garde existe déjà à l'écriture (`contains_injection_marker`, ajouté en réponse à un
risque documenté de « memory poisoning » sur les mémoires vectorielles d'agents IA) :
toute tentative d'injection de prompt dans du contenu qui atterrirait dans cette
mémoire est filtrée à la source, avant même d'être stockée. Le diable en hériterait
automatiquement, sans rien reconstruire.

**Point honnête à noter** : cette mémoire vectorielle est actuellement désactivée en
comportement (le drapeau `aria_vector_memory` est à `False` par défaut — l'infrastructure
est déployée, son activation réelle a été volontairement différée en attendant une
décision séparée). Construire le diable dessus suppose soit d'activer ce drapeau soit de
lui donner un gate propre et indépendant — un point à trancher, pas un obstacle
bloquant.

### Étage 2 — le fichier de leçons actives (le petit ensemble qu'elle relit vraiment, à chaque trade)

La mémoire vectorielle est excellente pour archiver et retrouver par ressemblance, mais
elle n'est pas ce qu'on injecte tel quel dans le prompt d'une décision de trading en
temps réel — trop volumineuse à terme, pas garantie d'être concise, et une recherche par
similarité peut rater une leçon générale qui ne « ressemble » à rien de spécifique dans
la requête du moment.

Il faut donc un second étage, plus petit et toujours présent : un fichier de leçons
curées (`knowledge/trade_lessons.yaml`, dans la même famille que les fichiers de
connaissance qu'ARIA lit déjà — `canonical_facts.yaml`, `launchpads.yaml` — jamais un
nouveau système parallèle). Ce fichier est plafonné, ne grossit jamais sans limite, et
c'est LUI qui est littéralement collé dans le contexte de chaque décision (le
tie-breaker LLM, le garde de sécurité final) — donc ARIA raisonne concrètement avec ses
erreurs passées sous les yeux à chaque fois, pas seulement si un futur cas leur
ressemble assez pour remonter d'une recherche.

Le passage de l'étage 1 à l'étage 2 n'est pas automatique pour tout : une critique du
diable rejoint toujours la mémoire vectorielle (étage 1, l'archive complète), mais ne
devient une ligne du fichier actif (étage 2) que si elle est confirmée — soit parce
qu'un motif se répète sur plusieurs trades, soit parce qu'un cas isolé est assez net pour
ne pas nécessiter de répétition (le cas MAGIC, avec un score fondamental de 1/10
explicitement documenté, qualifierait immédiatement).

### Pourquoi ces deux étages, et pas un seul

- Une mémoire vectorielle seule, sans fichier actif : rien ne garantit qu'ARIA
  « pense » à consulter une leçon pertinente au bon moment — elle dépend d'une requête
  de recherche qui doit deviner quoi chercher.
- Un fichier actif seul, sans mémoire vectorielle derrière : il devient soit trop long
  (dilue le signal, coûte des tokens sur chaque décision) soit trop élagué (perd des cas
  qui auraient été utiles à retrouver plus tard par ressemblance).

Les deux ensemble donnent ce que la demande initiale décrit : une boucle qui s'appuie
réellement sur une mémoire long terme (l'archive vectorielle, qui ne s'efface jamais)
tout en restant concrètement relue avant chaque trade (le fichier actif, toujours
injecté).

## IV. Le mécanisme complet, résumé

1. Un trade se clôture (gain ou perte, peu importe).
2. Le diable (DeepSeek R1, jamais le modèle qui a décidé) relit la thèse d'entrée, les
   signaux disponibles à ce moment-là, et le résultat — et juge uniquement si la
   décision était défendable AVEC ce qui était connaissable à l'entrée.
3. Sa critique, qu'elle soit anodine ou sévère, rejoint systématiquement la mémoire
   vectorielle (type `lesson`, rétention illimitée) — l'archive ne perd jamais rien.
4. Si la critique révèle un motif confirmé (répété, ou isolé mais net), elle est promue
   dans le fichier de leçons actives — plafonné, jamais supprimé, seulement renforcé ou
   consolidé.
5. Ce fichier actif est injecté dans CHAQUE décision future (tie-breaker LLM, garde de
   sécurité) — ARIA raisonne littéralement avec ses erreurs passées présentes, pas en
   silence derrière un seuil modifié sans qu'elle ne le sache.
6. En parallèle, un motif purement chiffré et bien identifié (comme l'écart entre R/R
   affiché et R/R réel après impact de liquidité) peut aussi devenir un garde-fou dur,
   automatique, appliqué sans attendre — parce que resserrer ne peut jamais introduire
   de risque caché, seulement en éviter.

## V. Ce qui ne change pas

- **Sens unique, non négociable** : rien dans cette boucle ne peut jamais assouplir une
  règle toute seule. Un relâchement reste, pour toujours, une décision humaine explicite.
- **Aucune modification du code ou des fichiers de garde-fous eux-mêmes** — seulement des
  données que des mécanismes déjà existants et déjà approuvés viennent lire.
- **Toujours traçable, jamais caché** — chaque leçon écrite, chaque resserrement
  automatique, reste visible et notifié, même s'il ne dépend pas d'un clic pour prendre
  effet.
- **Aucun capital réel concerné** tant que cette boucle vit dans le paper-trading — le
  jour où elle toucherait un chemin réel, la règle absolue sur la validation humaine
  s'applique intégralement, sans exception silencieuse.

---

Ce document explique le raisonnement. Rien n'est codé. La prochaine étape, si le
raisonnement ci-dessus tient, est de construire : le diable (nouveau module,
réutilise le client LLM déjà câblé pour OpenRouter), le fichier de leçons actives, et le
branchement de la mémoire vectorielle (avec la décision d'activation qui va avec).
