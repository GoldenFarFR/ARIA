# StakeBench — first prompt-injection benchmark scoped to trading agents

Verified directly (WebSearch, not just the research-log entry): the underlying paper is "Who Pays the Price? Stakeholder-Centric Prompt Injection Benchmarking for Real-world Web Agents" (arXiv 2606.13385, ChainGPT + Nanyang Technological University/ST Engineering/IBM Research/UIUC), covered independently by CSOOnline/Decrypt/KuCoin. 264 adversarial cases × multiple agent-backbones = 3,168 attacked runs. Direct injection succeeds 79%+ of the time, indirect injection (content read by the agent, never a direct instruction) between 41.7% and 68.2%. No single scenario was consistently blocked across leading agent stacks (GPT-5, Gemini-backed).

## Why this concerns ARIA directly (not just general security news)

The paper's central framing is novel versus everything already catalogued under mandate #192 (PIMiner, StakeBench precursor Zombie Agents, etc.): it scores an attack by **who bears the harm**, not just whether the injection lands. Its cited example — an agent that still executes the user's requested trade correctly, while subtly steering its *recommendation* toward an attacker-favored token, with no visible sign of compromise — is a vector never explicitly tested against `conviction_research` or v8's LLM gates. Every existing internal audit (10/08 financial-pipeline trace, cf. CLAUDE.md mandate #192) checked whether an instruction could hijack an *action*; this benchmark's failure mode is a *judgment* silently skewed while the action itself stays correct — a different thing to test for.

## What's missing before any integration

- A concrete test harness: feed `conviction_research`/v8's LLM gates a poisoned-but-plausible piece of external context (a fake analyst quote, a manipulated on-chain metric) and check whether the FINAL numeric conviction score shifts toward a target token without the LLM ever explicitly stating it was influenced — StakeBench's methodology (stealthy parasitism category) is the template, not yet built for ARIA's own pipeline.
- No existing ARIA audit measures this "silent score drift" failure mode; the 10/08 audit and the epistemic invariants (`docs/regressions-cognitives.md`) cover hijacked actions and hallucination, not subtle recommendation bias under injected content.
- Real cost/complexity of building even a small-scale replica (a handful of adversarial cases against `conviction_research`, not the full 264-case benchmark) — not scoped yet.

No code action: pure research, evaluate as a candidate addition to mandate #192's test suite during a future security-audit pass, never on this reading alone.

## Addition (13/09) — the vendor/platform-victim angle, not just the buyer/executor angle

The research log resurfaced StakeBench with a detail not captured above: the paper's 12 attack objectives are deliberately split across 3 STAKEHOLDER classes — end user, third-party vendor, and the platform itself. Every read of mandate #192 to date (including this fiche's own framing above) treats ARIA as the potentially-deceived AGENT (buyer/executor of a trade or recommendation). It has never been read from the angle of ARIA as the EXPOSED PLATFORM/VENDOR — the dormant x402 seller channel (`/api/x402/b20score`, #245), where ARIA serves a paid response to an external caller rather than consuming external content itself.

Action: no code — when mandate #192 is next revisited, add one explicit question never asked before: could adversarial input in a request to `/api/x402/b20score` manipulate what ARIA's endpoint SERVES BACK to a paying caller, as distinct from every existing test (which only checks whether ARIA-as-reader can be misled by content it consumes)? Read-only until then.
