// A posman adapter: one Bybit unified account, fetched once.
//
// DIME Terminal's posman skill runs this as `node fetch.mjs` on the
// account's interval and stores what it prints. POSMAN.md beside it says how
// to read each dataset. Nothing else runs it: an agent tests it with
// `posman.mjs test --adapter bybit/account`.
//
// What it reads from the environment (the runner sets all of it):
//   POSMAN_CRED              the NAME of the variable holding the credential's
//                            placeholder: CRED_BYBIT_<ENV>_<SLUG>
//   <POSMAN_CRED>_META       the credential's public details: `api_key`
//   POSMAN_PARAM_ENVIRONMENT optional: mainnet, testnet or demo; by default
//                            the one in the credential's label (bybit-<env>-<slug>)
//   POSMAN_BYBIT_API_URL     TESTS ONLY: a local stand-in for Bybit's REST API.
//                            Never set on a pod.
//
// What it prints: one JSON object, Bybit's records as Bybit sends them, each
// with the v5 `category` it was listed under added:
//   positions     GET /v5/position/list (linear USDT, linear USDC, inverse,
//                 option), every page, empty positions left out
//   open_orders   GET /v5/order/realtime (the same, plus spot), every page
//   balances      GET /v5/account/wallet-balance?accountType=UNIFIED, `coin`
//   account       the same answer's account-wide figures (without `coin`)
//   account_info  GET /v5/account/info (the margin mode)
// A dataset that fails is named in `_errors` instead, so the others are kept.
//
// And the same records as posman's rows (`_normalized`: positions,
// open_orders, balances; posman's SCHEMA.md), which is what the agent and the
// terminal read. Two public reads fill them in, for the symbols held only:
// GET /v5/market/instruments-info (kind, coins, expiry, option type, funding
// interval) and GET /v5/market/tickers (funding rate, IV, per-unit greeks).
// How each field is mapped is in POSMAN.md, "posman's rows".
//
// SIGNING, as the bybit app's MCP server signs (app.yaml). Bybit signs
// `timestamp + api key + recv window + query string` with the secret. The
// request carries those bytes, base64, in `X-Dime-Sign-<label>` and the
// placeholder in X-BAPI-SIGN; the credential proxy computes the HMAC, puts it
// in X-BAPI-SIGN and strips the X-Dime-Sign header. The API key is a public
// detail (`api_key`) and goes in X-BAPI-API-KEY as it is. The query string is
// built once, by hand, and both signed and sent: Bybit's cursors come
// percent-encoded already, and encoding them again would sign one string and
// send another.
//
// Exit 3: Bybit or the proxy refused the credential (HTTP 401, the proxy's
// 403, or a retCode that says so). Exit 2: the source is set up wrong (no
// credential, no API key, an environment it cannot tell). Any other failure,
// exit 1.

import { Buffer } from "node:buffer";
import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

const EXIT_CONFIG = 2;
const EXIT_AUTH = 3;

// The app's environments (app.yaml), by the id a credential's label carries.
export const HOSTS = {
  mainnet: "api.bybit.com",
  testnet: "api-testnet.bybit.com",
  demo: "api-demo.bybit.com",
};
const RECV_WINDOW = "5000";
const MAX_PAGES = 10;
const TIMEOUT_MS = 20_000;
const PUBLIC_CONCURRENCY = 8;

// retCodes that mean the credential, not the request (Bybit's error table):
// 10003 key invalid or for another environment, 10004 signature (the secret
// is not the key's), 10005 permission denied, 10007 authentication failed,
// 10010 the key's bound IPs exclude this one, 33004 key expired.
export const AUTH_CODES = new Set([10003, 10004, 10005, 10007, 10010, 33004]);

// What each v5 category is asked for. linear needs a settle coin (or a symbol);
// inverse and option list everything without one.
const POSITION_QUERIES = [
  { category: "linear", settleCoin: "USDT" },
  { category: "linear", settleCoin: "USDC" },
  { category: "inverse" },
  { category: "option" },
];
const ORDER_QUERIES = [...POSITION_QUERIES, { category: "spot" }];

export class Refused extends Error {}

function fail(message, code = 1) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

const snip = (text) => String(text).trim().replace(/\s+/g, " ").slice(0, 300);

