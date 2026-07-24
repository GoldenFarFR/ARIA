// Tangem WalletConnect bridge -- minimal, local-only, one-shot.
//
// WHY THIS EXISTS (see docs/HANDOFF_COINBASE_CDP.md for the full context):
// ARIA's Smart Account migration (Model B, aria-smart-st/aria-smart-vc) needs
// the operator's Tangem hardware wallet to approve a handful of setup-time
// actions (granting the one-time Spend Permission, and every single action on
// aria-smart-vc by design). Tangem's own app supports WalletConnect v2 as a
// WALLET, but there is no maintained Python SDK for the DAPP side of the
// protocol (verified 2026-07-24: the only Python WalletConnect package found,
// pyWalletConnect, explicitly implements the wallet role only). Rather than
// hand-roll the WalletConnect v2 crypto/relay protocol in Python -- a real
// security risk on a component that will eventually touch real capital --
// this is a tiny Node.js service using the OFFICIAL, maintained
// @walletconnect/sign-client SDK (dApp role), called locally by the Python
// backend. This service NEVER holds a private key: it only relays JSON-RPC
// signing requests to the Tangem app and returns whatever comes back after
// the operator physically taps their card. All business logic (spend caps,
// gates, decisions) stays in Python -- this file does nothing but WalletConnect
// protocol plumbing.
//
// SCOPE, DELIBERATELY MINIMAL (operator's own explicit constraints, 2026-07-24):
//   - 127.0.0.1 ONLY. Never bind to 0.0.0.0, never exposed on the network.
//   - No persistent session across restarts -- one-shot setup tool, not a
//     long-running production service. Restarting this process means
//     re-pairing (scanning/pasting a fresh WalletConnect URI in the Tangem app).
//   - No automatic reconnect/retry loops -- a human is present for every
//     use of this bridge (that IS the point: a Tangem tap is a human action).
//   - Defaults to Base Sepolia (eip155:84532, testnet) -- mainnet
//     (eip155:8453) requires an explicit override, never the default, mirroring
//     every other testnet-first gate in this project (ARIA_SEPOLIA_*,
//     aria_core.x402_seller.resolve_network()).
//
// Endpoints (all local JSON over plain HTTP -- no TLS needed for 127.0.0.1):
//   POST /wc/connect            -> { uri, connectionId }
//   GET  /wc/status?connectionId=<id> -> { status: "pending"|"connected"|"error", address? }
//   POST /wc/request-signature  -> { connectionId, method, params, chainId? } -> { result }

import { createServer } from "node:http";
import { SignClient } from "@walletconnect/sign-client";

const PORT = Number(process.env.TANGEM_BRIDGE_PORT || 8787);
const PROJECT_ID = process.env.WALLETCONNECT_PROJECT_ID || "";
// Base Sepolia by default -- mainnet is an explicit, separate override, never
// silently defaulted (same doctrine as every other testnet-first gate here).
const DEFAULT_NETWORK = process.env.TANGEM_BRIDGE_NETWORK || "eip155:84532";
const ALLOWED_METHODS = ["eth_sendTransaction", "eth_signTypedData_v4", "personal_sign"];

if (!PROJECT_ID) {
  console.error(
    "FATAL: WALLETCONNECT_PROJECT_ID is not set -- fail-closed, this service " +
      "will not start without it (get one at https://cloud.reown.com)."
  );
  process.exit(1);
}

let signClient = null;
// In-memory only, by design -- a single logical "current connection" at a
// time (this is a one-shot setup tool, not a multi-tenant service). Cleared
// on process restart.
const connections = new Map(); // connectionId -> { status, topic, address, approvalPromise }

async function getSignClient() {
  if (signClient) return signClient;
  signClient = await SignClient.init({
    projectId: PROJECT_ID,
    metadata: {
      name: "ARIA Tangem Bridge (internal, local-only)",
      description: "Setup-time signing bridge -- never exposed publicly.",
      url: "http://127.0.0.1",
      icons: [],
    },
  });
  return signClient;
}

