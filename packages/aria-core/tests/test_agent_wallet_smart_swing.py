"""Autonomous swing-pocket execution via a delegated spender (Smart Account
migration, Model B, 07/23). These cover the PURE safety-envelope builders
(constants + Spend Permission input) -- the Policy + live execution wiring are
a later, hardware-validated step (see docs/HANDOFF_COINBASE_CDP.md). Nothing
here touches the network or executes any real spend."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_smart_swing as sw
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS


# ── constants / identities ───────────────────────────────────────────────────


def test_addresses_match_the_deployed_monitor():
    """Guard against a copy-paste drift from the deployed
    agent_wallet_monitor.MONITORED_WALLETS -- these must stay in lockstep
    (real capital: a wrong address would grant a spend permission on the wrong
    account)."""
    from aria_core.agent_wallet_monitor import MONITORED_WALLETS

    assert sw.SMART_ST_ADDRESS == MONITORED_WALLETS["aria-smart-st-EVM"]
    assert sw.SMART_VC_ADDRESS == MONITORED_WALLETS["aria-smart-vc-EVM"]


def test_spend_permission_manager_address_matches_the_sdk():
    from cdp.spend_permissions import SPEND_PERMISSION_MANAGER_ADDRESS

    assert sw.SPEND_PERMISSION_MANAGER_ADDRESS == SPEND_PERMISSION_MANAGER_ADDRESS


def test_gate_off_by_default(monkeypatch):
    monkeypatch.delenv(sw._SMART_SWING_GATE, raising=False)
    assert sw.smart_swing_enabled() is False


def test_gate_on_when_env_set(monkeypatch):
    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    assert sw.smart_swing_enabled() is True


# ── usd_to_atomic_usdc ───────────────────────────────────────────────────────


@pytest.mark.parametrize("usd,atomic", [
    (50.0, 50_000_000), (1.0, 1_000_000), (0.5, 500_000), (15.0, 15_000_000),
])
def test_usd_to_atomic_usdc(usd, atomic):
    assert sw.usd_to_atomic_usdc(usd) == atomic


# ── build_spend_permission_input ─────────────────────────────────────────────


def test_default_spend_permission_encodes_the_operator_cap():
    """The operator's explicit 07/24 decision: 2500$/week, auto-renewing --
    deliberately LARGE (~10x the ~250$ real capital) so it never blocks a
    legitimate trade; the real brake is the loss circuit breaker, not this cap."""
    sp = sw.build_spend_permission_input()
    d = sp.model_dump()
    assert d["allowance"] == 2_500_000_000  # $2500 in USDC atomic units
    assert d["period_in_days"] == 7
    assert sw.SPEND_PERMISSION_ALLOWANCE_USD == 2500.0
    assert sw.SPEND_PERMISSION_ALLOWANCE_USD < sw._MAX_SANE_ALLOWANCE_USD
    assert d["account"] == sw.SMART_ST_ADDRESS  # pulls FROM the swing pocket
    assert d["spender"] == sw.SPENDER_ADDRESS   # ...to the dedicated spender
    from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

    assert d["token"] == USDC_BASE_ADDRESS


def test_spend_permission_never_grants_on_the_vc_pocket():
    """aria-smart-vc must NEVER get a delegated spend permission (every VC
    action requires the Tangem owner) -- the builder always targets the swing
    pocket, structurally."""
    sp = sw.build_spend_permission_input()
    assert sp.model_dump()["account"] != sw.SMART_VC_ADDRESS


def test_custom_allowance_within_range():
    sp = sw.build_spend_permission_input(allowance_usd=100.0, period_days=14)
    d = sp.model_dump()
    assert d["allowance"] == 100_000_000
    assert d["period_in_days"] == 14


@pytest.mark.parametrize("bad_allowance", [0, -1.0, -50.0])
def test_rejects_non_positive_allowance(bad_allowance):
    with pytest.raises(ValueError):
        sw.build_spend_permission_input(allowance_usd=bad_allowance)


def test_rejects_allowance_above_sane_ceiling():
    """The core safety invariant: an 'unlimited'/absurd allowance can never be
    produced here (it would silently remove safety layer #2)."""
    with pytest.raises(ValueError):
        sw.build_spend_permission_input(allowance_usd=sw._MAX_SANE_ALLOWANCE_USD + 0.01)


def test_accepts_allowance_exactly_at_ceiling():
    sp = sw.build_spend_permission_input(allowance_usd=sw._MAX_SANE_ALLOWANCE_USD)
    assert sp.model_dump()["allowance"] == sw.usd_to_atomic_usdc(sw._MAX_SANE_ALLOWANCE_USD)


@pytest.mark.parametrize("bad_period", [0, -1, -7])
def test_rejects_non_positive_period(bad_period):
    with pytest.raises(ValueError):
        sw.build_spend_permission_input(period_days=bad_period)


# ── build_swap_only_policy ───────────────────────────────────────────────────

ROUTER = "0x1111111111111111111111111111111111111111"


def test_swap_only_policy_scope_and_two_rules():
    pol = sw.build_swap_only_policy(ROUTER)
    assert pol.scope == "account"
    assert len(pol.rules) == 2
    # both rules are accept rules -- everything else is default-denied by the
    # CDP Policy Engine (that default-deny is what blocks an arbitrary transfer).
    assert all(r.action == "accept" for r in pol.rules)


def test_swap_only_policy_rule1_allowlists_the_given_router():
    pol = sw.build_swap_only_policy(ROUTER)
    swap_rule = pol.rules[0]
    crit = swap_rule.criteria[0]
    assert crit.type == "evmAddress"
    assert crit.operator == "in"
    assert crit.addresses == [ROUTER]


def test_swap_only_policy_rule1_bounds_net_usd_change_to_the_per_tx_cap():
    """VOLET 1 (calldata hole fix): rule 1 is no longer 'any calldata to the
    router' -- it ALSO carries a NetUSDChangeCriterion capping the net USD a
    single router call may move to the per-tx ceiling
    (SPEND_PERMISSION_ALLOWANCE_USD, in cents). Both criteria AND together, so a
    router call is accepted only within the cap -- the loss under a spender
    compromise is BOUNDED, never zero."""
    pol = sw.build_swap_only_policy(ROUTER)
    crits = pol.rules[0].criteria
    assert len(crits) == 2  # router-address AND net-USD-change
    net = crits[1]
    assert net.type == "netUSDChange"
    assert net.operator == "<="
    # reuses the SAME per-tx constant, never a second driftable number
    assert net.changeCents == int(round(sw.SPEND_PERMISSION_ALLOWANCE_USD * 100))
    assert net.changeCents == sw._per_tx_cap_cents()
    assert net.changeCents >= 0  # cdp-sdk field constraint (ge=0)


def test_swap_only_policy_rule2_return_transfer_is_token_agnostic_to_the_swing_pocket():
    """The single most delicate carve-out: an ERC-20 transfer whose recipient
    is the swing pocket, token-AGNOSTIC (no fixed token contract pinned) --
    because the output token varies per trade."""
    pol = sw.build_swap_only_policy(ROUTER)
    ret_rule = pol.rules[1]
    crit = ret_rule.criteria[0]
    assert crit.type == "evmData"
    assert crit.abi.value == "erc20"  # token-agnostic: matches ANY erc20's transfer
    cond = crit.conditions[0]
    assert cond.function == "transfer"
    param = cond.params[0]
    assert param.name == "to"
    assert param.operator == "in"
    # only ever the swing pocket, never an arbitrary address
    assert param.values == [sw.SMART_ST_ADDRESS]


def test_swap_only_policy_return_transfer_never_targets_the_vc_pocket():
    pol = sw.build_swap_only_policy(ROUTER)
    param = pol.rules[1].criteria[0].conditions[0].params[0]
    assert sw.SMART_VC_ADDRESS not in param.values


@pytest.mark.parametrize("bad_router", ["", "0x123", "not-an-address", "0x" + "z" * 40, None])
def test_swap_only_policy_rejects_malformed_router(bad_router):
    """Fail-closed: a garbage router must never produce a policy (it would
    default-deny every swap or error opaquely at attach time)."""
    with pytest.raises(ValueError):
        sw.build_swap_only_policy(bad_router)


def test_swap_only_policy_can_be_serialized_by_the_sdk():
    """Sanity that the object is a real, valid CDP policy the SDK accepts
    (model_dump never raises on a well-formed CreatePolicyOptions)."""
    pol = sw.build_swap_only_policy(ROUTER)
    dumped = pol.model_dump()
    assert dumped["scope"] == "account"
    assert len(dumped["rules"]) == 2


# ── execute_smart_swing_swap (guarded execution path) ────────────────────────


@pytest.fixture(autouse=True)
def _isolated_swing_db(tmp_path, monkeypatch):
    from aria_core import agent_wallet_log
    from aria_core.paths import configure_data_dir

    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "smart_swing_test.db"))
    configure_data_dir(tmp_path)
    yield


