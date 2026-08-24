# HANDOFF — Chainstack (Solana RPC provider, planned Robinhood Chain provider)

> Public repo — never a real IP/secret/token/key/personal email in clear text here. Variable names OK, their values never.

> Format: `[STATUS] Subject` / `Date: YYYY.MM.DD / Problem: ...` / `Solution: ... — file.py (hash)`.
> `[STATUS]` in DEPLOYED / CODE (tested, not deployed) / CONFIG (no commit) / ETAT ACTUEL (current snapshot, not a fix).

[ETAT ACTUEL] Subject: Chainstack platform reference (Solana + Robinhood Chain RPC provider)
Date: 2026.08.24 / Problem: ARIA already depends on Chainstack for Solana curve-tracker HTTP polling (`ARIA_SOLANA_RPC_HTTP_POLLING`) and plans to use it for Robinhood Chain, but no consolidated internal reference existed for its pricing/billing model, rate limits, or add-on products — every prior mention was scattered across HANDOFF_RESOURCE_BUDGET.md incident entries.
Solution: full reference below, sourced from `docs.chainstack.com` and `chainstack.com/pricing/` on 2026.08.24. Anything the source docs did not state is marked "not documented" rather than guessed.

---

### 1. Pricing / plan structure

Source: `chainstack.com/pricing/`, `/docs/rps-plan-limits`, `/docs/request-units`, `/docs/dedicated-node`.

| Plan | Monthly | Annual (per mo) | Included RU/mo | Overage rate | RPS limit (general) | Projects |
|---|---|---|---|---|---|---|
| Developer | $0 | $0 | 3,000,000 | $20 / 1M RU | 25 | 1 |
| Growth | $49 | $40 | 20,000,000 | $15 / 1M RU | 250 | 10 |
| Pro | $199 | $166 | 80,000,000 | $12.5 / 1M RU | 400 | 15 |
| Business | $499 | $416 | 200,000,000 | $10 / 1M RU | 600 | 20 |
| Enterprise | $990+ | $825+ | 400,000,000+ | $5 / 1M RU | Unlimited | Unlimited |
| Pay As You Go | Custom | Custom | Custom | $2.5+ / 1M RU | Unlimited | Unlimited |

