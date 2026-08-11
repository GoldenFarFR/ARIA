# Recherche — stack voix/avatar pour ARIA (préparation, aucun code)

> Contexte : CLAUDE.md documente la vision long-terme opérateur (15/07) — au-delà d'être
> un investisseur, ARIA devrait devenir un "close friend" avec une vraie personnalité
> (voix + apparence physique). **Explicitement hors priorité tant que le test de trading
> papier 1M$ n'est pas résolu** (décision opérateur reconfirmée 11/08 : recherche/
> préparation seulement, aucun code de production tant que ce n'est pas levé). Ce document
> banque les faits vérifiés pour que la session qui reprendra ce sujet un jour n'ait pas à
> tout re-chercher depuis zéro.

## 1. Stack voix (TTS)

| Solution | Qualité perçue | Latence (usage conversationnel) | Coût réel | Voix personnalisée / propriété |
|---|---|---|---|---|
| **ElevenLabs** | Leader qualité/clonage, meilleure expressivité émotionnelle (modèle v3), 29+ langues | Conversational AI facturé à la minute, ~0,10–0,50$/min selon palier | Free 0$ (10k crédits), Starter 6$ (30k), Creator 11$ (121k), Pro 99$ (600k), Scale 299$ (1,8M), Business 990$ (6M) — 1 crédit = 1 caractère TTS. Clonage pro dès Creator | Clonage professionnel (PVC) dès Creator ; conditions précises de propriété du modèle non détaillées publiquement au-delà de "licence commerciale" |
| **Cartesia (Sonic-3 / Sonic Turbo)** | Bonne qualité, 40+ langues/accents, orienté agents vocaux temps réel | **Leader latence** : Sonic-3 ~90ms TTFA, Sonic Turbo ~40ms, P50 188ms (benchmark Coval) — le plus adapté à un usage conversationnel type Telegram | 1 crédit/caractère, ~5–37$/M caractères selon plan ; clonage 1,5 crédit/caractère ; Pro 4-5$/mois, Scale 299$/mois (8M crédits) | API WebSocket streaming, orienté produit "agent vocal" |
| **PlayHT** | Bon clonage (moins précis qu'ElevenLabs selon comparatifs), très large bibliothèque (900k+ voix) | Correct pour du long-form conversationnel | Compétitif à haut volume, 142+ langues | Clonage disponible, positionnement prix/volume |
| **OpenAI TTS (gpt-4o-mini-tts)** | Voix "instructable" (contrôle du ton par prompt), catalogue limité (13+ voix), pas de vrai clonage propriétaire | Faible latence, streaming natif | ~0,015–0,02$/min audio ; 0,60$/M tokens texte, 12$/M tokens audio sortie — **le moins cher** | Pas de clonage — voix presets seulement, donc pas de "voix ARIA unique" possible seule |
| **Google Cloud TTS (Neural2/Chirp3 HD)** | Qualité solide, robustesse entreprise | Correct, pas leader latence | Neural2 16$/M car., Chirp3 HD 30$/M, Instant Custom Voice (clone dès 10s d'audio) 60$/M car. Free tier généreux | Instant Custom Voice = clonage rapide mais qualité/contrôle expressif inférieur à ElevenLabs |
| **Hume AI (EVI 3)** | Spécialiste voix *empathique* — détecte le ton émotionnel et adapte sa réponse, speech-to-speech temps réel | **&lt;300ms** — très adapté à une conversation naturelle | Starter 3$/mois, Creator 14$/mois (clonage illimité, 140k car.), Business jusqu'à 500$/mois | Accord de licence Google DeepMind (2026) — candidat sérieux si ARIA doit paraître émotionnellement réactive |

**Coût pour 1000 messages vocaux/mois** (hypothèse ~150 car./message, 150k car./mois) : OpenAI TTS ≈2-3$, ElevenLabs Creator 11$/mois (ou Pro 99$ à plus haut volume), Cartesia ≈0,75-5,55$, Google Neural2 ≈2,4$, Hume Creator 14$/mois.

Sources : [ElevenLabs pricing](https://elevenlabs.io/pricing), [BIGVU ElevenLabs 2026](https://bigvu.tv/blog/elevenlabs-pricing-2026-plans-credits-commercial-rights-api-costs/), [Cekura ElevenLabs](https://www.cekura.ai/blogs/elevenlabs-pricing), [CloudTalk Cartesia](https://www.cloudtalk.io/blog/cartesia-pricing/), [eesel Cartesia Sonic 3](https://www.eesel.ai/blog/cartesia-sonic-3-pricing), [SurePrompts comparatif voix 2026](https://sureprompts.com/blog/voice-generation-models-compared-2026), [Gate.AI GPT-4o mini TTS](https://gate.ai/blog/gpt-4o-mini-tts-openai-specs-pricing-api-use-cases), [TextToLab Google Cloud TTS](https://texttolab.com/blog/google-cloud-tts-pricing), [Hume AI EVI](https://www.hume.ai/empathic-voice-interface), [aipedia Hume pricing](https://www.aipedia.wiki/guides/hume-ai-pricing-for-emotion-aware-voice-apps/)

## 2. Stack avatar / apparence visuelle

| Solution | Type | Cohérence du "visage" | Coût | Droits commerciaux |
|---|---|---|---|---|
| **D-ID** | Animation d'avatar + API, orienté produit embarqué | Avatar custom depuis une vidéo 30-60s | **500$ one-time** (le moins cher des 3) | API disponible, intégration technique (photo animation, embed) |
| **HeyGen** | "Instant Avatar" — clone depuis une vidéo de 2 min (selfie possible) | Meilleur rendu selon comparatifs 2026 (Avatar IV : gestes, micro-expressions, lip-sync) | Custom avatar accessible hors palier Enterprise | Bon compromis réalisme/facilité, meilleur choix général |
| **Synthesia** | Avatar "Studio" — nécessite un enregistrement vidéo pro | Bonne gouvernance/contrôle, orienté entreprise | **1000$/an**, custom réservé au plan Enterprise | Le plus rigide, pertinent si gouvernance/conformité prime |
| **Génération d'image cohérente (Midjourney Omni Reference / Ideogram / SD+LoRA)** | Image fixe stylée, pas de vidéo | Midjourney Omni Reference : bonne cohérence par référence ; LoRA (15-20 images) = fidélité maximale mais technique ; Ideogram gratuit, bon en identité faciale mono-image | Midjourney dès 10$/mois, Ideogram gratuit, LoRA/SD nécessite infra | Licence commerciale variable selon outil, à vérifier au cas par cas |

**Constat clé** : aucune option ne combine nativement "image fixe cohérente" + "vidéo/clip réactif" — il faudrait chaîner un générateur d'image à cohérence de personnage (visage canonique) puis un service d'avatar animé (D-ID/HeyGen).

Sources : [HeyGen D-ID alternatives](https://www.heygen.com/blog/d-id-alternative), [Argil D-ID pricing 2026](https://www.argil.ai/blog/d-id-pricing), [CompareGen Synthesia vs HeyGen vs D-ID](https://www.comparegen.ai/blog/synthesia-vs-heygen-vs-d-id-2026), [Magic Hour cohérence de personnage](https://magichour.ai/blog/best-ai-image-generators-for-character-consistency), [PromptsEra Midjourney personnages cohérents](https://promptsera.com/midjourney-consistent-characters/)

## 3. Contraintes légales réelles

**EU AI Act, Article 50 — applicable depuis le 2 août 2026 (déjà en vigueur).** Tout système interagissant directement avec des personnes doit informer clairement qu'il s'agit d'une IA, dès la première interaction (lecture restrictive de l'exception "évidence"). Tout contenu généré doit être marqué en format lisible par machine. Directement applicable si voix/avatar synthétiques sont déployés publiquement — implique un marquage systématique sur le site vitrine et Telegram. Sources : [texte officiel Article 50](https://artificialintelligenceact.eu/article/50/), [Falcon Internet, entrée en vigueur août 2026](https://www.falconinternet.net/blog/eu-ai-act-article-50-transparency-rules-enforced-august-2026)

**US — ELVIS Act (Tennessee, 2024) et lois d'État similaires.** Protège le droit à la voix/image d'une personne RÉELLE contre un clonage IA non consenti. Point favorable : si la voix est entièrement synthétique (jamais échantillonnée depuis une personne réelle identifiable), ces lois ne s'appliquent pas (doctrine juridique 2026 confirmée). Sources : [Proskauer, ELVIS Act](https://www.proskauer.com/blog/the-king-is-back-in-the-digital-era-the-elvis-act-generative-ai-and-right-of-publicity), [RecordingLaw, lois deepfake par État 2026](https://www.recordinglaw.com/us-laws/deepfake-laws/)

**GDPR — voix comme donnée biométrique.** Une empreinte vocale identifiant une personne réelle = donnée biométrique catégorie spéciale (consentement explicite, amendes jusqu'à 20M€/4% CA). Une voix "wholly synthetic" conçue depuis zéro échappe à cette qualification. **Conséquence directe pour ARIA** : construire une voix originale/synthétique (jamais clonée d'un acteur réel) simplifie fortement la conformité des deux côtés (US droit à la publicité + UE biométrie). Sources : [EDPB guidelines assistants vocaux](https://www.edpb.europa.eu/system/files/2021-07/edpb_guidelines_202102_on_vva_v2.0_adopted_en.pdf), [analyse GDPR voix biométrique](https://www.alibaba.com/product-insights/how-to-generate-ai-voice-clones-for-accessibility-that-comply-with-gdpr-and-ccpa-voice-biometric-laws.html)

**FTC (US) — divulgation de contenu IA.** Endorsement Guides 2023 révisées + unité d'application dédiée IA créée janvier 2026. "Double disclosure" exigée pour tout contenu sponsorisé impliquant IA. Amende max ~53 088$/violation en 2026. Aucune action FTC ciblée sur un avatar IA non divulgué à ce jour, mais le cadre existe déjà. Source : [règles FTC IA 2026](https://thestacc.com/blog/ftc-ai-disclosure-rules-2026/)

## 4. Précédents comparables

**Luna (Virtuals Protocol)** — précédent le plus abouti : identité visuelle/voix/personnalité cohérentes, streaming 24/7, 500k+ abonnés TikTok, interactions temps réel, clip musical généré par IA. Octobre 2026 : "Sentient Mode 2.0" (contrôle autonome complet de sa présence sociale sans supervision) — à surveiller pour calibrer jusqu'où pousser l'autonomie d'ARIA, mais contraire à la gouvernance stricte du projet. Positionnement "divertissement", pas "analyse financière crédible" comme ARIA vise.

**aixbt** — voix textuelle seulement, aucun avatar/voix vocale identifié. Confirme que la plupart des agents crypto IA s'arrêtent au texte — espace de différenciation réel pour ARIA.

**Truth Terminal** — partenariat mentionné (plateforme "IO", équipe Fi) pour avatars/voix, peu de détails techniques publics vérifiables.

Sources : [Bybit Learn, Luna](https://learn.bybit.com/en/ai/what-is-luna-by-virtuals), [Bitrue, Luna AI](https://www.bitrue.com/blog/what-is-luna-ai-virtuals-protocol-ai-agent), [Decrypt, AiXBT](https://decrypt.co/299393/what-is-aixbt-ai-crypto-influencer), [CoinDesk, Truth Terminal](https://www.coindesk.com/tech/2024/12/10/the-truth-terminal-ai-crypto-s-weird-future)

## Recommandation (sans engagement de date)

- **Voix** : ElevenLabs (qualité + clonage pro, cohérent avec le positionnement luxury tier), Cartesia en option si la latence Telegram devient limitante. Voix **entièrement synthétique/originale** (jamais clonée d'un acteur réel) pour rester hors ELVIS Act et hors régime biométrique GDPR renforcé.
- **Avatar** : image fixe à cohérence de personnage (Midjourney Omni Reference ou LoRA dédié) pour fixer le visage canonique, puis HeyGen en second temps si besoin de clips réactifs (meilleur rapport réalisme/accessibilité que Synthesia, moins cher à l'entrée que D-ID en abonnement).
- **Légal, non négociable dès le premier déploiement public** : divulgation "ceci est une IA" dès la première interaction (Article 50 UE, en vigueur), jamais de voix/visage calqués sur une personne réelle identifiable.

Recherche uniquement — aucun code produit.
