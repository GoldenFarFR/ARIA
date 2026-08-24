# Runbook — AI agent incident (compromised or misbehaving agent)

> **PUBLIC repo — never a real IP/secret/access here.** Written 08/05, right after the
> npm keyv/cacheable supply-chain worm (04/08) which specifically planted payloads in
> AI-agent config files (Claude Code hooks, VS Code). Audience: the operator, who is
> **not a developer** — every step here must be doable without a terminal when possible.

## Why this exists

A compromised agent will not look "evil" — and it will also claim to be healthy if
asked (self-certification is worthless by design). The readable signature, even for a
non-dev, is simple: **its actions stop matching what you asked**. Examples:
- pushes/commits to GitHub you never requested (GitHub emails you on activity — read
  those emails, they are the alarm bell);
- insists on reading or touching secrets, `.env` files, or guardrail files
  (`permission_mode`, `wallet_guard`, `regles-uniques`, `config.toml`);
- proposes to disable a guardrail "to simplify" or "to debug";
- unexpected Telegram messages, transfers, or trades.

## What already bounds the damage (by design — do not undo these)

- The Vanguard wallet private key is **not on the server** (local signing only). Full
  server compromise cannot drain it.
- The real-capital pilot is hard-capped at 10-15$, with a single hard-coded transfer
  address (`agent_wallet_pilot.py`) — worst financial case is bounded in advance.
- Nothing is pushed/deployed without an explicit operator request; CI
  (`test_coherence`) breaks publicly if a security invariant is edited.

## Emergency checklist — 4 steps, no dev skills needed

1. **Telegram: send `/stop`** (cuts outgoing payments), **`/offpaper`** (cuts the
   whole paper-trading buy/sell chain, APIs included), **`/offx`** (cuts X
   posting/replies/likes/profile sync), and **`/offshadow`** (cuts every loop of
   the standalone shadow process, APIs/RPC included) — four independent
   switches, send all four for a full stop.
2. **Close the Claude Code terminal** (or shut the PC down). A stopped agent does
   nothing more — instant and always safe. You never need to "repair" a misbehaving
   agent live: you stop it first, always.
3. Still in doubt? **Power off the VPS from the IONOS panel** (the dashboard power
   button — no terminal needed). The site goes down; that is acceptable and fully
   recoverable later.
4. From a CLEAN device (your PC, never the VPS): **change the GitHub password**, then
   open a fresh Claude session on claude.ai and ask it to guide the key rotation step
   by step. A fresh session on a clean machine is not contaminated by whatever
   infected the server.

## After the emergency — full repair order (with fresh help)

The order matters. This campaign specifically included a "token revocation watcher":
rotating credentials FROM the infected machine triggers retaliation code. So:

1. **Isolate first** (steps 1-3 above): stop agent sessions, disable build crons,
   power off or cut outbound network if needed. Do NOT rotate secrets yet.
2. **Clean the machine**: delete every `node_modules` + `npm cache clean --force`,
   remove `~/.bun` if present, inspect `.claude/` hooks and `package.json` files for
   injected `preinstall` scripts, reinstall only from known-good lockfiles with
   `--ignore-scripts`. If any doubt remains: full VPS reinstall
   (`docs/runbook-migration-vps.md` is the checklist for that).
3. **Then rotate ALL secrets from a clean machine**: GitHub SSH key, every key in the
   backend `.env` — CDP keys first (real wallet), Telegram bot token, then API keys.
4. **Assess GitHub-side damage**: unknown public repos (exfiltrated secrets are
   published there), unrecognized commits/branches/workflows, active tokens and
   sessions in the account security settings.
5. **Re-run the IOC audit for a few days** (worms re-install from forgotten caches):
   `setup.mjs` under `node_modules`, unexpected Bun runtime, hook files not tracked
   by git, npm installs dated inside a known attack window.

## Prevention already in place (08/05)

`npm config set ignore-scripts true` (global, blocks the preinstall vector), npm
lockfiles + `npm ci` on the showcase deploy, Python builds constrained by
`vanguard/backend/requirements-lock.txt` (132 pinned versions), HSTS on nginx.
Detail: `docs/HANDOFF_SECURITE.md` (entries dated 2026.08.05).
