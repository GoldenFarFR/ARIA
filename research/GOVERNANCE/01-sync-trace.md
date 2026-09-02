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

---

    DATE             2026-09-02
    RULE / DECISION  One workstream at a time. "ne fais jamais deux chantiers en
                     meme temps, si tu en as plusieurs tu stoppes ceux les moins
                     urgents." REQ-0008 STOPPED (not paused) as a result; the
                     single active workstream is security + CI.
    SOURCE           HUMAN
    TRANSMITTED BY   B
    RECEIVED BY      A
    ACKNOWLEDGED BY  A
    PERSISTED WHERE  research/GOVERNANCE/01-sync-trace.md (this entry)
    STATUS           ACTIVE

---

    DATE             2026-09-02
    RULE / DECISION  Security alerts must be RESOLVED, never blacklisted. "je
                     veux que les problemes sur GitHub soient bien resolus, pas
                     juste blacklistes." Forbidden: dismissing Dependabot or
                     code-scanning alerts, adding scanner ignore entries or path
                     filters, CodeQL suppression comments, continue-on-error,
                     disabling a workflow, narrowing the scanned perimeter, or
                     closing a system_issue without fixing the defect. An alert
                     must disappear because the defect is gone, never because we
                     stopped looking. SOLE EXCEPTION: a genuine false positive,
                     DEMONSTRATED (the line, what the tool believes it sees, why
                     that is not what happens) and never merely asserted.
    SOURCE           HUMAN
    TRANSMITTED BY   B
    RECEIVED BY      A
    ACKNOWLEDGED BY  A
    PERSISTED WHERE  research/GOVERNANCE/01-sync-trace.md (this entry)
    STATUS           ACTIVE
    A'S NOTE         A had already placed a CodeQL suppression on
                     scripts/secret-exposure-audit.py:194 BEFORE this rule
                     arrived, together with its demonstration and a test locking
                     the invariant. Reported to B for arbitration rather than
                     kept silently -- see research/incidents/ and the report.

---

    DATE             2026-09-02
    RULE / DECISION  Stop after every report to the operator, until he answers.
                     "tant que je n'ai pas repondu a un de tes retours tu ne
                     passes jamais a la suite." Not slowed, not queued: BLOCKED.
                     B opens no new branch and sends A no new request; A starts
                     nothing new. Finishing the CURRENT workstream's outstanding
                     items is allowed -- those are finishing touches, not a
                     continuation.
                     Rationale, in B's words: reports were chained all day
                     without waiting for answers, so the operator received a
                     finding, then a correction of it, then a correction of the
                     correction, while work continued behind. When he answers, he
                     answers a state that no longer exists.
                     Third rule of the evening in the same family as "one
                     workstream at a time" and the synchronisation chain: no
                     unmanaged parallelism, no progress without acknowledgement.
    SOURCE           HUMAN
    TRANSMITTED BY   B
    RECEIVED BY      A
    ACKNOWLEDGED BY  A
    PERSISTED WHERE  research/GOVERNANCE/01-sync-trace.md (this entry)
    STATUS           ACTIVE