Annual billing saves "up to 16%". ARIA is currently on the **Developer plan** (verify against the console before citing this as still current — this file records the plan structure, not ARIA's live account state).

**"Extra usage" toggle** (`/docs/quotas`, `/docs/manage-your-billing#manage-the-extra-usage-setting`):
- Disabled = hard cap. Verbatim: "On reaching the quota, your Chainstack services will stop and you won't be charged on going over the limit."
- Enabled = soft cap, billed at the plan's overage rate.
- Toggle lives at `console.chainstack.com/user/settings/billing`.
- Email alerts fire at 80% and 100% of quota regardless of the toggle.

**Billing thresholds** (`/docs/billing-thresholds`) — separate, non-configurable auto-charge mechanism, applies only to metered extra-usage charges (API overage, Dedicated Node hours, Warp transactions), never the base subscription fee. Progressive auto-charge triggers as accumulated extra-usage cost crosses each level, resets each billing cycle:
- Developer: $10 / $25 / $50 / $100 / $400 / $600 / $1,000
- Growth: $50 / $100 / $400 / $600 / $1,000
- Business/Enterprise: $100 / $400 / $600 / $1,000
Not user-customizable except by upgrading plan; Enterprise can request custom thresholds via support.

**Add-on pricing** (`chainstack.com/pricing/`):
- Unlimited Node: $149/mo (25 RPS) up to $3,199/mo (500 RPS). Full tier list is 25/100/250/500/1000 RPS (`/docs/unlimited-node`), but only the 25 RPS and 500 RPS price points were confirmed — see Gaps section.
- Yellowstone gRPC Geyser: $49/mo (2 streams) / $149/mo (7 streams) / $449/mo (25 streams).
- Warp transactions: $0.15 per transaction actually sent via Warp (not per RPC request).
- Dedicated Nodes: compute "from $0.50/hour per node"; storage "$0.01 per 20 GB/hour".
- Support add-ons: Professional $100/mo or $1,000/yr (<6h response, 24x7); Premium $1,000/mo or $10,000/yr (<1h response, 24x7).

**Feature gating by plan** (`/docs/features-availability-across-subscription-plans`) — only 3 facts confirmed: Trader Node/Warp transactions and Debug/Trace APIs are paid-plans-only (not on the free Developer plan); private networking and Dedicated Node customization are Enterprise-only. A full per-tier feature matrix beyond these 3 facts is **not documented**.

---

### 2. How request units (RU) are billed per method/call

Source: `/docs/request-units`, `/docs/solana-methods`, `/docs/http-batch-request-vs-multicall-contract`.

- Base rule, uniform across all methods and all chains including Solana: **1 RU per full-node call, 2 RU per archive-node call**. Verbatim: "Each JSON-RPC call counts as one request, regardless of how much data the response contains."
- `getMultipleAccounts` is flat-rate — no cost override or special note in the Solana methods table (unlike `getProgramAccounts`, restricted to paid plans and requiring `dataSize`/`memcmp` filters, or `getTokenAccountsByOwner`, paid-plans-only at 80 RPS). Calling it for 1 account or 100 accounts costs the same 1 RU — confirms ARIA's `pumpfun_curve_tracker.py` design assumption (batched `getMultipleAccounts` polling).
- Full vs archive threshold per chain:
  - Most EVM chains: less than 127 blocks behind tip = full; 127+ blocks behind = archive.
  - Solana: archive applies when the target slot is below `firstAvailable + 5,000`.
  - EVM `debug_*`, `trace_*`, `arbtrace_*`, and `eth_callMany` are always billed as archive (2 RU) regardless of block age.
- **HTTP batching does NOT reduce RU cost** — directly relevant to any future ARIA polling redesign. Verbatim (`/docs/http-batch-request-vs-multicall-contract`): "1 batch request of 100 calls consumes 100 requests instead of 1" — the server unpacks the batch and bills each call individually. Only a genuine on-chain multicall *contract* call counts as one request, a pattern that doesn't apply to Solana's `getMultipleAccounts` (already the native multicall-equivalent at flat 1 RU). Batching still cuts wall-clock latency (~38% vs parallel singles in Chainstack's own 30-address benchmark) even though it doesn't cut RU cost.
- WebSocket subscriptions: 1 RU to open the subscription, then **1 additional RU per push notification received**. Chain-agnostic — would apply identically to Solana `programSubscribe`/`accountSubscribe` if ARIA ever re-adopted a WS stream on Chainstack (cf. the pump.fun program-wide `logsSubscribe` incident on Helius, `docs/HANDOFF_RESOURCE_BUDGET.md`, 2026.08.21 entry — same cost shape, different provider).
- Dedicated Nodes and the Unlimited Node add-on both escape RU metering entirely (see section 4).
- `getMultipleAccounts` max accounts per call / max response size: **not documented by Chainstack** — this is a Solana-protocol-level constraint (standard cap is 100 pubkeys/call), not something Chainstack's docs state or override.

---

### 3. Rate limits (RPS) per plan

Two separate, independently-enforced constraints (`/docs/rps-plan-limits`, `/docs/limits`).

**A. General/default RPS ceiling per plan:**
- Developer: 25 RPS / Growth: 250 RPS / Pro: 400 RPS / Business: 600 RPS / Enterprise: unlimited.

**B. Solana-specific ceilings are markedly lower than the general table — ARIA-relevant divergence** (`/docs/limits`, "Throughput guidelines"):
- Solana Mainnet: Developer plan **5 RPS**, Growth plan **50 RPS** — one fifth of the general per-plan RPS figure.
- Solana Devnet: Developer plan 25 RPS, Growth plan 250 RPS — matches the general table.
- Per-method Solana overrides on top of the mainnet ceiling: `getBlockTime` 500 RPS, `getBlock` 400 RPS, `getTokenSupply` 300 RPS, `getTokenAccountsByOwner` 80 RPS (20 RPS in some regions), `getSupply` 2 RPS.
- **`getMultipleAccounts` has no method-specific override, so it falls under the general Solana Mainnet ceiling — 5 RPS on Developer, not the general-plan 25 RPS.** ARIA's current Developer-plan Solana HTTP polling (`pumpfun_curve_tracker.py`) is effectively rate-capped at 5 requests/second, not 25 — verify this against the actual configured throttle constant before assuming headroom.
- Architectural range caps: `getBlocks`/`getBlocksWithLimit` limited to a 500,000-block range.
- Other chain overrides seen in the same table (not Solana, banked for completeness): Arbitrum Mainnet `debug_traceBlockByNumber` 20 RPS all plans; Cronos Mainnet archive `debug_traceBlockByNumber` 10 RPS all plans; Fantom `debug_traceBlockByNumber`/`debug_traceBlockByHash` 5 RPS; EVM `eth_newFilter` capped at 10,000 blocks/request on Developer; `eth_getLogs` block-range cap 100 blocks on Developer vs 10,000 on Growth+.

**C. RPS vs monthly RU quota — independent gates.** RPS is a real-time per-second throttle (rejects/queues requests exceeding it regardless of remaining monthly quota); the RU quota is a monthly budget (governs hard-stop vs overage billing per section 1). Exceeding RPS produces an immediate rate-limit error even with quota remaining; exhausting monthly RU produces a hard stop or overage bill even while under the RPS ceiling. Chainstack's docs document the two mechanisms separately and never state this relationship as one sentence — this is inferred from the two being functionally independent, standard practice for this billing pattern, not a verbatim Chainstack claim.

---

### 4. Solana-specific node offerings

**Global (Elastic) Node vs Dedicated Node** (`/docs/global-elastic-node`, `/docs/dedicated-node`):

| | Global/Elastic Node | Dedicated Node |
|---|---|---|
| Billing | Per-request, metered RUs | Compute + storage, monthly, unlimited/not metered requests |
| Resources | Load-balanced, region-optimized, shared | Exclusive |
| Deploy time | Instant | Not instant (provisioned compute) |
| Best for | Default choice, scales to any traffic | Sustained heavy load, customization — heavy `eth_subscribe`, deep `eth_getLogs` sweeps, debug/trace pipelines; docs state it "typically lands cheaper than per-request billing at the same throughput" for such workloads |
| Reliability | 99.95% availability SLA; load balancer can switch nodes if one fails or lags 40+ blocks | N/A (single exclusive node) |

**Solana regions available** (`/docs/nodes-clouds-regions-and-locations`): Chainstack Global Network `global1` (worldwide, archive mode); Chainstack Cloud `lon1` (London, full) and `nyc1` (New York, full). Warp transaction support flagged "Available" only for London and New York full nodes, "NA" elsewhere. Solana's regional coverage is notably thinner than EVM chains (which list London/Singapore/Ashburn/Frankfurt/Los Angeles/Amsterdam).

**Yellowstone gRPC Geyser plugin** (`/docs/yellowstone-grpc-geyser-plugin`): streams Solana data directly from validator memory (transactions, tx-status updates, account state changes, block/block-metadata, slot changes, entry data), bypassing RPC-polling latency. Pricing/capacity:

| Price | Concurrent streams | Accounts/stream | Filters |
|---|---|---|---|
| $49/mo | 2 | 50 | up to 5 concurrent filters of same type/connection |
| $149/mo | 7 | 50 | same |
| $449/mo | 25 | 50 | same |

Positioned as the low-latency alternative to polling for analytics/MEV/trading-bot use. Directly relevant if ARIA wants push-based account/mint updates instead of the current batched `getMultipleAccounts` polling — note the 50-accounts-per-stream cap would require partitioning the watchlist across multiple streams if tracking more than 50 accounts at once.

**Warp (priority transaction landing)** (`/docs/warp-transactions`, `/docs/trader-node`): routes `sendTransaction`/`eth_sendRawTransaction` directly to bloXroute's relay network (Solana: "directly to the leader") for faster propagation. Speed-optimized only — docs explicitly note "speed-optimized, protection disabled", i.e. no front-running protection. Available on Ethereum, BNB Smart Chain, Solana. $0.15/tx sent via Warp, paid-plans-only, delivered via the regionally-bound Trader Node type.

**MEV Protection add-on** (`/docs/mev-protection`): covers Ethereum, BNB Smart Chain, Arbitrum, Base mainnet only — **not Solana**. Bypasses public mempool, proxies to a partner builder network. Pricing not disclosed.

**Solana MEV protection is a separate mechanism, not a Chainstack add-on**: Jito `dontfront` (`/docs/solana-mev-protection`) — add a `jitodontfront`-prefixed pubkey as a read-only account on the instruction, include a Jito tip (minimum 1,000 lamports, recommended 70/30 split between priority fee and tip), send to `https://mainnet.block-engine.jito.wtf/api/v1/transactions`. Forces bundle index 0, preventing sandwiching. No pricing beyond the Jito tip itself. Use Warp to land first; use `dontfront` for execution-price protection from others' MEV — the two are complementary, not substitutes.

**Unlimited Node add-on** (`/docs/unlimited-node`, `/docs/unlimited-node-add-on`): flat-fee endpoint by RPS tier (25/100/250/500/1000 RPS). Consumes zero plan RU quota, never triggers overage; exceeding the tier's RPS produces a rate-exceeded error, not a bill. Solana applicability was **not explicitly confirmed** in the fetched docs (general framing suggests "any existing Chainstack RPC node" but no Solana-specific line found) — verify directly in the console before relying on it for Solana.

**Relevance to ARIA's cost profile**: on Developer plan (3M RU/month), the two most relevant documented levers are (a) Yellowstone gRPC Geyser at $49/mo for push-based updates in place of polling, for whatever fits in 50 tracked accounts/stream, and (b) the Unlimited Node add-on if RPS — not RU volume — turns out to be the binding constraint (section 3.B shows Solana Mainnet RPS is 5 on Developer, likely the tighter limit today). Dedicated Node is the heavier option, justified only for sustained (not spiky) throughput.

---

### 5. Robinhood Chain support

First-class supported chain, confirmed across `/docs/protocols-networks`, `/docs/robinhood-methods`, `/docs/robinhood-tooling`, cross-checked against Chainstack's own blog (`chainstack.com/what-is-robinhood-chain/`, `chainstack.com/chainstack-introduces-robinhood-chain-support/`).

- Chain IDs: Mainnet `4663`, Testnet `46630` — matches the testnet chain_id already recorded elsewhere in this repo for the pilot's Safe+AllowanceModule contract.
- Client: Arbitrum Nitro (Arbitrum Orbit L2), full EVM compatibility — standard Ethereum tooling works against a Chainstack endpoint unchanged.
- Endpoint hostnames (from the blog, not the docs.chainstack.com technical pages): `https://robinhood-mainnet.core.chainstack.com/<KEY>` and `https://robinhood-testnet.core.chainstack.com/<KEY>`.
- Node types: Global Node (Elastic) and Dedicated Node, plus Chainstack Self-Hosted deployment. Modes: Full and Archive, both mainnet and testnet.
- Namespaces supported: `eth`, `debug`, `arb`, `net`, `web3`.
- Gotchas/limitations (`/docs/robinhood-methods`):
  - Parity-style `trace_*` is **not exposed** — use `debug_*` tracers instead (`callTracer`, `flatCallTracer`, `prestateTracer`, `4byteTracer`, plus opcode logger — standard Geth tracers, paid plans only).
  - Several `eth_*` methods unavailable: `eth_getAccount`, `eth_getTransactionBySenderAndNonce`, `eth_hashrate`, `eth_protocolVersion`.
  - `net_*` mostly unavailable — only `net_version` works (`net_listening`, `net_peerCount` do not).
  - All `txpool_*` (mempool) methods unavailable on Global Nodes for Robinhood Chain.
  - Billing: same universal rule, 1 RU full / 2 RU archive.
- Chain maturity (from Chainstack's blog, not the technical docs, useful context given the "infrastructure not built yet" caveat around ARIA's Robinhood pilot): testnet launched 2026.02.10, mainnet live since 2026.07.01; single-sequencer (operated by Robinhood, FIFO ordering, no MEV priority auction); ~0.1s block time; ~0.00002 ETH per tx; full ERC-4337 account abstraction (session keys, sponsored gas) — a real available primitive if ARIA later builds session-key-scoped agent spending on this chain, worth weighing against the Safe+AllowanceModule approach already in progress. TVL exceeded $540M within 3 weeks of mainnet launch, $700M+ onchain assets by 2026.07.23 — a live, capitalized chain, not a ghost-town testnet.
- Block explorers: mainnet `robinscan.io`, testnet `explorer.testnet.chain.robinhood.com`.

---

### 6. Best practices for reducing RU consumption

Chainstack's docs have no single consolidated "reduce your spend" page — guidance is scattered across several pages:

- **Batching does not reduce RU cost** (section 2) — the single most important thing to know before optimizing: HTTP-batch saves latency only, never money.
- **Multicall contracts genuinely reduce RU cost on EVM chains** (N calls to 1 RU) — not directly applicable to Solana, where `getMultipleAccounts` already serves that role at flat 1 RU regardless of account count.
- **Yellowstone gRPC Geyser** (section 4) is the clearest documented polling replacement for Solana — push-based, billed as a separate flat fee rather than RU-metered.
- **Unlimited Node / Dedicated Node** as flat-fee escapes from RU metering for sustained-high-volume workloads (section 4) — Chainstack's own guidance: heavy `eth_subscribe`, deep `eth_getLogs` sweeps, or debug/trace pipelines at sustained volume typically land cheaper on Dedicated Node than per-request billing at the same throughput.
- Recommended polling interval: the only concrete number found is illustrative, not prescriptive — a code example in `/docs/make-your-dapp-more-reliable-with-chainstack` polls `getBlock` every 10 seconds. No Chainstack-stated general cadence recommendation was found, Solana-specific or otherwise.
- Load-balancing/failover pattern shown in the same doc (round-robin across regional endpoints with automatic failover on error) is framed as a reliability pattern, explicitly labeled "proof of concept, needs further optimization for production" — not a cost-reduction technique, but relevant context for ARIA's existing Helius/Chainstack split (`docs/HANDOFF_RESOURCE_BUDGET.md`, 2026.08.21 entries).
- **No dedicated caching-guidance page found** — cache TTLs, invalidation strategy, which Solana fields are safe to cache: genuinely **not documented**, not a search gap.

---

### 7. Authentication / API key management / IP allowlisting

**Two distinct auth systems** (`/docs/authentication-methods-for-different-scenarios`):
- Platform API (project/node/org management): bearer token (API key) in the request header.
- Blockchain node RPC access, three methods:
  1. Auth token embedded in the URL path: `https://<network>.core.chainstack.com/<AUTH_TOKEN>` (HTTPS) / `wss://<network>.core.chainstack.com/ws/<AUTH_TOKEN>` (WS).
  2. HTTP Basic Auth (username/password in the `Authorization` header) as an alternative to embedding the token in the URL.
  3. gRPC (Yellowstone) uses a distinct `x-token` in request metadata, not the same auth token format.
- API keys managed at `console.chainstack.com/user/settings/api-keys` — same page the MCP server's authenticated tools read their credential from (section 8).

**IP allowlisting / Origin restriction — "Access rules"** (`/docs/access-rules`):
- Available **only for Global Nodes** (not stated as available for Dedicated Nodes).
- Two rule types: IP-based (individual IPv4/IPv6 — **CIDR ranges not supported**) and Origin-based (exact domain or wildcard `*.example.com` matching the HTTP `Origin` header).
- Matching logic: if any rules exist, only matching requests are processed; zero rules means all requests pass (subject to normal auth).
- Rules must be added one IP/origin at a time — no bulk creation.
- HTTP-referrer-based rules also exist as a third restriction type, for browser-side page fetches (`/docs/best-practices-for-securing-your-chainstack-endpoint`).

**General security recommendations** (`/docs/best-practices-for-securing-your-chainstack-endpoint`): treat keys/username/password as secrets, never commit or screenshot them; rotate on a schedule and immediately after any suspected leak (no specific cadence prescribed); never expose the RPC endpoint from frontend/wallet/browser code, route it through a backend that alone holds the key; HTTPS/WSS only (plain HTTP exposes the URL-embedded auth token and request bodies); enable 2FA on the Chainstack account; use scoped/least-privilege team roles rather than shared full-access credentials; keep dependencies patched, be wary of third-party tools requesting the RPC endpoint URL or wallet data. Consistent with ARIA's own "any API key created via a third-party web dashboard: tighten to minimum permissions before any use" reflex (see CLAUDE.md established facts, cf. `docs/HANDOFF_COINBASE_CDP.md`).

---

### 8. Chainstack MCP server / AI-agent tooling

**Current architecture**: a single unified **Chainstack MCP server** (`/docs/chainstack-mcp-server`) supersedes the older, separate **Solana MCP server** (`/docs/solana-mcp-server`), which the docs explicitly flag as deprecated.

**Tool inventory:**
- Public, no auth required: `search_docs`, `get_doc_page`, `get_platform_status`, `contact_chainstack`, `get_chainstack_pricing`.
- Authenticated, require an API key from `console.chainstack.com/user/settings/api-keys`:
  - Org/project management: `get_organization`, `get_deployment_options`, `list_projects`, `create_project`, `get_project`, `update_project`, `delete_project`.
  - Node management: `list_nodes`, `get_node`, `create_node`, `update_node`, `delete_node`.
  - `request_testnet_funds` — faucet top-up across 12 networks (Sepolia, Hoodi, Base Sepolia, Amoy, BNB testnet, zkSync Sepolia, Scroll Sepolia, HyperEVM testnet, Plasma testnet, Monad testnet, TON testnet, Solana devnet).
- Chainstack's own framing: all tools appear in an agent's tool list regardless of auth state — authenticated calls simply error out without a configured key.

**Deprecated Solana MCP server** (`/docs/solana-mcp-server`, kept here since references to it may still surface): read-only Solana RPC surface (account info/balance/multiple-accounts/program-accounts, block/slot queries, transaction lookup, cluster/epoch/health data, fee estimation). No transaction signing or sending capability documented — query-only. No auth details documented.

**Safety assessment for unsupervised/autonomous agent use**: the docs do not address spending limits, approval workflows, or billing guardrails for the authenticated tool set. With a configured API key, an agent has standing ability to `create_node`/`delete_node` (real infra with real billing consequences — a created Dedicated Node bills compute/storage hours regardless of whether the agent remembers to delete it) and `create_project`/`delete_project` (structural account changes, including deleting everything under a project). No dry-run flag, confirmation step, or cost-preview gate is documented on `create_node` before it provisions billable infrastructure. Given ARIA's own absolute guardrails (never self-modify guardrail files, capital actions fail-closed/human-gated, structural separation from `wallet_guard`), **the authenticated half of this MCP server should not be exposed to an unsupervised ARIA process** — nothing in Chainstack's own docs provides an equivalent guardrail layer. The read-only/public half (`search_docs`, `get_doc_page`, `get_platform_status`, `get_chainstack_pricing`, `contact_chainstack`) carries no cost or state-changing capability and is safe for unsupervised use. `request_testnet_funds` is low-risk (testnet-only, typically free, rate-limited by the faucet itself) but is still an authenticated, state-changing call.

**Discovery/self-configuration**: Chainstack promotes pointing any agent at `mcp.chainstack.com` — an agent-readable onboarding page an agent can fetch and self-configure from. Two install paths: "Skill Mode" (drop a `SKILL.md` into the agent's skill directory, e.g. `~/.claude/skills/chainstack/` for Claude Code) or standard MCP registration via CLI/config, across Claude Code, Codex, Cursor, Windsurf, VS Code, Gemini CLI, Antigravity, Claude.ai, ChatGPT. Not currently wired into ARIA — no action taken on this, purely a banked capability.

---

### Gaps / open questions

Verify before relying on any of these:

- Exact $/month for the Unlimited Node 100 RPS and 250 RPS tiers (only 25 RPS/$149 and 500 RPS/$3,199 confirmed from the pricing page).
- MEV Protection (EVM add-on) pricing — not disclosed anywhere in the fetched docs.
- Whether the Unlimited Node add-on applies to Solana specifically — general framing suggests yes, no Solana-specific confirmation found; check the console directly.
- `getMultipleAccounts` max accounts per call / max response size — Solana-protocol-level, not stated by Chainstack (standard Solana RPC caps at 100 pubkeys/call, unverified against Chainstack's own proxy behavior).
- API key rotation cadence — docs say "rotate on a schedule", no number given.
- Any consolidated cache-TTL / caching-strategy guidance — none found.
- Full per-tier feature matrix (Developer/Growth/Pro/Business) beyond the 3 confirmed facts (Trader Node/Warp and Debug-Trace paid-plans-only; private networking/node customization Enterprise-only).
- **Robinhood Chain endpoint maturity beyond the blog's own claims** — mainnet live since 2026.07.01 per Chainstack's blog, but no independent confirmation of production-grade reliability (uptime history, real incident record) was found; treat the TVL/asset figures as a liquidity/adoption signal, not an infrastructure-quality one.
- **Exact overage behavior at the moment a request would cross the cap mid-call** — docs describe the quota-exhausted state (block or bill) but not what happens to a request already in flight when the crossing happens.
- ARIA's actual current Chainstack plan tier: **Growth ($49/mo, 20M RU/month, 250 RPS general / 50 RPS Solana Mainnet)**, operator upgraded from Developer on 2026.08.24 — re-verify against `console.chainstack.com` before citing a headroom number regardless, it can change again.

### Real incidents (HANDOFF-format entries, moved here 24/08 from `docs/HANDOFF_RESOURCE_BUDGET.md` — same doctrine as every other component HANDOFF, distinct from the reference sections above)

------------------------------------------------------------

[CONFIG] Sujet    : pump.fun sourcing moved off program-wide streaming onto batched polling
Date : 2026.08.21  /  Probleme : program-wide logsSubscribe cost 1 693 000 Helius credits/day against a 1M/month plan (74 GB/day streamed, 74.7% of it carrying no decodable trade). Two earlier switchover attempts each starved the pocket for 13 minutes because that stream fed TWO things nobody had inventoried: discovery via active_mints(), and buyer history via get_flow() which MIN_DISTINCT_BUYERS reads.
Solution : both dependencies replaced BEFORE cutting. Discovery -> pumpfun_curve_tracker (free PumpPortal creations + getMultipleAccounts at 1 credit per CALL up to 100 accounts, this Chainstack polling path), measured at 48 qualified candidates/hour against the pocket's real 53 detections/hour. Buyer history -> pre-arm at PRE_ARM_PROGRESS=0.60, ~26s before the entry window, so distinct_buyers is populated when consider_candidate reads it. Verified live: entry at 19:21 after the 19:14 cut, discovery tracker=5 fallback=4, zero refused watches — shadow_persistent.py + services/pumpfun_curve_tracker.py (5493f3e1, fdccb4bc)
Lecon : before narrowing ANY feed, inventory every consumer that reads it as a SOURCE and every filter that reads its VALUES. Checking one and not the other cost two outages the same day.

------------------------------------------------------------

[CONFIG] Subject : curve-tracker polling kept spending Chainstack credits while the kill-switch was armed
Date : 2026.08.24 / Problem : operator noticed 37% of the monthly 3M Chainstack request-unit quota burned in 4 days (1,089,203 units, a 575,978-unit spike alone on 23/08), all `Solana Mainnet`/`Elastic`. `curve_tracker_shadow_loop()` in `shadow_persistent.py` calls `poll_due()` (the only credit-spending step, batched `getMultipleAccounts`) unconditionally, every 1s tick, with zero kill-switch check -- the existing `outgoing_pause.is_paused(strict=True) or custody_pause.is_paused()` check (line ~798) only guards real trade execution inside the pilot, never the shadow sourcing/scan loop. So with `/stop` armed (issue #207, still armed at the time of this fix) the pocket kept polling and paying for candidates it could never act on.
Solution : `poll_due()` skipped whenever `outgoing_pause.is_paused(strict=True) or custody_pause.is_paused()` is true -- discovery (free PumpPortal feed), `add()`, `prune()` and state-save stay unconditional so no mint is lost while paused, only the paid RPC call is gated. Periodic report line now logs `killswitch_polling_skipped=<bool>` for visibility. This file lives outside the git repo (`/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py`, standalone systemd service `aria-shadow-persistent.service`) so there is no commit hash -- edited in place and the service restarted directly, confirmed healthy post-restart via `systemctl status`/`journalctl`.
`solana-robinhood-shadow/shadow_persistent.py` (outside repo, no hash).

------------------------------------------------------------

[CODE] Sujet    : plan upgrade to Growth exposed a real newHeads billing waste, RPS throttles recalibrated, daily per-chain RU cap added
Date : 2026.08.24 / Probleme : operator upgraded Developer -> Growth ($49/mo) to prepare Base+Robinhood polling on the same shared quota. Investigating showed three things: (1) `evm_swap_ws.py` (already live for Base) held a PERMANENT `newHeads` subscription open purely as a `process_subscriptions()` keepalive workaround, its content always discarded by `_handle_notification` -- billed 1 RU/push regardless (confirmed: docs.chainstack.com/docs/request-units), which on Robinhood Chain's 100ms block time alone would be ~864k RU/day for nothing; (2) Solana Mainnet's real RPS ceiling moved 5->50 with the plan upgrade but `solana_rpc_budget.py`/`pumpfun_curve_tracker.py` stayed throttled to 90% of the OLD number (4.5); (3) no mechanism existed to stop one chain's polling from eating the whole shared 20M/month quota before the other two chains got a turn -- Solana alone hit 575,978 units in a single day on 23/08.
Solution : `evm_swap_ws.py`'s `newHeads` keepalive now opens only while `_active_sub_ids` (real logs subscriptions) is empty, closes the moment a pool is tracked, reopens if the pool set empties out again. `CHAINSTACK_MAX_RPS`/`DEFAULT_RATE_PER_SECOND` raised to 45 (90% of the real 50 RPS Solana ceiling on Growth). New `chainstack_ru_budget.py`: hard 200k-units/day cap per chain (operator-set), independently tracked per chain so a runaway chain can never starve the others' share -- not yet wired into any caller, `shadow_persistent.py` (outside this repo, edited in place on the VPS) is the next integration point. 11568/11568 tests pass -- services/evm_swap_ws.py, services/solana_rpc_budget.py, services/pumpfun_curve_tracker.py, services/chainstack_ru_budget.py (387bc117).
