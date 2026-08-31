# Vision — ARIA Trader Assistant (31/08/2026, pas construit)

**Statut : vision banquée, zéro ligne de code écrite.** Décision explicite de
l'opérateur : application PARALLÈLE à ARIA, jamais mélangée au chantier
on-chain en cours (Brique 6/C_AGE) — le déclencheur reste on-chain, le
social/narratif contextualise, rien ne devient une règle d'achat avant
validation par replay historique. Objectif reformulé par l'opérateur :
**pas** « un système qui prédit les x1000 », mais un système qui donne à
un trader humain une information plus rapide, plus complète et moins
émotionnelle que l'interface standard — un avantage comparable aux
meilleurs traders memecoin, jamais une exécution automatique.

## Architecture à deux systèmes

```
                 ARIA
                   │
        ┌──────────┴──────────┐
        │                     │
 ARIA CORE              TRADER ASSISTANT
        │                     │
 onchain/backfill        X / FOMO
 features/regime         Narratives
 replay/execution        Market + chart context
        │                     │
        └──────────┬──────────┘
                   ↓
             OPPORTUNITY
                ENGINE
                   ↓
                TELEGRAM
                   ↓
                  toi
```

Le Social Radar écoute ARIA Core (« un token vient de franchir le filtre
on-chain ») puis enrichit avec FOMO + X + narratif + contexte marché.
FOMO supporte Robinhood Chain depuis juillet 2026 (pas théorique pour ce
terrain de recherche) — apporte une couche que l'on-chain n'a pas : les
trade theses expliquent le POURQUOI d'un achat/vente, avec PnL et solde du
trader affichés. Robinhood Chain est explicitement orientée tokenisation/
RWA (Stock Tokens) — base narrative structurelle potentielle, jamais
suffisante seule pour un signal d'achat.

## Les 6 dimensions du radar (jamais une note unique)

Explicitement PAS un score global (« ARIA SCORE = 87 » — on ne sait plus
pourquoi). Toujours plusieurs notes indépendantes + le "why"/"why not" en
clair.

1. **Social / Narrative Potential** — force du narratif, cohérence avec les
   thèmes du moment, nouveauté, activité X, accélération des mentions,
   nombre d'auteurs, qualité des theses FOMO, nouveaux traders exposés.
   Distinction clé : narratif EXISTANT vs narratif EN TRAIN D'ACCÉLÉRER.
2. **Market Opportunity / Chart Intelligence** — jamais "prix bas = bonne
   note". Où le token se situe dans SA trajectoire complète : ATH →
   drawdown → structure de consolidation → higher lows ? → volume revient ?
   → nouveaux participants ? → liquidité ? → activité accélère ?
   `HIGH POTENTIAL ≠ LOW PRICE` mais `position basse + structure qui
   s'améliore + flow qui revient + attention qui revient`.
3. **On-chain Momentum** — activity acceleration, flow persistence, new
   traders, active wallets, swap frequency, trade-size distribution,
   liquidity, relative strength, recovery speed. Moteur de confirmation
   principal — pas besoin d'attendre que toutes les variables existent
   avant de commencer (certaines déjà disponibles via Brique 6).
4. **Market Regime / résilience relative** — BTC↓/ETH↓/Robinhood↓/token↑
   est un signal beaucoup plus intéressant que token↑ quand tout monte.
   Rejoint directement la découverte du 31/08 sur C_AGE/C_EVENT
   (activité rare dans la population générale) — un survivant relatif dans
   une correction générale peut être plus intéressant qu'un suiveur.
5. **Entry Location / Entry Quality** — pas "prix très bas" mais "quelle
   part du mouvement potentiel reste devant nous par rapport au risque
   immédiatement sous-jacent" : distance from local structure, drawdown
   from ATH, distance to previous resistance, recovery from low,
   volatilité courante, liquidité, capacité de sortie attendue. Un token
   déjà bien monté peut scorer haut si sa structure montre une nouvelle
   accumulation ; un token à -70% peut scorer bas si tout est mort.
6. **Narrative Fit** — carte de thèmes (RWA, AI, NVDA, tokenized stocks,
   Robinhood, memes...) → cluster narratif → attention du cluster →
   accélération du cluster → part d'attention captée par CE token
   spécifiquement. Rejoint l'idée déjà notée `cohort_flow_share`.
   Particulièrement pertinent sur Robinhood Chain (infrastructure conçue
   autour des marchés financiers tokenisés).

