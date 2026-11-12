// A posman adapter: one OKX account, fetched once.
//
// DIME Terminal's posman skill runs this as `node fetch.mjs` on the account's
// interval and stores what it prints, unchanged. POSMAN.md beside it says how
// to read each dataset. Nothing else runs it: an agent tests it with
// `posman.mjs test --adapter okx/account`.
//
// What it reads from the environment (the runner sets all of it):
//   POSMAN_CRED               the NAME of the variable holding the signing
//                             secret's placeholder: CRED_OKX_<ENV>_SECRET
//   POSMAN_PARAM_ENVIRONMENT  optional: `mainnet` (live trading) or `demo`
//                             (demo trading, the same host with
//                             `x-simulated-trading: 1`); by default the one in
//                             the credential's label (okx-<env>-secret)
//   POSMAN_OKX_API_URL        TESTS ONLY: a local stand-in for www.okx.com.
//                             Never set on a pod.
//
// What it prints: one JSON object, OKX's records as OKX sends them (the `data`
// of each answer, pages joined):
//   positions       GET /api/v5/account/positions (SWAP, FUTURES, OPTION, MARGIN)
//   open_orders     GET /api/v5/trade/orders-pending, every page
//   algo_orders     GET /api/v5/trade/orders-algo-pending: stop-loss and
//                   take-profit (`conditional`, `oco`), `trigger` and trailing
//                   (`move_order_stop`) orders, every page
//   balances        GET /api/v5/account/balance, its `details` (one per currency)
//   account         the same answer's account-level fields (`details` left out)
//   account_config  GET /api/v5/account/config (account level, position mode)
// A dataset that fails is named in `_errors` instead, so the others are kept.
//
// And the same records as posman's rows (`_normalized`: positions,
// open_orders, balances; posman's SCHEMA.md), which is what the agent and the
// terminal read. Public reads fill them in: GET /api/v5/public/instruments
// (each contract's size, currencies, expiry and strike), funding-rate (the
// perpetuals' funding) and opt-summary (the options' greeks and IV). How each
// field is mapped is in POSMAN.md, "posman's rows".
//
// THE CREDENTIAL. OKX takes three secrets. The API key and the passphrase are
// attached by the credential proxy (OK-ACCESS-KEY, OK-ACCESS-PASSPHRASE): this
// sends neither. The secret key signs: each private request carries
// OK-ACCESS-TIMESTAMP (ISO 8601 with milliseconds), the placeholder in
// OK-ACCESS-SIGN, and in X-Dime-Sign-<label> the base64 of the bytes to sign,
// `timestamp + method + requestPath + body` with the query string in
// requestPath (the okx-api skill's "The signed string"). The proxy signs them
// with the secret, puts the signature in place of the placeholder, adds the
// other two headers and drops X-Dime-Sign-*.
//
// Exit 3: OKX or the proxy refused the credential (HTTP 401 or 403, or one of
// OKX's credential codes in a 200: see AUTH_CODES). Exit 2: the source is set
// up wrong (no credential, an environment it cannot tell). Any other failure,
// exit 1.

const EXIT_CONFIG = 2;
const EXIT_AUTH = 3;
const HOST = "https://www.okx.com";
const ENVIRONMENTS = ["mainnet", "demo"];
const TIMEOUT_MS = 20_000;
const PAGE = 100;
const MAX_PAGES = 10;
// OKX's codes for a credential it refuses, whatever the HTTP status (most come
// with a 401, a few in a 200): the API key frozen (50100), a key for the other
// environment (50101), the timestamp (50102, 50112), a header missing or wrong
// (50103-50107, 50111), an unknown exchange (50108-50109), an IP not on the
// key's list (50110, 50121, 50035), the signature (50113), the authorization
// (50114), the key gone (50119) or without the permission (50120, 50030).
const AUTH_CODES = new Set([
  "50030", "50035", "50100", "50101", "50102", "50103", "50104", "50105", "50106", "50107",
  "50108", "50109", "50110", "50111", "50112", "50113", "50114", "50119", "50120", "50121",
]);
// The algo order kinds that rest until they trigger. OKX takes one per request,
// bar conditional and oco together.
const ALGO_KINDS = ["conditional,oco", "trigger", "move_order_stop"];

class Refused extends Error {}

