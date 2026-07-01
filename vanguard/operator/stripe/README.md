# Stripe — Aria Market Pro (coffre opérateur)

> Aligné sur la doc officielle : [Webhooks](https://docs.stripe.com/webhooks) · [Signatures](https://docs.stripe.com/webhooks/signatures) · [Abonnements + webhooks](https://docs.stripe.com/billing/subscriptions/webhooks)

Repo **privé** uniquement. Ne jamais copier ces fichiers vers un repo public.

---

## Les 3 secrets (classés par rôle Stripe)

| Variable `production.env` | Préfixe | Où le trouver (Dashboard) | Rôle |
|---------------------------|---------|---------------------------|------|
| `STRIPE_SECRET_KEY` | `sk_test_` ou `sk_live_` | [Developers → API keys](https://dashboard.stripe.com/test/apikeys) | Appels API (Checkout, customers) |
| `STRIPE_PRICE_ID` | `price_` | [Products](https://dashboard.stripe.com/test/products) → produit récurrent Aria Market Pro | Prix abonnement mensuel |
| `STRIPE_WEBHOOK_SECRET` | `whsec_` | [Developers → Webhooks](https://dashboard.stripe.com/test/webhooks) → ton endpoint → **Signing secret** | Vérifier que l’événement vient bien de Stripe |

**Règle d’or** : les 3 doivent être du **même mode** (tout en **test** ou tout en **live**).  
Un `sk_test_` + un `whsec_` créé en mode live = ça ne marchera pas.

---

## Où trouver `STRIPE_WEBHOOK_SECRET` (`whsec_…`)

Ce n’est **pas** une clé API. Stripe le génère **par endpoint webhook**.

### Mode test (actuel)

1. [https://dashboard.stripe.com/test/webhooks](https://dashboard.stripe.com/test/webhooks) — vérifie **Mode test** (interrupteur en haut).
2. **Add endpoint** / **Ajouter un endpoint**.
3. **Endpoint URL** (HTTPS obligatoire — [doc](https://docs.stripe.com/webhooks#register-your-endpoint)) :
   ```
   https://api.ariavanguardzhc.com/api/billing/webhook
   ```
   (Fallback technique : `https://test-1-nwf2.onrender.com/api/billing/webhook`)
4. **Events to send** — sélectionne **ces 4** (ce que le code DEXPulse traite) :
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
5. Crée l’endpoint → onglet de l’endpoint → **Signing secret** → **Reveal** → copie `whsec_…`
6. Colle dans `production.env` :
   ```
   STRIPE_WEBHOOK_SECRET=whsec_...
   ```
7. Depuis la racine du coffre :
   ```powershell
   .\sync-render.ps1
   ```

Si tu **supprimes ou recrées** l’endpoint : nouveau `whsec_` → mets à jour `production.env` + `sync-render.ps1`.

### Mode live (plus tard)

Même procédure sur [https://dashboard.stripe.com/webhooks](https://dashboard.stripe.com/webhooks) (sans « test ») + `sk_live_` + prix live.

---

## Flux DEXPulse (doc Stripe ↔ notre code)

```
Aria Vanguard (#pricing)
    → POST /api/billing/checkout  (STRIPE_SECRET_KEY + STRIPE_PRICE_ID)
    → Stripe Checkout (mode subscription)
    → paiement OK
    → Stripe envoie webhook POST /api/billing/webhook
    → vérif signature (STRIPE_WEBHOOK_SECRET + header Stripe-Signature)
    → abonnement actif en base (Privy DID)
```

Références code (repo `dexpulse`, public/privé) :
- Route webhook : `backend/app/api/routes/billing.py` → `POST /billing/webhook`
- Vérification : `stripe.Webhook.construct_event(payload, signature, secret)` — [doc signatures](https://docs.stripe.com/webhooks/signatures)
- Événements gérés : `backend/app/billing/subscriptions.py` → `handle_stripe_event`

---

## Test local (optionnel — Stripe CLI)

[Doc : tester un handler](https://docs.stripe.com/webhooks#test-your-handler)

```powershell
stripe listen --events checkout.session.completed,customer.subscription.created,customer.subscription.updated,customer.subscription.deleted --forward-to localhost:8000/api/billing/webhook
```

La CLI affiche un `whsec_` **temporaire** (différent du Dashboard). Utilise-le seulement en local dans `backend/.env`, pas en prod.

---

## Fichiers dans ce dossier

| Fichier | Contenu |
|---------|---------|
| `recovery-codes.txt` | Codes récupération 2FA compte Stripe |
| `README.md` | Ce guide |
| `../production.env` | Les 3 variables `STRIPE_*` (+ reste des secrets Render) |

---

## Checklist opérateur

- [ ] Mode test **ou** live cohérent sur les 3 clés
- [ ] Endpoint HTTPS enregistré avec les 4 événements
- [ ] `STRIPE_WEBHOOK_SECRET` dans `production.env`
- [ ] `.\sync-render.ps1` exécuté
- [ ] `.\check-aria-status.ps1` → `stripe_webhook_configured: true`
- [ ] Test checkout → statut Pro **actif** après paiement

## Pièges

| Symptôme | Cause | Fix |
|----------|-------|-----|
| Checkout OK, Pro pas activé | Pas de `whsec_` ou mauvais endpoint | Créer endpoint + sync |
| Webhook 400 `invalid_signature` | `whsec_` ne correspond pas à l’endpoint | Reveal le bon secret Dashboard |
| `stripe_configured: true` mais rien après paiement | Webhook secret absent (health : `stripe_webhook_configured: false`) | Ajouter `STRIPE_WEBHOOK_SECRET` |
| Test + live mélangés | Clés de modes différents | Tout refaire dans le même mode |