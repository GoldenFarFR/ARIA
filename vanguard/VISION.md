# VISION — Aria Vanguard (see root VISION.md for the full ecosystem vision)

`vanguard/` is the production host: `backend/` (FastAPI, `app.main:app`) serves the API,
`src/` is the client showcase (`ariavanguardzhc.com`). Both deploy from this monorepo,
never a separate repo.

The full product/strategy vision — philosophy, architecture, positioning, durable product
decisions — lives at the repo root: [`VISION.md`](../VISION.md). This file exists only
because [`AGENTS.md`](./AGENTS.md) reads it first for anything touching `vanguard/`
specifically; it is not a duplicate SSOT.
