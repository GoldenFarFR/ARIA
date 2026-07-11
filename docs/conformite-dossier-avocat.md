# Dossier de cadrage — conformité avant facturation

> **Statut : document préparatoire interne. NE constitue PAS un avis juridique.**
> Objectif : préparer un rendez-vous avec un avocat spécialisé **crypto / MiCA /
> services financiers** pour valider la légalité **AVANT tout encaissement** et
> avant tout versement de récompenses. Mis à jour le 07/07/2026.
> **Aucun paiement client encaissé, aucun versement de cagnotte** tant que les
> questions bloquantes (§6) ne sont pas tranchées par un professionnel.

---

## 1. Contexte factuel (à transmettre à l'avocat)

- **Entité** : « Aria Vanguard ZHC » — *forme juridique + pays d'immatriculation À
  COMPLÉTER.*
- **Produit** : ARIA, agent IA autonome produisant des **rapports d'analyse** de
  tokens on-chain. **Analyse uniquement** — aucune exécution de trade, **aucune
  gestion ni détention de fonds/actifs clients**. Toute action = **validation
  humaine** par le client.
- **Chaîne** : **Base uniquement** au lancement.
- **Modèle commercial (prix de lancement)** :
  - Rapport **standard 30 $** / rapport **premium 50 $** (à l'unité) ;
  - **Abonnement 100 $/mois** → rapports illimités + **accès à ARIA en direct (LLM)** ;
  - **Accès OUVERT à tous** (pas de modèle boutique fermé). Objectif : **≥ 20
    abonnés d'ici déc. 2026**. Marketing **international, anglais d'abord**.
- **Contenu d'un rapport** : reco (BUY / WATCH / AVOID), thèse, scénarios, niveaux
  (entrée / invalidation / cible), taille suggérée en % du capital + **audit d'un
  juge adverse**. Disclaimer présent (« proposition, validation humaine, pas un
  conseil en investissement »).
- **Paiement** : **Stripe (carte) + crypto (USDC on Base)**.
- **Wallets** : **wallet embarqué self-custodial** (Privy) — **NON-custodial** :
  on ne détient **jamais** les clés ni les fonds des clients ; **receive-only**
  (aucune signature qui déplace leurs fonds ; transactions initiées par le client).
- **Programme de récompenses (cagnotte USDC)** : **15 % du revenu NET** (hors
  remboursés) abonde une cagnotte USDC ; distribuée **au pro-rata d'un score
  d'activité** (parrainage + engagement, avec décroissance temporelle) ;
  **déclenchement à 100 $**, puis **30 % du total distribué chaque mois**.
  **Financée par des revenus réels** (jamais l'argent des recrues).
- **Token ARIA** : **envisagé plus tard (phase 3 uniquement)**, en **pure
  utilité**, jamais avant clearance — mentionné ici pour un avis préliminaire.
- **Marketing** : ARIA publie du **contenu autonome** — **avertissements de
  risque publics** (AVOID/EXTRÊME) + pédagogie ; **les appels BUY actionnables
  restent réservés au produit payant.**

---

## 2. Questions à poser à l'avocat

**Bloc « produit » (préexistant) :**
1. **Qualification** : fournir contre abonnement des rapports d'analyse crypto —
   avec reco d'achat, taille, niveaux — est-ce un **« conseil en investissement »
   réglementé** (AMF / MiFID II) ? Le caractère **générique vs personnalisé**
   change-t-il la qualification ?
2. **Valeur du disclaimer** : « pas un conseil » suffit-il, ou le **contenu réel**
   prime-t-il ?
3. **MiCA** : obligations déclenchées (statut CASP, information) ? La reco
   publique d'un token relève-t-elle de l'**abus de marché** ?
4. **MAR** : soumis aux règles de **présentation objective des recommandations**
   et **divulgation des conflits d'intérêts** ?
5. **Clients hors UE** (ex. US : *investment adviser* / SEC) ?
6. **Structuration contractuelle** : clauses CGV/CGU, limitation de
   responsabilité, responsabilité si un rapport est erroné ?
7. **Conflits d'intérêts** : si la holding investit son propre capital sur des
   tokens qu'elle analyse (front-running) ?

**Bloc « web3 / monétisation » (nouveau) :**
8. **Cagnotte USDC / récompenses** : (a) est-ce une **valeur mobilière** / offre
   au public ? (b) déclenche-t-elle un statut **money transmitter / MSB / EMI** ?
   (c) obligations **AML/KYC** pour les versements USDC — seuils ? (d) **fiscalité**
   (bénéficiaires + nature de la charge pour nous) ? (e) confirmer que
   « **financé par revenus réels** » (pas par l'entrée des recrues) écarte la
   qualification **pyramidale / Ponzi** ; le modèle pro-rata « coefficient » pose-t-il
   un souci ?
9. **Parrainage / affiliation payé** (cash ou perks) sur un service financier :
   règles d'**apporteur d'affaires / introducing broker** ? limites (nb de niveaux) ?
10. **Paiements crypto (USDC/Base)** : AML/KYC, statut de **prestataire de
    paiement**, **TVA/fiscalité** sur encaissements crypto ?
11. **Wallet embarqué NON-custodial** (Privy) : confirmer que **ne jamais détenir
    clés/fonds clients** (receive-only, tx initiées par le client) nous tient **hors
    statut dépositaire / money-transmitter**. Points de vigilance ?
12. **Token ARIA (futur, pure utilité)** : **vue préliminaire** — faisabilité
    (Howey / MiCA), structuration, juridiction. Rien lancé avant clearance écrite.
13. **RGPD** : email, adresse wallet, usage — la **Privacy Policy provisoire**
    (`docs/legal-temporaire.md`) est-elle conforme ? Base légale ? DPO requis ?
14. **Marketing autonome d'ARIA** : publier des **verdicts de RISQUE publics** +
    pédagogie, **sans appels BUY publics** — est-ce le bon périmètre pour rester
    hors « conseil/sollicitation » et du bon côté du **MAR** ? **Valider les
    templates** de posts (playbook).
15. **Droit conso** : **droit de rétractation UE (14 j)** sur les rapports (biens
    numériques livrés immédiatement) — comment le gérer (renonciation éclairée) ?
16. **Finaliser** les CGV/CGU/Privacy/Risk/Refund provisoires (`legal-temporaire.md`).

---

## 3. À compléter / décisions

**Déjà décidé (cette session) :** Base only · prix 30/50/100 · accès ouvert ·
non-custodial (Privy) · paiement Stripe + crypto · cagnotte 15 % → seuil 100 $ →
30 %/mois · token reporté phase 3.

**À compléter par l'opérateur :**
- [ ] Forme juridique + pays d'immatriculation de la holding.
- [ ] Localisation des premiers clients (détermine le droit applicable).
- [ ] Adresse du wallet-cagnotte + qui la « détient » (idéalement smart contract).
- [ ] La holding détiendra/tradera-t-elle les tokens qu'elle analyse ?

---

## 4. Position de prudence (interne, en attendant l'avocat)

- **Ne rien encaisser** tant que Q1 n'est pas tranchée.
- **Ne rien verser** de la cagnotte tant que Q8/Q10 ne sont pas tranchées (mais
  l'**accumulation** de la cagnotte est sans risque).
- Conserver les disclaimers, **sans s'y fier seuls**.
- **Documenter la méthodologie facts-only** (atout MAR + crédibilité).
- Rester **non-custodial** et **receive-only** (réduit fortement l'exposition).

---

## 5. Comment trouver le bon avocat

- **Profil** : cabinet/avocat spécialisé **crypto / web3 + MiCA + services
  financiers** (PAS un généraliste). Idéalement avec dossiers réels sur **tokens,
  programmes de récompenses, DeFi**.
- **Où chercher** : cabinets fintech/crypto (FR/UE), recommandations de founders
  crypto, barreaux/annuaires spécialisés MiCA. Vérifier des **références concrètes**.
- **À demander d'emblée** : expérience **MiCA + récompenses/token** ; juridictions
  couvertes ; **budget** (forfait « cadrage » vs horaire) ; **délai** de premier avis.
- **Budget** : provisionner (poste « conformité » = 25 % de la feuille de route).
- **À envoyer pour un premier avis** : **ce dossier** + `docs/legal-temporaire.md`.

---

## 6. Priorités / ce qui BLOQUE le lancement

1. 🔴 **BLOQUANT avant tout encaissement** : Q1 (qualification conseil) + Q10
   (paiements crypto AML) + Q16 (docs légales finalisées).
2. 🔴 **BLOQUANT avant tout VERSEMENT de cagnotte** : Q8 (securities/MSB/AML/fisc).
3. 🟠 **Token** : bloqué jusqu'en phase 3 (Q12, avis préliminaire seulement).
4. 🟢 **Peut avancer sans attendre** : accumulation de la cagnotte, marketing de
   **risque/pédagogie** (Q14 à valider vite), build technique.
