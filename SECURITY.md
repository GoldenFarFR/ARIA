# Security Policy

This repository is the public codebase for ARIA (Aria Vanguard ZHC). No
secrets, private infrastructure details, or access credentials are ever
committed here — see `CLAUDE.md` for the full public/private split
(private infrastructure lives in a separate, non-public repo).

## Reporting a vulnerability

If you find a security issue in this codebase (a real vulnerability in the
code, not a false positive from an automated scanner), please use GitHub's
[private vulnerability reporting](https://github.com/GoldenFarFR/ARIA/security/advisories/new)
for this repository. This lets you share details privately before any
public disclosure.

If that channel is unavailable, you can reach the project at
`contact@ariavanguardzhc.com`.

Please do not open a public issue for a suspected vulnerability — use one
of the channels above instead.

## Scope

This policy covers the code in this repository. It does not cover:
- Third-party dependencies (report those directly to their maintainers,
  or open a Dependabot-related issue here if it's about how this repo
  consumes them)
- Social engineering, phishing, or issues unrelated to this codebase