**Sortie catégorielle, jamais BUY/SELL** : `WATCH / ALERT / IGNORE` (ou
`IGNORE(0-3) / WATCH(3-5) / INTERESTING(5-7) / ALERT(7-8.5) / HIGH
PRIORITY(8.5+)`) — seuils explicitement PAS fixés par intuition maintenant,
à calibrer plus tard par replay historique. Pour le premier prototype,
éviter même le score global agrégé, laisser les notes séparées et l'humain
décider.

## Format Telegram cible

```
🚨 ARIA RADAR — <TOKEN>

Social / Narrative        X.X/10
On-chain Momentum         X.X/10
Entry Structure           X.X/10
Market Resilience         X.X/10
Narrative Fit             X.X/10
Attention Acceleration    X.X/10

Why now
• <raisons concrètes, factuelles>

Risk
• <liquidité/concentration/timing/coût de sortie>

Trajectory
ATH → drawdown → accumulation → position actuelle

Why not
❌ <raisons de ne PAS agir, explicites, jamais omises>

🔗 DexScreener  🔗 FOMO
```

## Les 8 outils de la suite (pas un seul score)

1. **RADAR** (« quelque chose commence ») — activité/participation/flow/
   liquidité en accélération, prix encore calme, phase DISCOVERY→
   ACCELERATION. Pas d'ordre d'achat.
2. **NARRATIVE ENGINE** (« pourquoi maintenant ? ») — X + FOMO + theses +
   narratifs dominants + cohortes + tokens satellites. Attention absolue
   vs attention relative au mcap.
3. **CHART INTELLIGENCE** (« où sommes-nous dans l'histoire ? ») — pas
   RSI/MACD, mais la trajectoire complète (création→pump→ATH→drawdown→
   consolidation→higher lows?→volume revient?→participants reviennent?→
   cassure?).
4. **ENTRY QUALITY** — qualité d'emplacement, pas un prix. Position in
   trajectory, risk below structure, room to prior ATH, flow improvement,
   participation, liquidity → entry context score.
5. **MARKET RELATIVE** — BTC/ETH/Robinhood vs TOKEN, résilience vs
   faiblesse spécifique. Dans une correction globale, les survivants
   peuvent être plus intéressants que les tokens qui montent avec tout.
6. **FOMO INTELLIGENCE** — chaîne causale observable : THESIS → CAPITAL
   ENGAGÉ → ONCHAIN ACTIVITY → ATTENTION (X) → PRICE. Mesurable plus tard :
   `thesis_lead_time`, `attention_lead_time`, `onchain_lead_time`,
   `price_response`.
7. **EXHAUSTION ENGINE** — détecter « ça ne part plus », pas seulement
   « ça part » (différenciateur rare chez les traders humains). Buy flow↓,
   new traders↓, swap acceleration↓, large sells↑, liquidity↓, price
   recovery↓, attention↓ → alerte "Edge remaining: LOWER".
8. **POSITION ASSISTANT** — copilote post-entrée, mise à jour périodique
   (MFE, flow, new users, liquidity, attention) → statut HOLD/EXHAUSTION.

## Les 5 questions que toute alerte doit répondre

```
1. QUOI ?
2. POURQUOI MAINTENANT ?
3. QU'EST-CE QUI A CHANGÉ ?
4. QU'EST-CE QUI POURRAIT INVALIDER LE TRADE ?
5. QUAND DEVRAIS-JE SORTIR ?
```

## Doctrine explicite (opérateur, 31/08)

- Application parallèle, jamais mélangée au chantier on-chain en cours.
- Le déclencheur reste on-chain ; le social explique et contextualise ; le
  graphique mesure la position dans la trajectoire ; le régime indique la
  force relative au marché — aucune de ces couches ne devient une règle
  d'achat tant que le replay ne l'a pas validée.
- Prochain objectif : pas 50 features de plus, mais transformer les briques
  déjà là (Brique 6 on-chain + FOMO/X à explorer) en outils lisibles qui
  améliorent systématiquement la décision — preuve de valeur sur historique
  AVANT tout pouvoir d'exécution.
- Rejoint `docs/HANDOFF_WALLET_COPY_SHADOW.md`/`docs/HANDOFF_SIGNAL_CASCADE.md`
  pour l'inspiration mécanique (smart money, cascade de signaux) mais reste
  un système séparé, jamais fusionné sans mandat explicite.

Rien de plus n'est tranché : ni le repo cible, ni le premier prototype, ni
le calendrier. À reprendre quand l'opérateur donne le prochain "go" concret.
