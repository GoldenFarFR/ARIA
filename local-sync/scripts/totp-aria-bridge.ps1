# OBSOLETE (2026-06-20) — TOTP Telegram desactive.
# SSOT : code dans le chat Grok/Cursor → -TotpCode ou $env:GOLDENFAR_VAULT_TOTP_CODE

function Request-TotpViaAria {
    param([string]$Purpose = "vault-sync")
    throw "[TOTP_IDE_ONLY] TOTP Telegram desactive. Donne les 6 chiffres GoldenFar Vault dans le chat Grok/Cursor, puis relance avec -TotpCode."
}