function fail(message, code = 1) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

// ---- the credential and the environment ----

const credName = process.env.POSMAN_CRED || "";
if (!credName) fail("no credential: track this account with the OKX secret key (--cred CRED_OKX_..._SECRET)", EXIT_CONFIG);
const placeholder = process.env[credName] || "";
if (!placeholder) fail(`${credName} is not among the agent's credentials`, EXIT_CONFIG);
const label = credName.replace(/^CRED_/, "").toLowerCase().replaceAll("_", "-");
const signHeader = `X-Dime-Sign-${label}`;

const labelParts = label.split("-");
const environment =
  process.env.POSMAN_PARAM_ENVIRONMENT || (labelParts.length >= 3 && labelParts[0] === "okx" ? labelParts[1] : "");
if (!ENVIRONMENTS.includes(environment)) {
  fail(
    `cannot tell the OKX environment from the credential ${JSON.stringify(label)}: add --param ENVIRONMENT=<${ENVIRONMENTS.join("|")}>`,
    EXIT_CONFIG,
  );
}
const demo = environment === "demo";
const base = (process.env.POSMAN_OKX_API_URL || HOST).replace(/\/+$/, "");

// ---- requests ----

// The query as OKX's own SDK writes it, values unencoded (`ordType=conditional,oco`):
// requestPath is signed byte for byte, and every value here is an id, a number or
// a name OKX defines.
const SAFE_VALUE = /^[A-Za-z0-9_.,-]+$/;
function queryString(query) {
  return Object.entries(query)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => {
      if (!SAFE_VALUE.test(String(v))) throw new Error(`query ${k}=${JSON.stringify(v)} would need encoding`);
      return `${k}=${v}`;
    })
    .join("&");
}

