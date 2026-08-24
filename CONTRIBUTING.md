# Contributing

This repository publishes ARIA's build in the open, but it is not currently
accepting external code contributions.

## Why

ARIA is an autonomous trading agent. Its codebase controls real-capital-
adjacent mechanisms (wallet guardrails, agent-wallet pilots, key handling).
A single unreviewed change here is a supply-chain risk, not just a code-
quality question — the review bar for this kind of project is higher than a
typical open-source repo, and the project isn't currently staffed to run
that review process for third-party submissions.

## What this means in practice

- **Pull requests**: not reviewed or merged from outside contributors at
  this time. Feel free to fork and experiment, but please don't expect a
  PR opened here to be picked up.
- **Issues**: welcome for bug reports or questions about the public parts
  of the project (see the docs under `docs/`). Not a channel for feature
  requests expecting implementation.
- **Security reports**: if you find a real vulnerability, please open an
  issue describing it responsibly (no exploit payloads, no live secrets) --
  see `docs/HANDOFF_SECURITE.md` for the project's own security posture.

This policy may change in the future. Nothing here is a judgment on the
quality of any contribution -- it reflects the operational reality of a
single-operator, security-sensitive project.