// ---- the credential and the environment ----

export function credential(env) {
  const name = env.POSMAN_CRED || "";
  if (!name) fail("no credential: track this account with a Bybit credential (--cred CRED_BYBIT_...)", EXIT_CONFIG);
  const placeholder = env[name] || "";
  if (!placeholder) fail(`${name} is not among the agent's credentials`, EXIT_CONFIG);
  let meta = {};
  try {
    meta = JSON.parse(env[`${name}_META`] || "{}") ?? {};
  } catch {
    meta = {};
  }
  const label = name.replace(/^CRED_/, "").toLowerCase().replaceAll("_", "-");
  const apiKey = typeof meta.api_key === "string" ? meta.api_key.trim() : "";
  if (!apiKey) {
    fail(
      `${label} has no API key in its details: Bybit signs the key with every request, so re-enrol the credential with its API key`,
      EXIT_CONFIG,
    );
  }
  return { name, placeholder, apiKey, label };
}

export function environment(env, label) {
  const parts = label.split("-");
  const wanted = env.POSMAN_PARAM_ENVIRONMENT || (parts.length >= 3 && parts[0] === "bybit" ? parts[1] : "");
  if (!(wanted in HOSTS)) {
    fail(
      `cannot tell the Bybit environment from the credential ${JSON.stringify(label)}: add --param ENVIRONMENT=<${Object.keys(HOSTS).join("|")}>`,
      EXIT_CONFIG,
    );
  }
  // Tests only: a local stand-in for Bybit. Never set on a pod.
  const base = env.POSMAN_BYBIT_API_URL
    ? env.POSMAN_BYBIT_API_URL.replace(/\/+$/, "")
    : `https://${HOSTS[wanted]}`;
  return { id: wanted, base };
}

// ---- requests ----