@pytest.fixture(autouse=True)
def _swing_gate_and_pause_reset(monkeypatch):
    monkeypatch.delenv(sw._SMART_SWING_GATE, raising=False)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


class _Recorder:
    """Records the ordered sequence of injected CDP calls for assertions."""

    def __init__(self, *, pull=None, swap=None, ret=None):
        self.calls: list[tuple[str, dict]] = []
        self._pull = pull
        self._swap = swap
        self._ret = ret

    async def pull(self, **kwargs):
        self.calls.append(("pull", kwargs))
        if isinstance(self._pull, Exception):
            raise self._pull
        return self._pull or {"tx_hash": "0xpull"}

    async def swap(self, **kwargs):
        self.calls.append(("swap", kwargs))
        if isinstance(self._swap, Exception):
            raise self._swap
        return self._swap or {"tx_hash": "0xswap", "amount_out": 1.5, "amount_out_atomic": 1_500_000}

    async def ret(self, **kwargs):
        self.calls.append(("return", kwargs))
        if isinstance(self._ret, Exception):
            raise self._ret
        return self._ret or {"tx_hash": "0xreturn"}


async def _balance_20() -> float:
    return 20.0


async def _run_swap(rec: _Recorder, monkeypatch, **overrides):
    """Drive the BUY primitive (VOLET 2a: pull -> swap, token STAYS in spender,
    no return step, so no return_transfer_fn)."""
    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    kwargs = dict(
        token_out="0x" + "b" * 40, amount_in_usd=10.0, balance_fn=_balance_20,
        spend_pull_fn=rec.pull, swap_fn=rec.swap,
    )
    kwargs.update(overrides)
    return await sw.execute_smart_swing_swap(**kwargs)


async def _run_sell(rec: _Recorder, monkeypatch, **overrides):
    """Drive the SELL primitive (VOLET 2b: swap held token -> USDC, then
    MANDATORY return of the USDC to aria-smart-st)."""
    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    kwargs = dict(
        token_in="0x" + "b" * 40, amount_in_tokens=1000.0, value_in_usd=10.0,
        balance_fn=_balance_2000_tokens, swap_fn=rec.swap, return_transfer_fn=rec.ret,
    )
    kwargs.update(overrides)
    return await sw.execute_smart_swing_sell(**kwargs)


async def _balance_2000_tokens() -> float:
    return 2000.0


async def _last_log_row():
    from aria_core import agent_wallet_log

    rows = await agent_wallet_log.list_transactions()
    return rows[0] if rows else None


