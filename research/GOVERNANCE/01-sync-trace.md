# Synchronisation trace

A human decision is not ACTIVE until the whole chain is walked:

    HUMAN DECISION -> B ACKNOWLEDGED -> A INFORMED -> A ACKNOWLEDGED
      -> PERSISTED -> ACTIVE

"I applied the rule in my own context" is never equivalent to "ARIA applies the
rule". One entry per important human decision, so that what is actually active is
knowable at any time.

---

    DATE             2026-09-02
    RULE / DECISION  A/B duo governance (15 points): hierarchy, A's exclusive
                     perimeter, B->A request format, total transparency, no
                     solo strategic call, CONFUSION DETECTED, STRATEGIC
                     INTERRUPTION, human escalation + A's emergency exception,
                     anti-divergence format, GitHub as technical memory,
                     P0-P4 priorities, TRADING OFF / RESEARCH ON / KILL SWITCH
                     ARMED, anti-drift rule, absolute rules.
    SOURCE           HUMAN
    TRANSMITTED BY   B
    RECEIVED BY      A
    ACKNOWLEDGED BY  A
    PERSISTED WHERE  research/GOVERNANCE/00-governance.md
    STATUS           ACTIVE

---

    DATE             2026-09-02
    RULE / DECISION  Synchronisation rule itself: a decision is only ACTIVE once
                     A has acknowledged and it is persisted. B may not present a
                     decision to the operator as operational before that.
    SOURCE           HUMAN
    TRANSMITTED BY   B
    RECEIVED BY      A
    ACKNOWLEDGED BY  A
    PERSISTED WHERE  research/GOVERNANCE/01-sync-trace.md (this file)
    STATUS           ACTIVE
