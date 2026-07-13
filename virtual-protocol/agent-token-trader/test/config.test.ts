import { test } from "node:test";
import assert from "node:assert/strict";
import { isTraderEnabled } from "../src/config.js";

test("isTraderEnabled re-reads the environment on every call (real kill-switch)", () => {
  process.env.ARIA_AGENT_TOKEN_TRADER_ENABLED = "true";
  assert.equal(isTraderEnabled(), true);

  process.env.ARIA_AGENT_TOKEN_TRADER_ENABLED = "false";
  assert.equal(isTraderEnabled(), false);

  delete process.env.ARIA_AGENT_TOKEN_TRADER_ENABLED;
  assert.equal(isTraderEnabled(), false); // fail-closed par défaut
});
