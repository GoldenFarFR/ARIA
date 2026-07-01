# API holding — api.ariavanguardzhc.com

> Vitrine permanente : `ariavanguardzhc.com` (aria-vanguard)  
> API permanente : `api.ariavanguardzhc.com` (service Render **holding** — repo `aria-vanguard`, plus de service DEXPulse)

## Architecture

```
ariavanguardzhc.com          → site statique holding (ne pas supprimer)
api.ariavanguardzhc.com      → auth, billing, webhooks Stripe, Telegram, app
test-1-nwf2.onrender.com     → CNAME Render actuel (nom de service à renommer côté holding)
```

Le service Render **DEXPulse** a été supprimé volontairement. Voir `MIGRATION-VANGUARD.md` pour déplacer le code API dans `aria-vanguard`.

## DNS (une fois)

### Automatique (cle API IONOS)

1. `.\register-ionos-api-key.ps1` — ouvre [developer.hosting.ionos.fr/keys](https://developer.hosting.ionos.fr/keys), crée une clé, enregistre `prefix.secret` dans `.ionos-api-key`
2. Le script enchaîne sur `setup-ionos-dns-api.ps1` (CNAME `api` → `test-1-nwf2.onrender.com`)

Format clé IONOS : **Prefix** + **Secret** (header `X-API-Key: prefix.secret`), pas un Bearer token.

### Manuel (my.ionos.com)

| Type | Nom | Cible |
|------|-----|-------|
| CNAME | `api` | `test-1-nwf2.onrender.com` |

Puis Render → service dexpulse → Custom Domains → `api.ariavanguardzhc.com` **verified**.

## Scripts

```powershell
.\setup-holding-api.ps1        # URLs canoniques api. + sync Render + Vanguard
.\setup-ionos-dns-api.ps1      # CNAME api (cle .ionos-api-key ou procedure manuelle)
.\apply-holding-fallback.ps1   # temporaire si DNS api pas pret (onrender)
.\update-stripe-webhook-url.ps1
.\sync-render.ps1
.\sync-vanguard.ps1
.\check-aria-status.ps1
```

## Fallback (DNS api absent)

Tant que `api.ariavanguardzhc.com` ne resout pas :

```powershell
.\apply-holding-fallback.ps1
```

Remet Vanguard + webhooks Stripe/Telegram sur `test-1-nwf2.onrender.com`.  
Quand DNS OK : `.\setup-holding-api.ps1` puis `.\update-stripe-webhook-url.ps1`.

## URLs canoniques

| Usage | URL |
|-------|-----|
| Site public | https://ariavanguardzhc.com |
| API Vanguard (frontend) | https://api.ariavanguardzhc.com/api |
| Webhook Stripe | https://api.ariavanguardzhc.com/api/billing/webhook |
| Webhook Telegram | https://api.ariavanguardzhc.com/api/telegram/webhook |
| Health | https://api.ariavanguardzhc.com/api/health |