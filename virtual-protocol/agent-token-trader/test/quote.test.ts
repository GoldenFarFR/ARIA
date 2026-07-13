import { test } from "node:test";
import assert from "node:assert/strict";
import { expectedAmountOut, computeMinOutWei, getFreshQuote } from "../src/quote.js";

test("expectedAmountOut applies constant-product formula", () => {
  const out = expectedAmountOut(1_000n, { reserveIn: 10_000n, reserveOut: 20_000n });
  assert.equal(out, (20_000n * 1_000n) / 11_000n);
});

test("expectedAmountOut rejects non-positive amountIn", () => {
  assert.throws(() => expectedAmountOut(0n, { reserveIn: 1n, reserveOut: 1n }));
});

test("computeMinOutWei never equals the raw expected output when slippage > 0", () => {
  const reserves = { reserveIn: 10_000n, reserveOut: 20_000n };
  const minOut = computeMinOutWei(1_000n, reserves, 500); // 5%
  const expected = expectedAmountOut(1_000n, reserves);
  assert.ok(minOut < expected);
  assert.equal(minOut, (expected * 9_500n) / 10_000n);
});

test("computeMinOutWei with 0 bps equals the raw expected output (never bondv5's minOutWei=1 default)", () => {
  const reserves = { reserveIn: 10_000n, reserveOut: 20_000n };
  const minOut = computeMinOutWei(1_000n, reserves, 0);
  assert.equal(minOut, expectedAmountOut(1_000n, reserves));
  assert.notEqual(minOut, 1n);
});

test("getFreshQuote rejects a slippage request above the absolute cap", async () => {
  await assert.rejects(
    () =>
      getFreshQuote(
        "0xabc",
        1_000n,
        1_500, // 15% > plafond absolu 10%
        async () => ({ reserveIn: 10_000n, reserveOut: 20_000n }),
        1_000,
      ),
    /plafond absolu/,
  );
});

test("getFreshQuote always reads reserves fresh before quoting", async () => {
  let calls = 0;
  const quote = await getFreshQuote(
    "0xabc",
    1_000n,
    500,
    async () => {
      calls += 1;
      return { reserveIn: 10_000n, reserveOut: 20_000n };
    },
    1_000,
  );
  assert.equal(calls, 1);
  assert.equal(quote.minOutWei, computeMinOutWei(1_000n, { reserveIn: 10_000n, reserveOut: 20_000n }, 500));
});