function jsonResponse(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) });
  res.end(payload);
}

async function readJsonBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (chunks.length === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return null; // caller treats null as a malformed-body error
  }
}

async function handleConnect(req, res) {
  const client = await getSignClient();
  const { uri, approval } = await client.connect({
    requiredNamespaces: {
      eip155: {
        methods: ALLOWED_METHODS,
        chains: [DEFAULT_NETWORK],
        events: ["chainChanged", "accountsChanged"],
      },
    },
  });

  const connectionId = `conn_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  const entry = { status: "pending", topic: null, address: null };
  connections.set(connectionId, entry);

  // Fire-and-forget: the operator approves asynchronously by tapping their
  // Tangem card in response to the WalletConnect prompt on their phone.
  // /wc/status polls this outcome -- never blocks the /wc/connect response.
  approval()
    .then((session) => {
      entry.status = "connected";
      entry.topic = session.topic;
      const accounts = session.namespaces?.eip155?.accounts || [];
      // Account format is "eip155:<chainId>:<address>" -- keep the address only.
      entry.address = accounts.length > 0 ? accounts[0].split(":")[2] : null;
    })
    .catch((err) => {
      entry.status = "error";
      entry.error = String(err?.message || err);
    });

  jsonResponse(res, 200, { uri, connectionId });
}

function handleStatus(req, res, url) {
  const connectionId = url.searchParams.get("connectionId");
  if (!connectionId || !connections.has(connectionId)) {
    jsonResponse(res, 404, { error: "unknown connectionId" });
    return;
  }
  const entry = connections.get(connectionId);
  jsonResponse(res, 200, {
    status: entry.status,
    address: entry.address || null,
    error: entry.error || null,
  });
}

async function handleRequestSignature(req, res) {
  const body = await readJsonBody(req);
  if (body === null) {
    jsonResponse(res, 400, { error: "malformed JSON body" });
    return;
  }
  const { connectionId, method, params, chainId } = body;
  if (!connectionId || !connections.has(connectionId)) {
    jsonResponse(res, 404, { error: "unknown connectionId" });
    return;
  }
  const entry = connections.get(connectionId);
  if (entry.status !== "connected") {
    jsonResponse(res, 409, { error: `connection not ready (status=${entry.status})` });
    return;
  }
  if (!ALLOWED_METHODS.includes(method)) {
    // Fail-closed: never relay a method outside the small allowlist this
    // bridge was designed for, even if the underlying session technically
    // negotiated more -- keeps the blast radius of a caller bug small.
    jsonResponse(res, 400, { error: `method not allowed: ${method}` });
    return;
  }

  const client = await getSignClient();
  try {
    const result = await client.request({
      topic: entry.topic,
      chainId: chainId || DEFAULT_NETWORK,
      request: { method, params },
    });
    jsonResponse(res, 200, { result });
  } catch (err) {
    // Covers both an explicit rejection (operator declined the tap) and a
    // relay/timeout failure -- the caller (Python side) treats both as "no
    // signature obtained," never assumes success on any non-200.
    jsonResponse(res, 502, { error: String(err?.message || err) });
  }
}

const server = createServer((req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  Promise.resolve()
    .then(() => {
      if (req.method === "POST" && url.pathname === "/wc/connect") return handleConnect(req, res);
      if (req.method === "GET" && url.pathname === "/wc/status") return handleStatus(req, res, url);
      if (req.method === "POST" && url.pathname === "/wc/request-signature") return handleRequestSignature(req, res);
      jsonResponse(res, 404, { error: "not found" });
    })
    .catch((err) => {
      jsonResponse(res, 500, { error: String(err?.message || err) });
    });
});

// 127.0.0.1 ONLY -- never 0.0.0.0. This is a hard invariant, not a default
// that could be silently widened by an env var.
server.listen(PORT, "127.0.0.1", () => {
  console.log(`tangem-wc-bridge listening on 127.0.0.1:${PORT} (network=${DEFAULT_NETWORK})`);
});