async function call(path, query, { signed }) {
  const qs = queryString(query);
  const requestPath = qs ? `${path}?${qs}` : path;
  const headers = { Accept: "application/json" };
  if (demo) headers["x-simulated-trading"] = "1";
  if (signed) {
    // One string, used twice: OKX recomputes over the header it receives.
    const timestamp = new Date().toISOString();
    headers["OK-ACCESS-TIMESTAMP"] = timestamp;
    headers["OK-ACCESS-SIGN"] = placeholder;
    headers[signHeader] = Buffer.from(`${timestamp}GET${requestPath}`).toString("base64");
  }
  let res;
  try {
    res = await fetch(`${base}${requestPath}`, { headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (err) {
    throw new Error(`GET ${path}: ${err.cause?.message ?? err.message}`);
  }
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    // a proxy's or a gateway's plain-text answer
  }
  const code = body && typeof body === "object" ? String(body.code ?? "") : "";
  const msg = (body && typeof body === "object" && body.msg) || text.slice(0, 300).trim();
  if (res.status === 401 || res.status === 403 || AUTH_CODES.has(code)) {
    const hint =
      code === "50101"
        ? ` (a ${demo ? "live" : "demo-trading"} key? set --param ENVIRONMENT=${demo ? "mainnet" : "demo"})`
        : "";
    throw new Refused(`GET ${path}: HTTP ${res.status}${code ? ` code ${code}` : ""}: ${msg}${hint}`);
  }
  if (!res.ok) throw new Error(`GET ${path}: HTTP ${res.status}${code ? ` code ${code}` : ""}: ${msg}`);
  if (!body || code !== "0" || !Array.isArray(body.data)) {
    throw new Error(`GET ${path}: OKX answered code ${code || "?"}: ${msg || "no data"}`);
  }
  return body.data;
}

/** Every page of a list OKX pages by id (`after` the last one), at most MAX_PAGES. */
async function pages(path, query, idField) {
  const out = [];
  let after = null;
  for (let i = 0; i < MAX_PAGES; i++) {
    const page = await call(path, { ...query, limit: String(PAGE), after }, { signed: true });
    out.push(...page);
    if (page.length < PAGE) return out;
    after = page[page.length - 1]?.[idField];
    if (!after) return out;
  }
  process.stderr.write(`${path}: stopped after ${MAX_PAGES} pages\n`);
  return out;
}

// ---- decimals: OKX's numbers are strings, and so are posman's ----

const DECIMAL_RE = /^-?\d+(\.\d+)?$/;

function dec(v) {
  if (v == null) return null;
  const s = String(v).trim();
  if (!DECIMAL_RE.test(s)) return null;
  const neg = s.startsWith("-");
  const [i, f = ""] = s.replace(/^-/, "").split(".");
  const n = BigInt(i + f) * (neg ? -1n : 1n);
  return { n, scale: f.length };
}

function str(d) {
  if (d == null) return null;
  const neg = d.n < 0n;
  let digits = (neg ? -d.n : d.n).toString().padStart(d.scale + 1, "0");
  let out = d.scale ? `${digits.slice(0, -d.scale)}.${digits.slice(-d.scale)}` : digits;
  if (out.includes(".")) out = out.replace(/0+$/, "").replace(/\.$/, "");
  if (out === "0") return "0";
  return neg ? `-${out}` : out;
}

const mul = (a, b) => (a == null || b == null ? null : { n: a.n * b.n, scale: a.scale + b.scale });

function round(d, places) {
  if (d == null || d.scale <= places) return d;
  const cut = 10n ** BigInt(d.scale - places);
  let q = d.n / cut;
  const r = d.n % cut;
  // Half away from zero.
  if ((r < 0n ? -r : r) * 2n >= cut) q += d.n < 0n ? -1n : 1n;
  return { n: q, scale: places };
}

function div(a, b, places) {
  if (a == null || b == null || b.n === 0n) return null;
  // a/b = (a.n / 10^a.scale) / (b.n / 10^b.scale), to `places` decimals plus a guard digit.
  const shift = BigInt(places + 1 + b.scale - a.scale);
  const num = shift >= 0n ? a.n * 10n ** shift : a.n / 10n ** -shift;
  return round({ n: num / b.n, scale: places + 1 }, places);
}

const neg = (d) => (d == null ? null : { n: -d.n, scale: d.scale });
const abs = (d) => (d == null ? null : { n: d.n < 0n ? -d.n : d.n, scale: d.scale });
const sign = (d) => (d == null ? 0 : d.n > 0n ? 1 : d.n < 0n ? -1 : 0);
const sub = (a, b) => {
  if (a == null || b == null) return null;
  const scale = Math.max(a.scale, b.scale);
  return { n: a.n * 10n ** BigInt(scale - a.scale) - b.n * 10n ** BigInt(scale - b.scale), scale };
};
const ZERO = { n: 0n, scale: 0 };

/** A price OKX gives, or null for none (`""`, or `-1`: "at market"). */
function price(v) {
  const d = dec(v);
  return d == null || sign(d) <= 0 ? null : d;
}

function iso(ms) {
  const n = Number(ms);
  return ms != null && ms !== "" && Number.isFinite(n) && n > 0 ? new Date(n).toISOString() : null;
}

// ---- fetch ----

const out = {};
const errors = {};

async function dataset(name, load) {
  try {
    out[name] = await load();
  } catch (err) {
    if (err instanceof Refused) throw err;
    errors[name] = err.message;
  }
}

try {
  await Promise.all([
    dataset("positions", () => call("/api/v5/account/positions", {}, { signed: true })),
    dataset("open_orders", () => pages("/api/v5/trade/orders-pending", {}, "ordId")),
    dataset("algo_orders", async () => {
      const lists = await Promise.all(
        ALGO_KINDS.map((ordType) => pages("/api/v5/trade/orders-algo-pending", { ordType }, "algoId")),
      );
      return lists.flat();
    }),
    dataset("balances", async () => {
      const [acct] = await call("/api/v5/account/balance", {}, { signed: true });
      if (!acct || typeof acct !== "object") throw new Error("GET /api/v5/account/balance: no account in the answer");
      const { details, ...summary } = acct;
      out.account = summary;
      return Array.isArray(details) ? details : [];
    }),
    dataset("account_config", async () => (await call("/api/v5/account/config", {}, { signed: true }))[0] ?? {}),
  ]);
} catch (err) {
  if (err instanceof Refused) fail(err.message, EXIT_AUTH);
  throw err;
}
if ("balances" in errors) errors.account = errors.balances;

// ---- the public reads posman's rows need ----

// An instrument family from an id: BTC-USDT-SWAP -> BTC-USDT,
// BTC-USD-261127 -> BTC-USD, BTC-USD-261225-74000-C -> BTC-USD.
function family(instType, instId) {
  const parts = instId.split("-");
  if (instType === "SWAP") return parts.slice(0, -1).join("-");
  if (instType === "FUTURES") return parts.slice(0, -1).join("-");
  if (instType === "OPTION") return parts.slice(0, -3).join("-");
  return null;
}

const CONTRACTS = ["SWAP", "FUTURES", "OPTION"];
const records = [...(out.positions ?? []), ...(out.open_orders ?? []), ...(out.algo_orders ?? [])];
const families = new Map(); // "SWAP BTC-USDT" -> [instType, family]
for (const r of records) {
  if (!CONTRACTS.includes(r?.instType) || typeof r.instId !== "string") continue;
  const f = family(r.instType, r.instId);
  if (f) families.set(`${r.instType} ${f}`, [r.instType, f]);
}
const optionFamilies = [...families.values()].filter(([t]) => t === "OPTION").map(([, f]) => f);
const hasSwapPosition = (out.positions ?? []).some((p) => p?.instType === "SWAP");

const instruments = new Map(); // instId -> instrument
const funding = new Map(); // instId -> funding-rate record
const summaries = new Map(); // instId -> opt-summary record
const auxErrors = {};

await Promise.all([
  ...[...families.values()].map(async ([instType, instFamily]) => {
    try {
      for (const i of await call("/api/v5/public/instruments", { instType, instFamily }, { signed: false })) {
        instruments.set(i.instId, i);
      }
    } catch (err) {
      auxErrors.instruments = err.message;
    }
  }),
  hasSwapPosition &&
    (async () => {
      try {
        for (const f of await call("/api/v5/public/funding-rate", { instId: "ANY" }, { signed: false })) {
          funding.set(f.instId, f);
        }
      } catch (err) {
        auxErrors.funding_rates = err.message;
      }
    })(),
  ...optionFamilies.map(async (instFamily) => {
    try {
      for (const s of await call("/api/v5/public/opt-summary", { instFamily }, { signed: false })) {
        summaries.set(s.instId, s);
      }
    } catch (err) {
      auxErrors.option_summary = err.message;
    }
  }),
]);
Object.assign(errors, auxErrors);

// ---- posman's rows ----

const KINDS = { SWAP: "perp", FUTURES: "future", OPTION: "option", MARGIN: "spot", SPOT: "spot" };
const skipped = [];

/** The instrument fields of a row, and how to turn its quantities into the underlying. */
function instrumentOf(r) {
  const kind = KINDS[r.instType];
  if (!kind) return null; // EVENTS and anything newer: raw only
  if (kind === "spot") {
    const [baseCcy, quoteCcy] = r.instId.split("-");
    if (!baseCcy || !quoteCcy) return null;
    return { row: { symbol: r.instId, kind, underlying: baseCcy, quote_asset: quoteCcy, contract: null }, spot: true };
  }
  const inst = instruments.get(r.instId);
  if (!inst) return null;
  const [baseCcy, quoteCcy] = String(inst.uly || family(r.instType, r.instId) || "").split("-");
  if (!baseCcy) return null;
  const option = kind === "option";
  const contract = inst.ctType === "linear" || inst.ctType === "inverse" ? inst.ctType : null;
  const row = {
    symbol: r.instId,
    kind,
    underlying: baseCcy,
    // An option's strike and greeks are in USD; its premium is in the coin it
    // settles in, which is why its prices are left null (POSMAN.md).
    quote_asset: option ? "USD" : quoteCcy || null,
    settle_asset: inst.settleCcy || null,
    contract,
    expiry: kind === "future" || option ? iso(inst.expTime) : null,
    strike: option ? str(dec(inst.stk)) : null,
    option_type: option ? { C: "call", P: "put" }[inst.optType] ?? null : null,
  };
  const ctSize = mul(dec(inst.ctVal), dec(inst.ctMult));
  if (ctSize == null) return null;
  // One contract is ctVal x ctMult of ctValCcy: the underlying itself (linear
  // contracts, options), or the quote (inverse contracts: USD face value).
  const per = inst.ctValCcy === baseCcy ? "base" : inst.ctValCcy === quoteCcy ? "quote" : null;
  if (!per) return null;
  return { row, ctSize, per };
}

/** Contracts as units of the underlying; an inverse contract's USD face value over `at`. */
function underlyingOf(inst, contracts, at) {
  if (contracts == null) return null;
  if (inst.spot) return contracts;
  const face = mul(contracts, inst.ctSize);
  return inst.per === "base" ? face : div(face, at, 12);
}

function greeks(row, size, r) {
  if (row.kind !== "option") return { delta: str(size), gamma: "0", vega: "0", theta: "0", iv: null };
  const s = summaries.get(r.instId);
  if (!s) return { delta: null, gamma: null, vega: null, theta: null, iv: null };
  // OKX's BS greeks are per 1 unit of the underlying, in USD (opt-summary).
  const g = (field) => str(round(mul(dec(s[field]), size), 18));
  return { delta: g("deltaBS"), gamma: g("gammaBS"), vega: g("vegaBS"), theta: g("thetaBS"), iv: str(dec(s.markVol)) };
}

function fundingPerHour(instId) {
  const f = funding.get(instId);
  if (!f) return null;
  const hours = (Number(f.nextFundingTime) - Number(f.fundingTime)) / 3_600_000;
  if (!Number.isInteger(hours) || hours <= 0) return null;
  return str(div(dec(f.fundingRate), { n: BigInt(hours), scale: 0 }, 18));
}

// A portfolio-margin account (acctLv 4) reports its cross positions as `cross`.
const portfolio = String(out.account_config?.acctLv ?? "") === "4";
function marginMode(mgnMode) {
  if (mgnMode === "cross") return portfolio ? "portfolio" : "cross";
  return mgnMode === "isolated" ? "isolated" : null;
}

function positionRow(p) {
  const inst = instrumentOf(p);
  if (!inst) return null;
  const pos = dec(p.pos);
  if (sign(pos) === 0) return null; // an isolated position funded but empty
  let size;
  if (inst.spot) {
    // MARGIN: pos is always positive; posCcy the base currency is a long, the
    // quote currency a short, whose size is the base borrowed (liab).
    const [baseCcy] = p.instId.split("-");
    if (p.posCcy === baseCcy) size = abs(pos);
    else if (p.liabCcy === baseCcy && sign(dec(p.liab)) !== 0) size = neg(abs(dec(p.liab)));
    else return null;
  } else {
    const contracts = p.posSide === "short" ? neg(abs(pos)) : p.posSide === "long" ? abs(pos) : pos;
    size = underlyingOf(inst, contracts, price(p.markPx));
  }
  if (size == null || sign(size) === 0) return null;
  const option = inst.row.kind === "option";
  const unrealized = str(dec(p.upl));
  const fundingPnl = inst.row.kind === "perp" || inst.row.kind === "future" ? str(dec(p.fundingFee)) : null;
  return {
    ...inst.row,
    side: sign(size) > 0 ? "long" : "short",
    size: str(size),
    entry_price: option ? null : str(price(p.avgPx)),
    mark_price: option ? null : str(price(p.markPx)),
    liquidation_price: str(price(p.liqPx)),
    unrealized_pnl: unrealized,
    funding_pnl: fundingPnl,
    pnl_asset: unrealized != null || fundingPnl != null ? p.ccy || null : null,
    margin_mode: marginMode(p.mgnMode),
    leverage: str(price(p.lever)),
    funding_rate: inst.row.kind === "perp" ? fundingPerHour(p.instId) : null,
    ...greeks(inst.row, size, p),
    updated_at: iso(p.uTime),
  };
}

// ordType -> [posman type, time in force]
const ORDER_TYPES = {
  limit: ["limit", "gtc"],
  post_only: ["limit", "post_only"],
  fok: ["limit", "fok"],
  ioc: ["limit", "ioc"],
  op_fok: ["limit", "fok"],
  mmp: ["limit", "gtc"],
  mmp_and_post_only: ["limit", "post_only"],
  rpi: ["limit", "other"],
  market: ["market", null],
  optimal_limit_ioc: ["market", "ioc"],
};

function quantities(inst, o, sz, filled, at) {
  // A spot market order may be sized in the quote currency (tgtCcy).
  if (inst.spot && o.tgtCcy === "quote_ccy") return null;
  const size = underlyingOf(inst, dec(sz), at);
  if (size == null || sign(size) <= 0) return null;
  const filledC = dec(filled);
  return {
    size: str(size),
    filled_size: filledC == null ? null : str(underlyingOf(inst, filledC, at)),
    remaining_size: filledC == null ? null : str(underlyingOf(inst, sub(dec(sz), filledC), at)),
  };
}

function orderRow(o) {
  const inst = instrumentOf(o);
  if (!inst) return null;
  const option = inst.row.kind === "option";
  // An option's px is in the coin; pxUsd is OKX's own USD price of it.
  const px = option ? price(o.pxUsd) : price(o.px);
  const q = quantities(inst, o, o.sz, o.accFillSz, price(o.px));
  if (!q) return null;
  const [type, tif] = ORDER_TYPES[o.ordType] ?? ["other", "other"];
  return {
    ...inst.row,
    order_id: String(o.ordId),
    client_order_id: o.clOrdId || null,
    side: o.side,
    type,
    venue_type: o.ordType,
    status: o.state === "partially_filled" ? "partially_filled" : "open",
    venue_status: o.state || null,
    price: str(px),
    trigger_price: null,
    ...q,
    time_in_force: tif,
    reduce_only: o.reduceOnly === true || o.reduceOnly === "true",
    created_at: iso(o.cTime),
    expires_at: null,
  };
}

function algoRow(a) {
  const inst = instrumentOf(a);
  if (!inst) return null;
  let type = "other";
  let trigger = null;
  let px = null;
  const sl = price(a.slTriggerPx);
  const tp = price(a.tpTriggerPx);
  if (a.ordType === "conditional" && sl && !tp) {
    trigger = sl;
    px = price(a.slOrdPx);
    type = px ? "stop_limit" : "stop_market";
  } else if (a.ordType === "conditional" && tp && !sl) {
    trigger = tp;
    px = price(a.tpOrdPx);
    type = px ? "take_profit_limit" : "take_profit_market";
  } else if (a.ordType === "trigger") {
    trigger = price(a.triggerPx);
    px = price(a.ordPx);
  } else if (a.ordType === "move_order_stop") {
    type = "trailing_stop";
    trigger = price(a.moveTriggerPx);
  }
  // A TP/SL on a whole position (closeFraction) has no size of its own.
  const q = quantities(inst, a, a.sz, null, px ?? trigger);
  if (!q) return null;
  return {
    ...inst.row,
    order_id: String(a.algoId),
    client_order_id: a.algoClOrdId || a.clOrdId || null,
    side: a.side,
    type,
    venue_type: a.ordType,
    status: "untriggered",
    venue_status: a.state || null,
    price: inst.row.kind === "option" ? null : str(px),
    trigger_price: str(trigger),
    size: q.size,
    filled_size: null,
    remaining_size: null,
    time_in_force: null,
    reduce_only: a.reduceOnly === true || a.reduceOnly === "true",
    created_at: iso(a.cTime),
    expires_at: null,
  };
}

function rows(list, map, what) {
  const outRows = [];
  for (const r of list) {
    const row = map(r);
    if (row) outRows.push(row);
    else skipped.push(`${what} ${r?.instId ?? "?"} ${r?.ordId ?? r?.algoId ?? ""}`.trim());
  }
  return outRows;
}

const normalized = {};
// Without an instrument family, positions and orders on it cannot be sized: they
// are not mapped this fetch (posman keeps their last rows).
const instrumentsOk = !("instruments" in errors);
if ("positions" in out && instrumentsOk) normalized.positions = rows(out.positions, positionRow, "position");
if ("open_orders" in out && "algo_orders" in out && instrumentsOk) {
  normalized.open_orders = [
    ...rows(out.open_orders, orderRow, "order"),
    ...rows(out.algo_orders, algoRow, "algo order"),
  ];
}
if ("balances" in out) {
  normalized.balances = out.balances
    .map((b) => {
      const total = str(dec(b.cashBal)) ?? str(dec(b.eq));
      return b.ccy && total != null ? { asset: b.ccy, total, updated_at: iso(b.uTime) } : null;
    })
    .filter(Boolean);
}
if (skipped.length) process.stderr.write(`not mapped to posman's rows (raw only): ${skipped.join(", ")}\n`);

out._normalized = normalized;
if (Object.keys(errors).length) out._errors = errors;
process.stdout.write(`${JSON.stringify(out)}\n`);
