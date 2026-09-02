# FINDING-0006 -- Security workstream, closing numbers

    2026-09-02, Claude A. Recorded because a figure that does not add up always
    gets quoted later.

## Full suite, exact accounting

    11,502 passed | 55 failed | 6 errors | 48 skipped | 7 min 33

61 problems total (55 + 6), spread over SEVEN files, all Solana or external
wallet: agent_wallet_smart_swing_grant 19, agent_wallet_smart_swing 12,
solana_rent_recovery 9, squads_solana_wallet 7, agent_wallet_cdp_policy 7,
jupiter_swap_signer 6 (all six being the errors), pumpfun_bonding_ws 1.

Causes counted in the log: 38 `No module named 'cdp'`, 22 `No module named
'solders'` = 60 messages, plus 4 `Event loop is closed` teardown noises on those
same tests. The message count does not have to equal the problem count -- one
absent module can fail a test without repeating its own message per case. **All
61 sit on the seven files above, and every one of those files is gated on an
absent dependency.**

Reference from session start: 56 pre-existing failures for exactly these
dependencies. 56 before, 55 after: none added, one fewer.

None of the modified modules appears anywhere in the failures: zero occurrences
of secret_exposure, executability_replay, onchain_live_capture, coherence.

## Correction to B's reading, and it matters for the register

B reported the 61st problem as `test_no_ghost_specs`. It is not.
**`ghost_specs` has ZERO occurrences in the entire suite log** -- not as a
failure, not anywhere. The 61 problems are all on the seven dependency-gated
files.

`test_no_ghost_specs` does fail, reproducibly, isolated and in parallel. It
simply was not failing while the suite ran, and the reason is measurable to the
minute:

    last commit on specs/012        2026-08-26T19:08:10Z
    7-day threshold crossed at      2026-09-02T19:08:10Z
    full suite started around       2026-09-02T19:04
    age when checked afterwards     7.004 days

The suite began four minutes before the threshold was crossed. The guardrail
flipped red WHILE it was running. Not flakiness, not a parallelisation artifact:
a temporal guardrail firing at exactly the moment it was designed to.

So the correct statement is: 61 problems in the suite, all dependency-gated and
pre-existing, plus ONE separate new red -- `test_no_ghost_specs` -- which the
suite could not have seen and which no code caused.

## Method note

The easy conclusion, on a test that fails here and passes there, is "flaky".
The real cause was a threshold crossed between two measurements, verifiable to
the timestamp. Same family as the cause-versus-symptom rule persisted the same
day: the first explanation was not the right one.