@pytest.mark.asyncio
async def test_swap_blocked_when_gate_disabled_by_default():
    rec = _Recorder()
    result = await sw.execute_smart_swing_swap(
        token_out="0x" + "b" * 40, amount_in_usd=10.0, balance_fn=_balance_20,
        spend_pull_fn=rec.pull, swap_fn=rec.swap,
    )
    assert result.status == "blocked"
    assert "ARIA_SMART_SWING_ENABLED" in result.reason
    assert rec.calls == []  # no CDP call attempted
    row = await _last_log_row()
    assert row["status"] == "blocked"
    assert row["wallet_product"] == sw.WALLET_PRODUCT


@pytest.mark.asyncio
async def test_swap_blocked_when_kill_switch_paused(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    rec = _Recorder()
    result = await _run_swap(rec, monkeypatch)
    assert result.status == "blocked"
    assert rec.calls == []


@pytest.mark.asyncio
async def test_swap_kill_switch_checked_strict(monkeypatch):
    """Real-money path must use strict=True (fail-closed on unreadable pause)."""
    seen = {}

    def fake_is_paused(**kw):
        seen.update(kw)
        return False

    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", fake_is_paused)
    rec = _Recorder()
    await _run_swap(rec, monkeypatch)
    assert seen.get("strict") is True


@pytest.mark.asyncio
async def test_swap_blocked_on_non_positive_amount(monkeypatch):
    rec = _Recorder()
    result = await _run_swap(rec, monkeypatch, amount_in_usd=0.0)
    assert result.status == "blocked"
    assert rec.calls == []


@pytest.mark.asyncio
async def test_swap_blocked_above_per_tx_cap(monkeypatch):
    """Per-tx cap bounded by the spend-permission allowance, never beyond."""
    rec = _Recorder()
    result = await _run_swap(
        rec, monkeypatch,
        amount_in_usd=sw.SPEND_PERMISSION_ALLOWANCE_USD + 0.01,
        balance_fn=lambda: _huge_balance(),
    )
    assert result.status == "blocked"
    assert "plafond par transaction" in result.reason
    assert rec.calls == []


async def _huge_balance() -> float:
    return 10_000_000.0


@pytest.mark.asyncio
async def test_swap_blocked_when_balance_unavailable_none(monkeypatch):
    async def none_balance():
        return None

    rec = _Recorder()
    result = await _run_swap(rec, monkeypatch, balance_fn=none_balance)
    assert result.status == "blocked"
    assert "fail-closed" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_swap_blocked_when_balance_raises(monkeypatch):
    async def boom_balance():
        raise RuntimeError("rpc down")

    rec = _Recorder()
    result = await _run_swap(rec, monkeypatch, balance_fn=boom_balance)
    assert result.status == "blocked"
    assert "fail-closed" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_swap_blocked_above_real_balance(monkeypatch):
    async def small_balance():
        return 2.0

    rec = _Recorder()
    result = await _run_swap(rec, monkeypatch, amount_in_usd=10.0, balance_fn=small_balance)
    assert result.status == "blocked"
    assert "solde réel" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_buy_happy_path_pull_then_swap_token_held_in_spender(monkeypatch):
    """VOLET 2a: a BUY is pull -> swap and STOPS -- the token stays in the
    spender by design, there is NO return step."""
    rec = _Recorder()
    result = await _run_swap(rec, monkeypatch)
    assert result.status == "ok"
    assert result.funds_stranded is False
    assert result.pull_tx_hash == "0xpull"
    assert result.swap_tx_hash == "0xswap"
    assert result.return_tx_hash == ""      # a buy never returns anything
    assert result.amount_out == 1.5         # tokens received, now held in spender
    assert result.amount_out_atomic == 1_500_000  # exact atomic, threadable to a later sell
    # exact order: pull -> swap, and NOTHING after (no return)
    assert [c[0] for c in rec.calls] == ["pull", "swap"]
    # pull value is the USDC atomic amount of amount_in_usd
    assert rec.calls[0][1]["value_atomic"] == sw.usd_to_atomic_usdc(10.0)
    row = await _last_log_row()
    assert row["status"] == "ok"
    assert row["wallet_product"] == sw.WALLET_PRODUCT
    assert row["amount_in"] == 10.0          # USD spent (positions reads as cost_usd)
    assert row["amount_out"] == 1.5          # tokens received
    assert row["to_address"] == ""           # nothing returned on a buy


@pytest.mark.asyncio
async def test_buy_default_wallet_product_and_canary_override(monkeypatch):
    """A normal buy logs under the real swing tag; a canary-tagged buy logs
    under the separate canary product (kept out of the real-position feed)."""
    rec = _Recorder()
    await _run_swap(rec, monkeypatch)
    assert (await _last_log_row())["wallet_product"] == sw.WALLET_PRODUCT
    rec2 = _Recorder()
    await _run_swap(rec2, monkeypatch, wallet_product=sw.CANARY_WALLET_PRODUCT)
    assert (await _last_log_row())["wallet_product"] == sw.CANARY_WALLET_PRODUCT


@pytest.mark.asyncio
async def test_swap_forces_slippage_regardless_of_caller(monkeypatch):
    rec = _Recorder()
    await _run_swap(rec, monkeypatch, slippage_bps=3000)  # caller asks 30%
    swap_call = next(c for c in rec.calls if c[0] == "swap")
    assert swap_call[1]["slippage_bps"] == sw.MAX_SLIPPAGE_BPS  # forced to 10%


@pytest.mark.asyncio
async def test_swap_pull_failure_leaves_funds_safe(monkeypatch):
    rec = _Recorder(pull=RuntimeError("permission expired"))
    result = await _run_swap(rec, monkeypatch)
    assert result.status == "failed"
    assert result.funds_stranded is False  # nothing pulled -> USDC still in aria-smart-st
    assert [c[0] for c in rec.calls] == ["pull"]  # swap/return never attempted
    row = await _last_log_row()
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_swap_failure_after_pull_flags_stranded(monkeypatch):
    rec = _Recorder(swap=RuntimeError("no liquidity"))
    result = await _run_swap(rec, monkeypatch)
    assert result.status == "failed"
    assert result.funds_stranded is True  # USDC pulled into the spender, needs recovery
    assert [c[0] for c in rec.calls] == ["pull", "swap"]
    assert "récupération vers aria-smart-st" in result.reason
    row = await _last_log_row()
    assert row["status"] == "failed"
    assert row["to_address"] == sw.SMART_ST_ADDRESS  # recovery destination surfaced


@pytest.mark.asyncio
async def test_swap_blocked_while_loss_breaker_armed(monkeypatch):
    """Defense-in-depth: an armed loss circuit breaker blocks the execution
    primitive itself, no CDP call attempted -- even without a cycle checking it."""
    sw.block_swing_swaps("drawdown 22% depuis le plus haut", by="test")
    rec = _Recorder()
    result = await _run_swap(rec, monkeypatch)
    assert result.status == "blocked"
    assert rec.calls == []
    row = await _last_log_row()
    assert row["status"] == "blocked"


# ── Loss circuit breaker ─────────────────────────────────────────────────────


@pytest.fixture()
def _clean_breaker(tmp_path, monkeypatch):
    from aria_core.paths import configure_data_dir

    configure_data_dir(tmp_path)
    return tmp_path


def test_breaker_default_not_blocked(_clean_breaker):
    status = sw.swing_breaker_status()
    assert status["blocked"] is False
    assert status["readable"] is True
    blocked, reason = sw.blocks_swing_swaps()
    assert blocked is False
    assert reason is None


def test_breaker_block_and_manual_resume_roundtrip(_clean_breaker):
    sw.block_swing_swaps("drawdown 22.0% depuis le plus haut", by="test")
    status = sw.swing_breaker_status()
    assert status["blocked"] is True
    assert "drawdown" in status["reason"]
    blocked, reason = sw.blocks_swing_swaps()
    assert blocked is True

    sw.resume_swing_swaps(by="operator")
    assert sw.swing_breaker_status()["blocked"] is False
    blocked, _ = sw.blocks_swing_swaps()
    assert blocked is False


def test_breaker_block_preserves_high_water_mark(_clean_breaker):
    sw._persist_swing({"high_water_mark": 300.0})
    sw.block_swing_swaps("5 pertes consécutives")
    assert sw.swing_breaker_status()["high_water_mark"] == 300.0
    sw.resume_swing_swaps()
    # resume must not reset the drawdown reference
    assert sw.swing_breaker_status()["high_water_mark"] == 300.0


def test_breaker_global_pause_also_blocks(_clean_breaker, monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    blocked, reason = sw.blocks_swing_swaps()
    assert blocked is True


def test_breaker_fail_closed_on_corrupted_state(_clean_breaker):
    path = sw._swing_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    status = sw.swing_breaker_status()
    assert status["readable"] is False
    blocked, reason = sw.blocks_swing_swaps()
    assert blocked is True  # fail-closed
    assert "illisible" in reason


def test_count_consecutive_losses_leading_run():
    # most-recent-first: 3 losses then a win stops the count
    assert sw._count_consecutive_losses([-5.0, -2.0, -1.0, 3.0, -9.0]) == 3
    assert sw._count_consecutive_losses([1.0, -1.0]) == 0  # newest is a win
    assert sw._count_consecutive_losses([]) == 0
    # capped at the threshold, never counts further
    assert sw._count_consecutive_losses([-1.0] * 20) == sw.HARD_CONSECUTIVE_LOSSES


@pytest.mark.asyncio
async def test_evaluate_updates_high_water_mark(_clean_breaker):
    state = await sw.evaluate_swing_risk(equity_usd=250.0, recent_pnls=[])
    assert state.high_water_mark == 250.0
    # a higher equity raises the HWM
    state2 = await sw.evaluate_swing_risk(equity_usd=300.0, recent_pnls=[])
    assert state2.high_water_mark == 300.0
    assert state2.blocked is False


@pytest.mark.asyncio
async def test_evaluate_arms_on_drawdown_and_runs_post_mortem(_clean_breaker):
    notified: list[str] = []

    async def notify(msg):
        notified.append(msg)

    async def post_mortem():
        return "Post-mortem : failles trouvées — CANAL X : entrée trop tardive."

    # establish a high-water mark of 300
    await sw.evaluate_swing_risk(equity_usd=300.0, recent_pnls=[])
    # equity drops to 230 -> -23.3% drawdown, past the -20% hard threshold
    state = await sw.evaluate_swing_risk(
        equity_usd=230.0, recent_pnls=[], post_mortem_fn=post_mortem, notify_fn=notify,
    )
    assert state.newly_triggered_hard is True
    assert state.blocked is True
    assert state.drawdown_pct >= sw.HARD_DRAWDOWN_PCT
    # the operator was notified WITH the post-mortem result (never a mute arming)
    assert len(notified) == 1
    assert "coupe-circuit swing" in notified[0]
    assert "Post-mortem" in notified[0]
    assert "ARGENT RÉEL" in notified[0]


@pytest.mark.asyncio
async def test_evaluate_arms_on_consecutive_losses(_clean_breaker):
    notified: list[str] = []

    async def notify(msg):
        notified.append(msg)

    await sw.evaluate_swing_risk(equity_usd=250.0, recent_pnls=[])
    losses = [-1.0] * sw.HARD_CONSECUTIVE_LOSSES
    state = await sw.evaluate_swing_risk(equity_usd=245.0, recent_pnls=losses, notify_fn=notify)
    assert state.newly_triggered_hard is True
    assert state.blocked is True
    assert state.consecutive_losses == sw.HARD_CONSECUTIVE_LOSSES
    assert len(notified) == 1
    # no post_mortem_fn -> the message says so honestly, never mute
    assert "post-mortem non exécuté" in notified[0]


@pytest.mark.asyncio
async def test_evaluate_below_threshold_does_not_arm(_clean_breaker):
    await sw.evaluate_swing_risk(equity_usd=250.0, recent_pnls=[])
    state = await sw.evaluate_swing_risk(equity_usd=240.0, recent_pnls=[-1.0, -2.0])  # -4% only
    assert state.newly_triggered_hard is False
    assert state.blocked is False


@pytest.mark.asyncio
async def test_evaluate_not_re_triggered_when_already_blocked(_clean_breaker):
    calls: list[int] = []

    async def notify(msg):
        calls.append(1)

    await sw.evaluate_swing_risk(equity_usd=300.0, recent_pnls=[])
    await sw.evaluate_swing_risk(equity_usd=230.0, recent_pnls=[], notify_fn=notify)  # arms
    # a second breach while already armed must NOT re-notify
    state = await sw.evaluate_swing_risk(equity_usd=200.0, recent_pnls=[], notify_fn=notify)
    assert state.newly_triggered_hard is False
    assert state.blocked is True
    assert len(calls) == 1  # only the first trigger notified


@pytest.mark.asyncio
async def test_evaluate_never_auto_resumes(_clean_breaker):
    """Once armed, a later recovery in equity must NOT lift the breaker
    automatically (manual resume only)."""
    await sw.evaluate_swing_risk(equity_usd=300.0, recent_pnls=[])
    await sw.evaluate_swing_risk(equity_usd=230.0, recent_pnls=[])  # arms
    state = await sw.evaluate_swing_risk(equity_usd=320.0, recent_pnls=[])  # recovered
    assert state.blocked is True  # still armed
    sw.resume_swing_swaps(by="operator")
    assert sw.swing_breaker_status()["blocked"] is False


# ── run_swing_post_mortem (adversarial reuse of trade_devils_advocate) ────────


@pytest.mark.asyncio
async def test_post_mortem_empty_buys():
    out = await sw.run_swing_post_mortem([])
    assert "aucun achat" in out.lower()


@pytest.mark.asyncio
async def test_post_mortem_reuses_devils_advocate_prompt_and_reports_flaws():
    seen_system: list[str] = []

    async def fake_llm(prompt, system, **kwargs):
        seen_system.append(system)
        return '{"verdict": "flawed", "flaw": "over-sized on a thin pool", "lesson": "cap size on low liquidity"}'

    buys = [
        {"symbol": "AAA", "thesis": "t", "entry_price": 1.0, "exit_price": 0.5,
         "pnl_pct": -50.0, "pnl_usd": -10.0, "close_reason": "stop"},
    ]
    out = await sw.run_swing_post_mortem(buys, llm=fake_llm)
    assert "AAA" in out
    assert "cap size on low liquidity" in out
    # faithfully reuses the SAME adversarial system prompt as trade_devils_advocate
    from aria_core.skills import trade_devils_advocate as tda

    assert seen_system and seen_system[0] == tda._REVIEW_SYSTEM


@pytest.mark.asyncio
async def test_post_mortem_sound_verdicts_report_no_structural_flaw():
    async def fake_llm(prompt, system, **kwargs):
        return '{"verdict": "sound", "flaw": "", "lesson": ""}'

    buys = [{"symbol": "BBB", "thesis": "t", "pnl_pct": -10.0, "pnl_usd": -2.0}]
    out = await sw.run_swing_post_mortem(buys, llm=fake_llm)
    assert "aucune faille" in out.lower()


@pytest.mark.asyncio
async def test_post_mortem_survives_llm_failure():
    async def boom_llm(prompt, system, **kwargs):
        raise RuntimeError("provider down")

    buys = [{"symbol": "CCC", "pnl_usd": -1.0}]
    out = await sw.run_swing_post_mortem(buys, llm=boom_llm)
    assert "LLM indisponible" in out


@pytest.mark.asyncio
async def test_post_mortem_handles_offschema_llm_answer():
    async def junk_llm(prompt, system, **kwargs):
        return "not json at all"

    buys = [{"symbol": "DDD", "pnl_usd": -1.0}]
    out = await sw.run_swing_post_mortem(buys, llm=junk_llm)
    # off-schema degrades to a safe "sound" (no fabricated flaw)
    assert "aucune faille" in out.lower()


# ── VOLET 2b: execute_smart_swing_sell (held token -> USDC, mandatory return) ──


@pytest.mark.asyncio
async def test_sell_happy_path_swap_then_return_no_pull(monkeypatch):
    """A SELL is swap -> MANDATORY return (no pull -- the spender already holds
    the token). The USDC output is returned to aria-smart-st."""
    rec = _Recorder()
    result = await _run_sell(rec, monkeypatch)
    assert result.status == "ok"
    assert result.funds_stranded is False
    assert result.pull_tx_hash == ""        # a sell never pulls
    assert result.swap_tx_hash == "0xswap"
    assert result.return_tx_hash == "0xreturn"
    assert result.amount_out == 1.5         # USDC received
    # exact order: swap -> return (no pull step)
    assert [c[0] for c in rec.calls] == ["swap", "return"]
    assert rec.calls[1][1]["to_address"] == sw.SMART_ST_ADDRESS  # USDC back to swing pocket
    row = await _last_log_row()
    assert row["status"] == "ok"
    assert row["wallet_product"] == sw.WALLET_PRODUCT
    assert row["token_in"] == "0x" + "b" * 40   # the token sold
    assert row["token_out"] == USDC_BASE_ADDRESS
    assert row["amount_in"] == 1000.0            # tokens sold (positions qty_sold)
    assert row["amount_out"] == 1.5              # USDC received (positions proceeds_usd)
    assert row["to_address"] == sw.SMART_ST_ADDRESS


@pytest.mark.asyncio
async def test_sell_blocked_when_gate_disabled_by_default():
    rec = _Recorder()
    result = await sw.execute_smart_swing_sell(
        token_in="0x" + "b" * 40, amount_in_tokens=1000.0, value_in_usd=10.0,
        balance_fn=_balance_2000_tokens, swap_fn=rec.swap, return_transfer_fn=rec.ret,
    )
    assert result.status == "blocked"
    assert "ARIA_SMART_SWING_ENABLED" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_blocked_when_kill_switch_paused(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    rec = _Recorder()
    result = await _run_sell(rec, monkeypatch)
    assert result.status == "blocked"
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_blocked_while_loss_breaker_armed(monkeypatch):
    sw.block_swing_swaps("drawdown 22%", by="test")
    rec = _Recorder()
    result = await _run_sell(rec, monkeypatch)
    assert result.status == "blocked"
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_blocked_on_non_positive_quantity(monkeypatch):
    rec = _Recorder()
    result = await _run_sell(rec, monkeypatch, amount_in_tokens=0.0)
    assert result.status == "blocked"
    assert "nulle ou négative" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_blocked_on_non_positive_value(monkeypatch):
    """A sell must be valued to be capped -- an unpriced sell is fail-closed."""
    rec = _Recorder()
    result = await _run_sell(rec, monkeypatch, value_in_usd=0.0)
    assert result.status == "blocked"
    assert "non valorisé" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_blocked_above_per_tx_cap(monkeypatch):
    rec = _Recorder()
    result = await _run_sell(
        rec, monkeypatch, value_in_usd=sw.SPEND_PERMISSION_ALLOWANCE_USD + 0.01,
    )
    assert result.status == "blocked"
    assert "plafond par transaction" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_blocked_when_token_balance_unavailable(monkeypatch):
    async def none_balance():
        return None

    rec = _Recorder()
    result = await _run_sell(rec, monkeypatch, balance_fn=none_balance)
    assert result.status == "blocked"
    assert "fail-closed" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_blocked_when_token_balance_raises(monkeypatch):
    async def boom_balance():
        raise RuntimeError("rpc down")

    rec = _Recorder()
    result = await _run_sell(rec, monkeypatch, balance_fn=boom_balance)
    assert result.status == "blocked"
    assert "fail-closed" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_blocked_above_token_balance(monkeypatch):
    async def small_token_balance():
        return 500.0

    rec = _Recorder()
    result = await _run_sell(rec, monkeypatch, amount_in_tokens=1000.0, balance_fn=small_token_balance)
    assert result.status == "blocked"
    assert "solde réel du token" in result.reason
    assert rec.calls == []


@pytest.mark.asyncio
async def test_sell_forces_slippage_regardless_of_caller(monkeypatch):
    rec = _Recorder()
    await _run_sell(rec, monkeypatch, slippage_bps=3000)
    swap_call = next(c for c in rec.calls if c[0] == "swap")
    assert swap_call[1]["slippage_bps"] == sw.MAX_SLIPPAGE_BPS


@pytest.mark.asyncio
async def test_sell_threads_exact_atomic_quantity(monkeypatch):
    rec = _Recorder()
    await _run_sell(rec, monkeypatch, amount_in_tokens_atomic=1_500_000)
    swap_call = next(c for c in rec.calls if c[0] == "swap")
    assert swap_call[1]["amount_in_tokens"] == 1000.0
    assert swap_call[1]["amount_in_tokens_atomic"] == 1_500_000


@pytest.mark.asyncio
async def test_sell_swap_failure_is_not_stranded_token_stays_in_spender(monkeypatch):
    """A failed sell swap leaves the token where the buy put it (spender) -- the
    position is still open, NOT newly stranded."""
    rec = _Recorder(swap=RuntimeError("no liquidity"))
    result = await _run_sell(rec, monkeypatch)
    assert result.status == "failed"
    assert result.funds_stranded is False
    assert [c[0] for c in rec.calls] == ["swap"]  # return never attempted
    row = await _last_log_row()
    assert row["status"] == "failed"
    assert row["to_address"] == ""  # nothing to recover -- token already in spender


@pytest.mark.asyncio
async def test_sell_return_failure_flags_stranded(monkeypatch):
    """Sold but the USDC return failed -> USDC stranded in the spender, needs
    recovery to aria-smart-st."""
    rec = _Recorder(ret=RuntimeError("transfer reverted"))
    result = await _run_sell(rec, monkeypatch)
    assert result.status == "failed"
    assert result.funds_stranded is True
    assert [c[0] for c in rec.calls] == ["swap", "return"]
    assert result.swap_tx_hash == "0xswap"
    row = await _last_log_row()
    assert row["to_address"] == sw.SMART_ST_ADDRESS  # recovery destination surfaced


@pytest.mark.asyncio
async def test_sell_canary_wallet_product_override(monkeypatch):
    rec = _Recorder()
    await _run_sell(rec, monkeypatch, wallet_product=sw.CANARY_WALLET_PRODUCT)
    assert (await _last_log_row())["wallet_product"] == sw.CANARY_WALLET_PRODUCT


@pytest.mark.asyncio
async def test_buy_then_sell_pairs_into_a_real_closed_position(monkeypatch):
    """End-to-end VOLET 2 across the real journal + positions pairer: a buy
    (token held) then a sell of the same token yields ONE closed position with a
    correct realized P&L -- the gap agent_wallet_positions documented is closed."""
    from aria_core import agent_wallet_positions as awp

    token = "0x" + "e" * 40
    # BUY 10$ -> 1000 tokens held in the spender
    buy_rec = _Recorder(swap={"tx_hash": "0xbuy", "amount_out": 1000.0, "amount_out_atomic": 1000_000000})
    buy = await _run_swap(buy_rec, monkeypatch, token_out=token, amount_in_usd=10.0)
    assert buy.status == "ok"
    # SELL the 1000 tokens back for 13$ USDC (a +3$ win)
    sell_rec = _Recorder(swap={"tx_hash": "0xsell", "amount_out": 13.0})
    sell = await sw.execute_smart_swing_sell(
        token_in=token, amount_in_tokens=1000.0, value_in_usd=10.0,
        balance_fn=_balance_2000_tokens, swap_fn=sell_rec.swap, return_transfer_fn=sell_rec.ret,
    )
    assert sell.status == "ok"
    result = await awp.load_positions(sw.WALLET_PRODUCT)
    assert len(result.closed) == 1
    assert result.open == []
    pos = result.closed[0]
    assert pos["contract"] == token
    assert pos["cost_usd"] == 10.0
    assert pos["proceeds_usd"] == 13.0
    assert pos["pnl_usd"] == pytest.approx(3.0)


# ── VOLET 3: precheck_swap_roundtrip (dual-quote, pure, non-executing) ────────

_PTOKEN = "0x" + "d" * 40


def _quote_provider(*, buy_out, sell_out, buy_min=None, sell_min=None,
                    unavailable_on=None, raise_on=None, as_dict=True):
    """Build a fake quote_fn mirroring cdp.evm.create_swap_quote. BUY = USDC->token,
    SELL = token->USDC. Records the calls it received."""
    from types import SimpleNamespace

    calls: list[dict] = []

    async def quote_fn(*, from_token, to_token, from_amount_atomic, network, slippage_bps):
        leg = "buy" if from_token == USDC_BASE_ADDRESS else "sell"
        calls.append({"leg": leg, "from_token": from_token, "to_token": to_token,
                      "from_amount_atomic": from_amount_atomic, "slippage_bps": slippage_bps})
        if raise_on == leg:
            raise RuntimeError(f"{leg} quote boom")
        if unavailable_on == leg:
            return {"liquidity_available": False} if as_dict else SimpleNamespace(liquidity_available=False)
        out = buy_out if leg == "buy" else sell_out
        mn = (buy_min if leg == "buy" else sell_min)
        payload = {"liquidity_available": True, "to_amount": str(out),
                   "min_to_amount": str(mn if mn is not None else out)}
        return payload if as_dict else SimpleNamespace(**payload)

    quote_fn.calls = calls
    return quote_fn


@pytest.mark.asyncio
async def test_precheck_accepts_a_cheap_round_trip():
    # buy 100$ -> 1e9 token atomic; sell 1e9 tokens -> 99 USDC atomic*... = 99$
    quote_fn = _quote_provider(buy_out=1_000_000_000, sell_out=99_000_000)
    res = await sw.precheck_swap_roundtrip(token=_PTOKEN, notional_usd=100.0, quote_fn=quote_fn)
    assert res.accepted is True
    assert res.round_trip_loss_bps == pytest.approx(100.0)  # 1%
    # forced 10% slippage + correct legs
    assert all(c["slippage_bps"] == sw.MAX_SLIPPAGE_BPS for c in quote_fn.calls)
    assert [c["leg"] for c in quote_fn.calls] == ["buy", "sell"]
    # the sell quote is for the exact token amount the buy would yield
    assert quote_fn.calls[1]["from_amount_atomic"] == 1_000_000_000


@pytest.mark.asyncio
async def test_precheck_rejects_a_lossy_round_trip():
    quote_fn = _quote_provider(buy_out=1_000_000_000, sell_out=85_000_000)  # 15% loss
    res = await sw.precheck_swap_roundtrip(token=_PTOKEN, notional_usd=100.0, quote_fn=quote_fn)
    assert res.accepted is False
    assert res.round_trip_loss_bps == pytest.approx(1500.0)
    assert "prix/liquidité" in res.reason


@pytest.mark.asyncio
async def test_precheck_reads_a_real_sdk_quote_object():
    """Proves the field readers work on the actual cdp QuoteSwapResult, not just
    a dict."""
    quote_fn = _quote_provider(buy_out=1_000_000_000, sell_out=99_000_000, as_dict=False)
    res = await sw.precheck_swap_roundtrip(token=_PTOKEN, notional_usd=100.0, quote_fn=quote_fn)
    assert res.accepted is True


@pytest.mark.asyncio
async def test_precheck_fail_closed_on_quote_error():
    quote_fn = _quote_provider(buy_out=1_000_000_000, sell_out=99_000_000, raise_on="buy")
    res = await sw.precheck_swap_roundtrip(token=_PTOKEN, notional_usd=100.0, quote_fn=quote_fn)
    assert res.accepted is False
    assert "indisponible" in res.reason


@pytest.mark.asyncio
async def test_precheck_fail_closed_on_unavailable_liquidity():
    quote_fn = _quote_provider(buy_out=1_000_000_000, sell_out=99_000_000, unavailable_on="sell")
    res = await sw.precheck_swap_roundtrip(token=_PTOKEN, notional_usd=100.0, quote_fn=quote_fn)
    assert res.accepted is False
    assert "liquidité insuffisante" in res.reason


@pytest.mark.asyncio
async def test_precheck_fail_closed_on_zero_out_amount():
    quote_fn = _quote_provider(buy_out=0, sell_out=99_000_000)
    res = await sw.precheck_swap_roundtrip(token=_PTOKEN, notional_usd=100.0, quote_fn=quote_fn)
    assert res.accepted is False
    assert "sans montant de sortie" in res.reason


@pytest.mark.asyncio
async def test_precheck_fail_closed_on_non_positive_notional():
    quote_fn = _quote_provider(buy_out=1, sell_out=1)
    res = await sw.precheck_swap_roundtrip(token=_PTOKEN, notional_usd=0.0, quote_fn=quote_fn)
    assert res.accepted is False
    assert quote_fn.calls == []  # never even fetched a quote


# ── VOLET 4: run_swing_entry_canary (executed micro buy+sell probe) ──────────


def _canary_seams(*, usdc_balance=100.0, tokens_out=300.0, tokens_out_atomic=300_000000,
                  spender_token_balance=300.0, usdc_recovered=2.9,
                  buy_swap_exc=None, sell_swap_exc=None, pull_exc=None, ret_exc=None):
    calls: list[str] = []

    async def balance_fn():
        return usdc_balance

    async def spend_pull_fn(**kw):
        calls.append("pull")
        if pull_exc:
            raise pull_exc
        return {"tx_hash": "0xpull"}

    async def buy_swap_fn(**kw):
        calls.append("buy_swap")
        if buy_swap_exc:
            raise buy_swap_exc
        return {"tx_hash": "0xbuyswap", "amount_out": tokens_out, "amount_out_atomic": tokens_out_atomic}

    async def token_balance_fn():
        return spender_token_balance

    async def sell_swap_fn(**kw):
        calls.append(("sell_swap", kw))
        if sell_swap_exc:
            raise sell_swap_exc
        return {"tx_hash": "0xsellswap", "amount_out": usdc_recovered}

    async def return_transfer_fn(**kw):
        calls.append("return")
        if ret_exc:
            raise ret_exc
        return {"tx_hash": "0xreturn"}

    seams = dict(
        balance_fn=balance_fn, spend_pull_fn=spend_pull_fn, buy_swap_fn=buy_swap_fn,
        token_balance_fn=token_balance_fn, sell_swap_fn=sell_swap_fn,
        return_transfer_fn=return_transfer_fn,
    )
    return seams, calls


_CTOKEN = "0x" + "c" * 40


@pytest.mark.asyncio
async def test_canary_clean_passes_and_stays_out_of_the_real_feed(monkeypatch):
    from aria_core import agent_wallet_positions as awp

    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    seams, calls = _canary_seams(usdc_recovered=2.9)  # 3$ -> 2.9$ = ~333 bps
    res = await sw.run_swing_entry_canary(token=_CTOKEN, canary_amount_usd=3.0, **seams)
    assert res.passed is True
    assert res.amount_spent_usd == 3.0
    assert res.amount_recovered_usd == pytest.approx(2.9)
    assert res.round_trip_loss_bps == pytest.approx(333.3, abs=1.0)
    # full order: buy(pull, buy_swap) then sell(sell_swap, return)
    assert [c if isinstance(c, str) else c[0] for c in calls] == ["pull", "buy_swap", "sell_swap", "return"]
    # the sell got the exact atomic quantity the buy reported
    sell_call = next(c for c in calls if not isinstance(c, str) and c[0] == "sell_swap")
    assert sell_call[1]["amount_in_tokens_atomic"] == 300_000000
    # both canary legs logged under the SEPARATE canary product...
    swing_positions = await awp.load_positions(sw.WALLET_PRODUCT)
    assert swing_positions.closed == [] and swing_positions.open == []  # real feed untouched
    canary_positions = await awp.load_positions(sw.CANARY_WALLET_PRODUCT)
    assert len(canary_positions.closed) == 1  # the probe round-trip is auditable on its own tag


@pytest.mark.asyncio
async def test_canary_rejects_a_high_real_round_trip_tax(monkeypatch):
    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    seams, _ = _canary_seams(usdc_recovered=2.0)  # 3$ -> 2.0$ = ~3333 bps > 2000
    res = await sw.run_swing_entry_canary(token=_CTOKEN, canary_amount_usd=3.0, **seams)
    assert res.passed is False
    assert res.round_trip_loss_bps > sw.CANARY_MAX_ROUNDTRIP_LOSS_BPS
    assert "taxe aller-retour RÉELLE" in res.reason


@pytest.mark.asyncio
async def test_canary_refuses_when_sell_fails_honeypot_signature(monkeypatch):
    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    seams, calls = _canary_seams(sell_swap_exc=RuntimeError("sell reverted"))
    res = await sw.run_swing_entry_canary(token=_CTOKEN, canary_amount_usd=3.0, **seams)
    assert res.passed is False
    assert "honeypot" in res.reason
    assert res.funds_stranded is False  # token stays in spender (position open), tiny + bounded
    assert res.sell_result is not None and res.sell_result.status == "failed"


@pytest.mark.asyncio
async def test_canary_refuses_when_buy_blocked_by_gate():
    # gate OFF (autouse fixture deletes it) -> the canary buy is blocked
    seams, calls = _canary_seams()
    res = await sw.run_swing_entry_canary(token=_CTOKEN, canary_amount_usd=3.0, **seams)
    assert res.passed is False
    assert res.buy_result is not None and res.buy_result.status == "blocked"
    assert calls == []  # no CDP call attempted at all


@pytest.mark.asyncio
async def test_canary_surfaces_stranded_funds_on_buy_swap_failure(monkeypatch):
    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    seams, _ = _canary_seams(buy_swap_exc=RuntimeError("no liquidity"))
    res = await sw.run_swing_entry_canary(token=_CTOKEN, canary_amount_usd=3.0, **seams)
    assert res.passed is False
    assert res.funds_stranded is True  # USDC pulled then swap failed -> stuck in spender
    assert res.sell_result is None     # never got to the sell


@pytest.mark.asyncio
async def test_canary_refuses_when_zero_tokens_received(monkeypatch):
    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    seams, _ = _canary_seams(tokens_out=0.0)
    res = await sw.run_swing_entry_canary(token=_CTOKEN, canary_amount_usd=3.0, **seams)
    assert res.passed is False
    assert "0 token" in res.reason
    assert res.sell_result is None