/** `k=v&...` in the order given, values as they are: the bytes signed are the bytes sent. */
export function queryString(params) {
  return Object.entries(params)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${k}=${v}`)
    .join("&");
}

/** The headers a signed GET carries, and the bytes the proxy signs. */
export function signedHeaders(ctx, qs, now = Date.now()) {
  const timestamp = String(now);
  const payload = timestamp + ctx.apiKey + RECV_WINDOW + qs;
  return {
    "X-BAPI-API-KEY": ctx.apiKey,
    "X-BAPI-TIMESTAMP": timestamp,
    "X-BAPI-RECV-WINDOW": RECV_WINDOW,
    "X-BAPI-SIGN": ctx.placeholder,
    [`X-Dime-Sign-${ctx.label}`]: Buffer.from(payload).toString("base64"),
  };
}

async function get(ctx, path, params, signed) {
  const qs = queryString(params);
  const url = `${ctx.base}${path}${qs ? `?${qs}` : ""}`;
  const headers = { Accept: "application/json", ...(signed ? signedHeaders(ctx, qs) : {}) };
  let res;
  try {
    res = await fetch(url, { headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (err) {
    throw new Error(`GET ${path}: ${err.cause?.message ?? err.message}`);
  }
  const text = await res.text();
  if (res.status === 401) {
    throw new Refused(`GET ${path}: HTTP 401${text.trim() ? `: ${snip(text)}` : " with an empty body"}: Bybit refused the API key`);
  }
  if (res.status === 403) {
    // Bybit's own 403 is a rate limit or a blocked region (CloudFront), not the
    // credential; the proxy's is plain text saying why it refused.
    if (/cloudfront|country|region|rate limit/i.test(text)) throw new Error(`GET ${path}: HTTP 403: ${snip(text)}`);
    throw new Refused(`GET ${path}: HTTP 403: ${snip(text) || "refused"}`);
  }
  if (!res.ok) throw new Error(`GET ${path}: HTTP ${res.status}${text.trim() ? `: ${snip(text)}` : ""}`);
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    throw new Error(`GET ${path}: an answer that is not JSON: ${snip(text)}`);
  }
  if (body?.retCode !== 0) {
    const message = `GET ${path} (${qs}): retCode ${body?.retCode}: ${body?.retMsg ?? ""}`.trim();
    if (AUTH_CODES.has(body?.retCode)) throw new Refused(message);
    throw new Error(message);
  }
  return body.result ?? {};
}

/** Every page of a signed list, each record with its category added. */
async function pages(ctx, path, params, limit) {
  const list = [];
  let cursor = "";
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await get(ctx, path, { ...params, limit, cursor }, true);
    const records = Array.isArray(result.list) ? result.list : [];
    for (const r of records) list.push({ category: params.category, ...r });
    const next = typeof result.nextPageCursor === "string" ? result.nextPageCursor : "";
    if (!next || next === cursor || records.length === 0) return list;
    cursor = next;
  }
  throw new Error(`GET ${path} (${queryString(params)}): more than ${MAX_PAGES} pages`);
}

async function each(queries, read) {
  const out = [];
  for (const q of queries) out.push(...(await read(q)));
  return out;
}

/** Run `fn` over `items`, at most `n` at once. */
async function pool(items, n, fn) {
  const results = new Array(items.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(n, items.length) }, async () => {
      while (next < items.length) {
        const i = next++;
        results[i] = await fn(items[i]);
      }
    }),
  );
  return results;
}

// ---- decimals: Bybit's strings, kept exact ----

const DEC_RE = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;

/** A decimal as {n, s} (value n·10^-s), or null for anything that is not one. */
export function parseDec(v) {
  if (v == null) return null;
  const t = String(v).trim();
  const m = DEC_RE.exec(t);
  if (!m || (m[2] === "" && (m[3] ?? "") === "")) return null;
  const frac = m[3] ?? "";
  let n = BigInt(`${m[2] || "0"}${frac}` || "0");
  let s = frac.length - Number(m[4] ?? 0);
  if (s < 0) {
    n *= 10n ** BigInt(-s);
    s = 0;
  }
  return { n: m[1] === "-" ? -n : n, s };
}

export function fmt(d) {
  if (d == null) return null;
  let { n, s } = d;
  while (s > 0 && n % 10n === 0n) {
    n /= 10n;
    s -= 1;
  }
  const neg = n < 0n;
  let digits = (neg ? -n : n).toString();
  if (s > 0) {
    digits = digits.padStart(s + 1, "0");
    digits = `${digits.slice(0, -s)}.${digits.slice(-s)}`;
  }
  return n === 0n ? "0" : `${neg ? "-" : ""}${digits}`;
}

const dec = (v) => fmt(parseDec(v));
const isZero = (d) => d == null || d.n === 0n;
const mul = (a, b) => (a == null || b == null ? null : { n: a.n * b.n, s: a.s + b.s });

/** a / b, rounded half away from zero to `places`. */
function div(a, b, places = 12) {
  if (a == null || b == null || b.n === 0n) return null;
  const num = a.n * 10n ** BigInt(places + b.s);
  const den = b.n * 10n ** BigInt(a.s);
  let q = num / den;
  const r = num % den;
  if (r !== 0n && 2n * (r < 0n ? -r : r) >= (den < 0n ? -den : den)) q += (num < 0n) === (den < 0n) ? 1n : -1n;
  return { n: q, s: places };
}

/** A non-zero decimal, else null: Bybit writes "0" and "" for "none". */
const nonZero = (v) => {
  const d = parseDec(v);
  return isZero(d) ? null : fmt(d);
};

/** Bybit's millisecond strings as ISO UTC; "0" and "" are null. */
export function iso(ms) {
  const t = String(ms ?? "").trim();
  if (!/^\d+$/.test(t) || /^0+$/.test(t)) return null;
  const d = new Date(Number(t));
  return Number.isFinite(d.getTime()) ? d.toISOString() : null;
}

// ---- the instruments the account holds ----

/** posman's instrument fields from Bybit's instrument record. */
export function instrumentFields(category, symbol, inst) {
  if (!inst) return guessInstrument(category, symbol);
  const base = {
    symbol,
    underlying: inst.baseCoin || null,
    quote_asset: inst.quoteCoin || null,
    settle_asset: inst.settleCoin || null,
  };
  if (category === "spot") return { ...base, kind: "spot", settle_asset: null, contract: null };
  if (category === "option") {
    const optionType = inst.optionsType === "Call" ? "call" : inst.optionsType === "Put" ? "put" : null;
    const strike = dec(symbol.split("-")[2]);
    const expiry = iso(inst.deliveryTime);
    if (!optionType || !strike) return { ...base, kind: "other", contract: "linear" };
    return { ...base, kind: "option", contract: "linear", expiry, strike, option_type: optionType };
  }
  const contract = category === "inverse" ? "inverse" : "linear";
  const type = inst.contractType || "";
  if (type.endsWith("Perpetual")) return { ...base, kind: "perp", contract, expiry: null };
  const expiry = iso(inst.deliveryTime);
  if (type.endsWith("Futures") && expiry) return { ...base, kind: "future", contract, expiry };
  return { ...base, kind: "other", contract };
}

/**
 * An instrument Bybit's instruments list did not have (delisted, settling):
 * what its symbol says, as `other` so nothing is claimed about an expiry.
 */
function guessInstrument(category, symbol) {
  const parts = symbol.split("-");
  const head = parts[0];
  const underlying =
    category === "option" || parts.length > 1 ? head : symbol.replace(/(USDT|USDC|PERP|USD)$/, "") || symbol;
  // What it settles in, as far as the symbol says: USDT or USDC for linear
  // (a PERP or a dated USDC contract is USDC), the coin itself for inverse.
  let settle = null;
  if (category === "inverse") settle = underlying;
  else if (category === "option") settle = ["USDT", "USDC"].includes(parts.at(-1)) ? parts.at(-1) : "USDC";
  else if (category === "linear") settle = /USDT/.test(symbol) ? "USDT" : "USDC";
  return {
    symbol,
    underlying,
    settle_asset: category === "spot" ? null : settle,
    kind: category === "spot" ? "spot" : "other",
    contract: category === "inverse" ? "inverse" : category === "spot" ? null : "linear",
  };
}

const instrumentKey = (category, symbol) => `${category}:${symbol}`;

/** The instruments and tickers of the symbols held, public reads, one symbol each. */
async function marketData(ctx, held, withTickers) {
  const keys = [...held.keys()];
  const query = (category, symbol) => {
    const q = { category, symbol };
    // Bybit lists BTC options unless told the base coin.
    if (category === "option") q.baseCoin = symbol.split("-")[0];
    return q;
  };
  const instruments = new Map();
  let instrumentsError = null;
  await pool(keys, PUBLIC_CONCURRENCY, async (key) => {
    const { category, symbol } = held.get(key);
    try {
      const r = await get(ctx, "/v5/market/instruments-info", query(category, symbol), false);
      const inst = (r.list ?? []).find((i) => i.symbol === symbol);
      if (inst) instruments.set(key, inst);
    } catch (err) {
      instrumentsError ??= err.message;
    }
  });
  const tickers = new Map();
  let tickersError = null;
  const wanted = keys.filter((k) => withTickers.has(k));
  await pool(wanted, PUBLIC_CONCURRENCY, async (key) => {
    const { category, symbol } = held.get(key);
    try {
      const r = await get(ctx, "/v5/market/tickers", query(category, symbol), false);
      const t = (r.list ?? []).find((x) => x.symbol === symbol);
      if (t) tickers.set(key, t);
    } catch (err) {
      tickersError ??= err.message;
    }
  });
  return { instruments, instrumentsError, tickers, tickersError };
}

// ---- posman's rows ----

const MARGIN_MODES = { REGULAR_MARGIN: "cross", ISOLATED_MARGIN: "isolated", PORTFOLIO_MARGIN: "portfolio" };

export function positionRow(p, inst, ticker, marginMode) {
  const abs = parseDec(p.size);
  if (isZero(abs) || (p.side !== "Buy" && p.side !== "Sell")) return null;
  const sign = p.side === "Buy" ? 1n : -1n;
  // An inverse contract counts USD; its value in coins is Bybit's positionValue.
  const units = p.category === "inverse" ? parseDec(p.positionValue) : abs;
  if (isZero(units)) return null;
  const size = { n: (units.n < 0n ? -units.n : units.n) * sign, s: units.s };
  const row = {
    ...instrumentFields(p.category, p.symbol, inst),
    side: sign > 0n ? "long" : "short",
    size: fmt(size),
    entry_price: nonZero(p.avgPrice),
    mark_price: nonZero(p.markPrice),
    liquidation_price: nonZero(p.liqPrice),
    // Bybit's unrealised PnL is price only: funding is realised into curRealisedPnl.
    unrealized_pnl: dec(p.unrealisedPnl),
    funding_pnl: null,
    pnl_asset: inst?.settleCoin || guessInstrument(p.category, p.symbol).settle_asset || null,
    margin_mode: MARGIN_MODES[marginMode] ?? null,
    leverage: p.category === "option" ? null : nonZero(p.leverage),
    updated_at: iso(p.updatedTime),
  };
  if (row.kind === "perp") {
    const hours = parseDec(ticker?.fundingIntervalHour) ?? div(parseDec(inst?.fundingInterval), parseDec("60"));
    row.funding_rate = ticker ? fmt(div(parseDec(ticker.fundingRate), isZero(hours) ? null : hours, 12)) : null;
  }
  if (row.kind === "option") {
    row.delta = ticker ? fmt(mul(parseDec(ticker.delta), size)) : null;
    row.gamma = ticker ? fmt(mul(parseDec(ticker.gamma), size)) : null;
    row.vega = ticker ? fmt(mul(parseDec(ticker.vega), size)) : null;
    row.theta = ticker ? fmt(mul(parseDec(ticker.theta), size)) : null;
    row.iv = ticker ? dec(ticker.markIv) : null;
  } else if (row.kind === "perp" || row.kind === "future") {
    Object.assign(row, { delta: row.size, gamma: "0", vega: "0", theta: "0" });
  }
  // A PnL needs its asset: without the instrument's settle coin, drop the PnL.
  if (row.pnl_asset == null) row.unrealized_pnl = null;
  return row;
}

const TIME_IN_FORCE = { GTC: "gtc", IOC: "ioc", FOK: "fok", PostOnly: "post_only" };
const ORDER_STATUS = { New: "open", PartiallyFilled: "partially_filled", Untriggered: "untriggered", Triggered: "open" };

export function orderType(o) {
  const limit = o.orderType === "Limit";
  switch (o.stopOrderType || "") {
    case "":
      return limit ? "limit" : o.orderType === "Market" ? "market" : "other";
    case "TakeProfit":
    case "PartialTakeProfit":
      return limit ? "take_profit_limit" : "take_profit_market";
    case "StopLoss":
    case "PartialStopLoss":
    case "Stop":
      return limit ? "stop_limit" : "stop_market";
    case "TrailingStop":
      return "trailing_stop";
    default:
      // tpslOrder, OcoOrder, BidirectionalTpslOrder, MmRateClose: no one type.
      return "other";
  }
}

export function orderRow(o, inst) {
  const type = orderType(o);
  const price = type === "market" || type === "stop_market" || type === "take_profit_market" ? null : nonZero(o.price);
  const trigger = nonZero(o.triggerPrice);
  let size = parseDec(o.qty);
  let filled = parseDec(o.cumExecQty);
  let remaining = parseDec(o.leavesQty);
  // An inverse order counts USD, and a spot market order may count the quote
  // coin: either is turned into the underlying at the order's own price.
  if (o.category === "inverse" || (o.category === "spot" && o.marketUnit === "quoteCoin")) {
    const ref = parseDec(price ?? trigger);
    if (isZero(ref)) return null;
    size = div(size, ref, 8);
    filled = div(filled, ref, 8);
    remaining = div(remaining, ref, 8);
  }
  if (isZero(size) || size.n < 0n) return null;
  return {
    ...instrumentFields(o.category, o.symbol, inst),
    order_id: String(o.orderId),
    client_order_id: o.orderLinkId || null,
    side: o.side === "Buy" ? "buy" : "sell",
    type,
    venue_type: o.stopOrderType || o.orderType || "unknown",
    status: ORDER_STATUS[o.orderStatus] ?? "open",
    venue_status: o.orderStatus || null,
    price,
    trigger_price: trigger,
    size: fmt(size),
    filled_size: fmt(filled),
    remaining_size: fmt(remaining),
    time_in_force: TIME_IN_FORCE[o.timeInForce] ?? (o.timeInForce ? "other" : null),
    reduce_only: typeof o.reduceOnly === "boolean" ? o.reduceOnly : null,
    created_at: iso(o.createdTime),
    expires_at: null,
  };
}

export function balanceRow(c) {
  const total = dec(c.walletBalance);
  if (!c.coin || total == null) return null;
  return { asset: c.coin, total, updated_at: null };
}

async function normalize(ctx, out, errors) {
  const rows = {};
  if ("balances" in out) rows.balances = out.balances.map(balanceRow).filter(Boolean);
  const held = new Map();
  const withTickers = new Set();
  for (const p of out.positions ?? []) {
    const key = instrumentKey(p.category, p.symbol);
    held.set(key, { category: p.category, symbol: p.symbol });
    if (p.category !== "spot") withTickers.add(key);
  }
  for (const o of out.open_orders ?? []) {
    held.set(instrumentKey(o.category, o.symbol), { category: o.category, symbol: o.symbol });
  }
  if (!("positions" in out) && !("open_orders" in out)) return rows;
  const market = await marketData(ctx, held, withTickers);
  if (market.instrumentsError) {
    // Without the instruments nothing says what a symbol is: those rows keep
    // their last copy rather than claim a kind or an underlying.
    errors.instruments = market.instrumentsError;
    return rows;
  }
  if (market.tickersError) errors.tickers = market.tickersError;
  const marginMode = out.account_info?.marginMode;
  if ("positions" in out) {
    rows.positions = out.positions
      .map((p) => {
        const key = instrumentKey(p.category, p.symbol);
        return positionRow(p, market.instruments.get(key), market.tickers.get(key), marginMode);
      })
      .filter(Boolean);
  }
  if ("open_orders" in out) {
    rows.open_orders = out.open_orders
      .map((o) => orderRow(o, market.instruments.get(instrumentKey(o.category, o.symbol))))
      .filter(Boolean);
  }
  return rows;
}

// ---- the fetch ----

export async function main(env) {
  const cred = credential(env);
  const where = environment(env, cred.label);
  const ctx = { ...cred, base: where.base };
  // Demo trading has no options (the bybit-api skill).
  const noOptions = (q) => !(where.id === "demo" && q.category === "option");
  const readers = {
    positions: async () =>
      (await each(POSITION_QUERIES.filter(noOptions), (q) => pages(ctx, "/v5/position/list", q, 200))).filter(
        (p) => !isZero(parseDec(p.size)),
      ),
    open_orders: () => each(ORDER_QUERIES.filter(noOptions), (q) => pages(ctx, "/v5/order/realtime", q, 50)),
    wallet: () => get(ctx, "/v5/account/wallet-balance", { accountType: "UNIFIED" }, true),
    account_info: () => get(ctx, "/v5/account/info", {}, true),
  };
  const out = {};
  const errors = {};
  let refused = null;
  await Promise.all(
    Object.entries(readers).map(async ([dataset, read]) => {
      try {
        out[dataset] = await read();
      } catch (err) {
        if (err instanceof Refused) refused ??= err;
        else errors[dataset] = err.message.slice(0, 300);
      }
    }),
  );
  if (refused) fail(refused.message, EXIT_AUTH);
  if ("wallet" in out) {
    const unified = (out.wallet.list ?? []).find((a) => a.accountType === "UNIFIED") ?? out.wallet.list?.[0];
    const { coin = [], ...account } = unified ?? {};
    out.balances = coin;
    out.account = account;
    delete out.wallet;
  } else if (errors.wallet) {
    errors.balances = errors.wallet;
    errors.account = errors.wallet;
    delete errors.wallet;
  }
  const ordered = {};
  for (const k of ["positions", "open_orders", "balances", "account", "account_info"]) if (k in out) ordered[k] = out[k];
  ordered._normalized = await normalize(ctx, ordered, errors);
  if (Object.keys(errors).length) ordered._errors = errors;
  return ordered;
}

const isMain = (() => {
  try {
    return realpathSync(process.argv[1] ?? "") === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
})();

if (isMain) {
  try {
    process.stdout.write(`${JSON.stringify(await main(process.env))}\n`);
  } catch (err) {
    fail(`${err.name}: ${err.message}`);
  }
}
