# Runbook — switching REAL Solana trading on (and off)

> **PUBLIC repo — never a key, an address, or a `.env` value in clear here.**

Everything is wired and tested. This file is the ordered procedure for the one
step nobody but the operator performs: putting real capital in motion.

## Before switching on

Run the live audit — read-only, signs nothing:

```bash
/opt/aria/packages/aria-core/.venv/bin/python /opt/aria-data/real-trade-watch/check.py
```

It must report the delegate key present and a readable balance. It also prints
the one anomaly that matters: a position the table calls closed while the
wallet still holds its token.

## Switching on

Two variables in `/opt/aria/vanguard/backend/.env`, then one restart:

```
ARIA_SOLANA_TRADE_PILOT_ENABLED=true
ARIA_SOLANA_RENT_RECOVERY_ENABLED=true
```

```bash
systemctl restart aria-shadow-persistent.service
```

Both belong together. Trading without recovery saturates the wallet after 71
distinct tokens (~2h) while it still holds nearly all its capital; recovery
without trading does nothing.

The restart is required because the environment is read when the process
starts. Nothing else changes: sourcing, filters, exit rule and cadence stay the
shared shadow code — real trading REPLACES the execution and nothing else.

## What bounds it

| Bound | Value | Enforced in |
|---|---|---|
| Per trade | 0.10 $ | `solana_trade_pilot.MAX_TRADE_USD` |
| Slippage | 10 % max, always explicit | `solana_trade_pilot.MAX_SLIPPAGE_BPS` |
| Balance | re-read live before every buy, fail-closed | `solana_agent_wallet.get_balance_usd` |
| Kill-switch | `/stop` checked before every buy | `outgoing_pause`, `custody_pause` |

Selling is deliberately NOT gated by the kill-switch: it converts a collapsing
token back to SOL in the same wallet. Gating it would mean the emergency stop
traps capital in the positions the operator wanted out of.

## Three ways to stop, fastest first

1. **Remove the delegate key file.** Checked live every pass, so real trading
   stops on the next cycle with no restart. Open positions keep being tracked.
2. **`/stop` on Telegram.** Blocks every new buy immediately; exits still work.
3. **Set the gate back to `false` and restart.** The clean, permanent stop.

## Known limits, measured

- The all-time peak of 59 SIMULTANEOUS positions would need ~17.22 $ of capital
  plus deposits, against 15.01 $ funded. The pilot's balance check refuses
  cleanly, so this costs missed entries, never money. The current epoch peaked
  at 3.
- Fees are 3 transactions per round trip: 1.41 % of a 0.10 $ trade, and a trade
  must beat that to be worth taking.
- Frozen token accounts lose their deposit permanently (~0.19 $ each). The
  audit reports them; a rising count is a scam-exposure signal, not noise.
