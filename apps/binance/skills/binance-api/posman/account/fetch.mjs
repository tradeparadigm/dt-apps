// A posman adapter: one Binance account, one product, fetched once.
//
// DIME Terminal's posman skill runs this as `node fetch.mjs` on the account's
// interval and stores what it prints. POSMAN.md beside it says how to read each
// dataset. Nothing else runs it: an agent tests it with
// `posman.mjs test --adapter binance/account`.
//
// ONE CREDENTIAL IS ONE PRODUCT. A Binance credential is enrolled per
// environment, and each environment is one host (app.yaml): spot
// (api.binance.com), USD-M futures (fapi.binance.com) and their demo twins.
// The proxy signs only for the host the credential was enrolled for, so a spot
// credential cannot read futures and the reverse. Track each as its own
// source; nothing here reaches for the other one, which would count the same
// position twice when both are tracked.
//
// What it reads from the environment (the runner sets all of it):
//   POSMAN_CRED                the NAME of the variable holding the signing
//                              placeholder: CRED_BINANCE_<ENV>_SECRET
//   POSMAN_PARAM_ENVIRONMENT   optional: spot, usdm, spot-demo or usdm-demo; by
//                              default the one in the credential's label
//                              (binance-<env>-secret)
//   POSMAN_BINANCE_API_URL     TESTS ONLY: a local stand-in for both Binance
//                              hosts, over plain HTTP. Never set on a pod.
//
// What it prints: one JSON object, Binance's records as Binance sends them.
// Spot:
//   balances          the `balances` of GET /api/v3/account?omitZeroBalances=true
//   account           the rest of that answer (commissions, permissions, ...)
//   open_orders       GET /api/v3/openOrders, every symbol
// USD-M futures:
//   positions         GET /fapi/v3/positionRisk, open positions only
//   open_orders       GET /fapi/v1/openOrders, every symbol
//   open_algo_orders  GET /fapi/v1/openAlgoOrders: stop, take-profit and
//                     trailing orders, which Binance keeps apart since
//                     2025-12-09 and openOrders never lists
//   balances          the `assets` of GET /fapi/v3/account, non-zero wallets
//   account           the rest of that answer: the account's totals
// A dataset that fails is named in `_errors` instead, so the others are kept.
//
// And the same records as posman's rows (`_normalized`; posman's SCHEMA.md).
// Public reads fill them in, only when there is something to map: exchange
// info (each symbol's base, quote and margin asset, contract type, delivery
// date), and for futures positions the premium index (funding rate), funding
// info (funding interval) and the signed symbol config (leverage, margin
// mode). How each field is mapped is in POSMAN.md, "posman's rows".
//
// SIGNING, as scripts/binance.mjs does it (references/signing.md): the query
// is percent-encoded once, its exact bytes go base64 in
// `X-Dime-Sign-<label>`, and `signature=<placeholder>` is appended last. The
// proxy writes the HMAC in place of the placeholder and attaches X-MBX-APIKEY
// itself; this never sends a key.
//
// Exit 3: Binance or the proxy refused the credential (401/403, or Binance's
// -2014, -2015, -2008, -1002, -1022 on a signed read). Exit 2: the source is
// set up wrong (no credential, an environment it cannot tell). Exit 1: nothing
// could be read at all (a network failure, Binance refusing the location with
// 451, a rate-limit ban), with every reason on stderr.

import { Buffer } from "node:buffer";

const EXIT_CONFIG = 2;
const EXIT_AUTH = 3;
const TIMEOUT_MS = 20_000;
const RECV_WINDOW = "10000";

// The app's environments (app.yaml), longest id first so `spot-demo` is not
// read as `spot`.
const ENVIRONMENTS = {
  "spot-demo": { host: "demo-api.binance.com", product: "spot" },
  "usdm-demo": { host: "demo-fapi.binance.com", product: "usdm" },
  spot: { host: "api.binance.com", product: "spot" },
  usdm: { host: "fapi.binance.com", product: "usdm" },
};

// Binance's codes for a key it will not take: no key, a bad key or IP or
// permission, an unknown key id, unauthorised, a signature that does not
// verify (a wrong secret, or bytes changed after signing).
const AUTH_CODES = new Set([-2014, -2015, -2008, -1002, -1022]);

class Refused extends Error {}

function fail(message, code = 1) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

// ---- decimal arithmetic on Binance's strings, exactly (BigInt) ----

function dec(s) {
  const m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(String(s ?? "").trim());
  if (!m) return null;
  const frac = m[3] ?? "";
  return { n: BigInt(`${m[1] ?? ""}${m[2]}${frac}`), scale: frac.length };
}

function fmt(n, scale) {
  const neg = n < 0n;
  const digits = (neg ? -n : n).toString().padStart(scale + 1, "0");
  const int = digits.slice(0, digits.length - scale);
  const frac = digits.slice(digits.length - scale).replace(/0+$/, "");
  const body = frac ? `${int}.${frac}` : int;
  return neg && body !== "0" ? `-${body}` : body;
}

function align(a, b) {
  const scale = Math.max(a.scale, b.scale);
  return [a.n * 10n ** BigInt(scale - a.scale), b.n * 10n ** BigInt(scale - b.scale), scale];
}

const isZero = (s) => dec(s)?.n === 0n;
const sign = (s) => {
  const d = dec(s);
  return d == null ? 0 : d.n > 0n ? 1 : d.n < 0n ? -1 : 0;
};
function add(a, b) {
  const [x, y, scale] = align(dec(a), dec(b));
  return fmt(x + y, scale);
}
function sub(a, b) {
  const [x, y, scale] = align(dec(a), dec(b));
  return fmt(x - y, scale);
}
// Sign flips on the string itself, so Binance's own digits are kept.
const negate = (s) => (sign(s) === 0 ? s : s.startsWith("-") ? s.slice(1) : `-${s}`);
const abs = (s) => (sign(s) < 0 ? negate(s) : s);
/** a / k for a whole k, to 12 places past what `a` gives, rounded half away from zero. */
function divide(a, k) {
  const d = dec(a);
  const extra = 12;
  const scaled = d.n * 10n ** BigInt(extra) * 2n;
  let q = scaled / BigInt(k);
  q = q < 0n ? (q - 1n) / 2n : (q + 1n) / 2n;
  return fmt(q, d.scale + extra);
}

// A venue decimal as a row value: null for nothing, a plain decimal otherwise.
function num(v) {
  if (v == null || v === "") return null;
  const s = typeof v === "number" ? String(v) : v;
  return dec(s) ? s : null;
}
// A price where Binance writes 0 for "none" (a market order, no stop, no
// liquidation level).
const price = (v) => (num(v) == null || isZero(v) ? null : num(v));
const iso = (ms) => (Number.isFinite(Number(ms)) && Number(ms) > 0 ? new Date(Number(ms)).toISOString() : null);

// ---- the credential and the environment ----

function credential(env) {
  const name = env.POSMAN_CRED || "";
  if (!name) fail("no credential: track this account with a Binance signing credential (--cred CRED_BINANCE_..._SECRET)", EXIT_CONFIG);
  const value = env[name];
  if (!value) fail(`${name} is not among the agent's credentials`, EXIT_CONFIG);
  const label = name.replace(/^CRED_/, "").toLowerCase().replaceAll("_", "-");
  return { name, value, label, header: `X-Dime-Sign-${label}` };
}

function environment(env, label) {
  const wanted = env.POSMAN_PARAM_ENVIRONMENT;
  if (wanted) {
    if (!(wanted in ENVIRONMENTS)) {
      fail(`ENVIRONMENT=${wanted} is not one of ${Object.keys(ENVIRONMENTS).join(", ")}`, EXIT_CONFIG);
    }
    return wanted;
  }
  const rest = label.startsWith("binance-") ? label.slice("binance-".length) : "";
  for (const id of Object.keys(ENVIRONMENTS)) {
    if (rest === id || rest.startsWith(`${id}-`)) return id;
  }
  fail(
    `cannot tell the Binance environment from the credential ${label}: add --param ENVIRONMENT=<${Object.keys(ENVIRONMENTS).join("|")}>`,
    EXIT_CONFIG,
  );
}

// ---- Binance over HTTP ----

function makeClient({ base, cred }) {
  async function send(path, query, headers) {
    const url = `${base}${path}${query ? `?${query}` : ""}`;
    let res;
    try {
      res = await fetch(url, { headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
    } catch (err) {
      throw new Error(`GET ${path}: ${err.cause?.code ?? err.name ?? "failed"}: ${err.message}`);
    }
    const text = await res.text();
    let body = null;
    try {
      body = JSON.parse(text);
    } catch {
      // the proxy answers in plain text
    }
    if (res.ok && body != null && !(typeof body === "object" && !Array.isArray(body) && typeof body.code === "number" && body.code < 0)) {
      return body;
    }
    const code = body && typeof body.code === "number" ? body.code : null;
    const why = code != null ? `${code} ${body.msg ?? ""}`.trim() : text.split("\n")[0].slice(0, 200);
    const message = `GET ${path}: HTTP ${res.status}${why ? `: ${why}` : ""}`;
    if (res.status === 401 || res.status === 403 || (code != null && AUTH_CODES.has(code))) throw new Refused(message);
    if (res.status === 429 || res.status === 418) {
      throw new Error(`${message} (rate limited; Retry-After ${res.headers.get("retry-after") ?? "not given"}s)`);
    }
    if (res.status === 451) throw new Error(`GET ${path}: HTTP 451: Binance refuses requests from this location`);
    throw new Error(message);
  }
  return {
    /** A public read: no placeholder, no signing header. */
    get: (path, params = {}) => send(path, new URLSearchParams(params).toString(), {}),
    /** A signed read, exactly as scripts/binance.mjs builds one. */
    signed(path, params = {}) {
      const qs = new URLSearchParams({ ...params, recvWindow: RECV_WINDOW, timestamp: String(Date.now()) }).toString();
      return send(path, `${qs}&signature=${cred.value}`, { [cred.header]: Buffer.from(qs).toString("base64") });
    },
  };
}

/** Settle every read; a refusal of any of `fatal` ends the run (exit 3). */
async function settle(reads, fatal) {
  const names = Object.keys(reads);
  const results = await Promise.allSettled(names.map((n) => reads[n]()));
  const out = {};
  const refused = [];
  names.forEach((n, i) => {
    const r = results[i];
    out[n] = r.status === "fulfilled" ? { ok: true, value: r.value } : { ok: false, error: r.reason };
    if (r.status === "rejected" && r.reason instanceof Refused && fatal.includes(n)) refused.push(r.reason.message);
  });
  if (refused.length) {
    fail(`the credential was refused: ${refused.join("; ")}`, EXIT_AUTH);
  }
  return out;
}

const reason = (err) => err?.message ?? String(err);

// ---- instruments ----

const TYPES = {
  LIMIT: "limit",
  LIMIT_MAKER: "limit",
  MARKET: "market",
  STOP: "stop_limit",
  STOP_MARKET: "stop_market",
  STOP_LOSS: "stop_market",
  STOP_LOSS_LIMIT: "stop_limit",
  TAKE_PROFIT: "take_profit_limit",
  TAKE_PROFIT_LIMIT: "take_profit_limit",
  TAKE_PROFIT_MARKET: "take_profit_market",
  TRAILING_STOP_MARKET: "trailing_stop",
};
const CONDITIONAL = new Set(["stop_limit", "stop_market", "take_profit_limit", "take_profit_market", "trailing_stop"]);
const TIF = { GTC: "gtc", IOC: "ioc", FOK: "fok", GTX: "post_only", GTD: "gtd" };

/** A futures symbol's instrument fields, from exchange info. */
function futuresInstrument(info, symbol, marginAsset) {
  const s = info.get(symbol);
  if (!s) {
    // Not listed (delisted, or delivered): the symbol less a delivery suffix
    // and its margin asset, when it ends with it, is the base Binance named.
    const bare = symbol.replace(/_\d{6}$/, "");
    const underlying = marginAsset && bare.endsWith(marginAsset) && bare.length > marginAsset.length ? bare.slice(0, -marginAsset.length) : bare;
    return { symbol, kind: "other", underlying, settle_asset: marginAsset ?? null, contract: "linear" };
  }
  const perpetual = /PERPETUAL/.test(s.contractType ?? "");
  const dated = !perpetual && /QUARTER|WEEK|MONTH/.test(s.contractType ?? "");
  return {
    symbol,
    kind: perpetual ? "perp" : dated ? "future" : "other",
    underlying: s.baseAsset,
    quote_asset: s.quoteAsset ?? null,
    settle_asset: s.marginAsset ?? marginAsset ?? null,
    contract: "linear",
    expiry: dated ? iso(s.deliveryDate) : null,
  };
}

function spotInstrument(info, symbol) {
  const s = info.get(symbol);
  return s
    ? { symbol, kind: "spot", underlying: s.baseAsset, quote_asset: s.quoteAsset ?? null }
    : { symbol, kind: "spot", underlying: symbol };
}

// ---- spot ----

async function spot(client) {
  const out = {};
  const errors = {};
  const reads = await settle(
    {
      account: () => client.signed("/api/v3/account", { omitZeroBalances: "true" }),
      open_orders: () => client.signed("/api/v3/openOrders"),
    },
    ["account", "open_orders"],
  );
  if (reads.account.ok) {
    const { balances = [], ...rest } = reads.account.value;
    out.balances = balances.filter((b) => !(isZero(b.free) && isZero(b.locked)));
    out.account = rest;
  } else {
    errors.balances = errors.account = reason(reads.account.error);
  }
  if (reads.open_orders.ok) out.open_orders = reads.open_orders.value;
  else errors.open_orders = reason(reads.open_orders.error);
  if (!reads.account.ok && !reads.open_orders.ok) {
    fail(`nothing could be read: ${reason(reads.account.error)}; ${reason(reads.open_orders.error)}`);
  }

  const normalized = { positions: [] };
  if (out.balances) {
    normalized.balances = out.balances.map((b) => ({ asset: b.asset, total: add(b.free, b.locked) }));
  }
  if (out.open_orders) {
    const symbols = [...new Set(out.open_orders.map((o) => o.symbol))];
    let info = new Map();
    let mapped = true;
    if (symbols.length) {
      try {
        const ex = await client.get("/api/v3/exchangeInfo", { symbols: JSON.stringify(symbols) });
        info = new Map((ex.symbols ?? []).map((s) => [s.symbol, s]));
      } catch (err) {
        errors.exchange_info = reason(err);
        mapped = false;
      }
    }
    if (mapped) normalized.open_orders = out.open_orders.flatMap((o) => spotOrder(o, info));
  }
  return { out, errors, normalized };
}

function spotOrder(o, info) {
  if (sign(o.origQty) <= 0) return []; // sized in the quote asset alone: no size in units of the base
  const type = TYPES[o.type] ?? "other";
  const untriggered = o.status === "PENDING_NEW" || (CONDITIONAL.has(type) && o.isWorking === false);
  return [
    {
      ...spotInstrument(info, o.symbol),
      order_id: String(o.orderId),
      client_order_id: o.clientOrderId || null,
      side: String(o.side).toLowerCase(),
      type,
      venue_type: o.type,
      status: untriggered ? "untriggered" : o.status === "PARTIALLY_FILLED" ? "partially_filled" : "open",
      venue_status: o.status,
      price: type === "market" ? null : price(o.price),
      trigger_price: price(o.stopPrice),
      size: o.origQty,
      filled_size: num(o.executedQty),
      remaining_size: num(o.executedQty) == null ? null : sub(o.origQty, o.executedQty),
      time_in_force: o.type === "LIMIT_MAKER" ? "post_only" : (TIF[o.timeInForce] ?? (o.timeInForce ? "other" : null)),
      created_at: iso(o.time),
    },
  ];
}

// ---- USD-M futures ----

async function futures(client) {
  const out = {};
  const errors = {};
  const reads = await settle(
    {
      positions: () => client.signed("/fapi/v3/positionRisk"),
      open_orders: () => client.signed("/fapi/v1/openOrders"),
      account: () => client.signed("/fapi/v3/account"),
      open_algo_orders: () => client.signed("/fapi/v1/openAlgoOrders"),
    },
    ["positions", "open_orders", "account"],
  );
  if (reads.positions.ok) out.positions = reads.positions.value.filter((p) => !isZero(p.positionAmt));
  else errors.positions = reason(reads.positions.error);
  if (reads.open_orders.ok) out.open_orders = reads.open_orders.value;
  else errors.open_orders = reason(reads.open_orders.error);
  if (reads.open_algo_orders.ok) {
    out.open_algo_orders = reads.open_algo_orders.value;
  } else {
    const err = reads.open_algo_orders.error;
    errors.open_algo_orders =
      err instanceof Refused
        ? `${reason(err)}: this credential's routes predate /fapi/v1/openAlgoOrders (binance 1.2.0), so its stop and take-profit orders cannot be read; enrol the key pair again to read them`
        : reason(err);
  }
  if (reads.account.ok) {
    const { assets = [], positions, ...rest } = reads.account.value;
    out.balances = assets.filter((a) => !isZero(a.walletBalance));
    out.account = rest;
  } else {
    errors.balances = errors.account = reason(reads.account.error);
  }
  if (!reads.positions.ok && !reads.open_orders.ok && !reads.account.ok) {
    fail(`nothing could be read: ${[reads.positions, reads.open_orders, reads.account].map((r) => reason(r.error)).join("; ")}`);
  }

  const normalized = {};
  if (out.balances) {
    normalized.balances = out.balances.map((a) => ({ asset: a.asset, total: a.walletBalance, updated_at: iso(a.updateTime) }));
  }
  const positions = out.positions ?? [];
  const orders = [...(out.open_orders ?? []), ...(out.open_algo_orders ?? [])];
  if (!out.positions && !out.open_orders) return { out, errors, normalized };

  const lookups = {};
  if (positions.length || orders.length) lookups.exchange_info = () => client.get("/fapi/v1/exchangeInfo");
  if (positions.length) {
    lookups.premium_index = () => client.get("/fapi/v1/premiumIndex");
    lookups.funding_info = () => client.get("/fapi/v1/fundingInfo");
    lookups.symbol_config = () => client.signed("/fapi/v1/symbolConfig");
  }
  const got = await settle(lookups, []);
  for (const [name, r] of Object.entries(got)) if (!r.ok) errors[name] = reason(r.error);
  if (got.exchange_info && !got.exchange_info.ok) return { out, errors, normalized };

  const info = new Map((got.exchange_info?.value?.symbols ?? []).map((s) => [s.symbol, s]));
  const marks = new Map((got.premium_index?.value ?? []).map((p) => [p.symbol, p]));
  const hours = new Map((got.funding_info?.value ?? []).map((f) => [f.symbol, f.fundingIntervalHours]));
  const config = new Map((got.symbol_config?.value ?? []).map((c) => [c.symbol, c]));
  const marginOf = new Map(positions.map((p) => [p.symbol, p.marginAsset]));

  if (out.positions) {
    normalized.positions = positions.map((p) => futuresPosition(p, { info, marks, hours, config, fundingKnown: got.premium_index?.ok }));
  }
  if (out.open_orders) {
    const held = new Map(positions.map((p) => [`${p.symbol}/${positionSide(p)}`, abs(p.positionAmt)]));
    normalized.open_orders = [
      ...out.open_orders.flatMap((o) => futuresOrder(o, info, marginOf)),
      ...(out.open_algo_orders ?? []).flatMap((o) => algoOrder(o, info, marginOf, held)),
    ];
  }
  return { out, errors, normalized };
}

/** long or short: the hedge-mode side when Binance names one, else the sign. */
function positionSide(p) {
  if (p.positionSide === "LONG") return "long";
  if (p.positionSide === "SHORT") return "short";
  return sign(p.positionAmt) > 0 ? "long" : "short";
}

function futuresPosition(p, { info, marks, hours, config, fundingKnown }) {
  const side = positionSide(p);
  const size = side === "long" ? abs(p.positionAmt) : sign(p.positionAmt) < 0 ? p.positionAmt : negate(p.positionAmt);
  const instrument = futuresInstrument(info, p.symbol, p.marginAsset);
  const cfg = config.get(p.symbol);
  const rate = marks.get(p.symbol)?.lastFundingRate;
  const interval = Number(hours.get(p.symbol));
  const perHour =
    instrument.kind === "perp" && fundingKnown && num(rate) != null
      ? divide(rate, Number.isInteger(interval) && interval > 0 ? interval : 8)
      : null;
  return {
    ...instrument,
    side,
    size,
    entry_price: price(p.entryPrice),
    mark_price: price(p.markPrice),
    liquidation_price: price(p.liquidationPrice),
    unrealized_pnl: num(p.unRealizedProfit),
    funding_pnl: null,
    pnl_asset: p.marginAsset ?? instrument.settle_asset ?? null,
    margin_mode: cfg?.marginType === "ISOLATED" ? "isolated" : cfg?.marginType === "CROSSED" ? "cross" : null,
    leverage: num(cfg?.leverage),
    funding_rate: perHour,
    delta: size,
    gamma: "0",
    vega: "0",
    theta: "0",
    updated_at: iso(p.updateTime),
  };
}

function futuresOrder(o, info, marginOf) {
  if (sign(o.origQty) <= 0) return [];
  const type = TYPES[o.type] ?? "other";
  return [
    {
      ...futuresInstrument(info, o.symbol, marginOf.get(o.symbol)),
      order_id: String(o.orderId),
      client_order_id: o.clientOrderId || null,
      side: String(o.side).toLowerCase(),
      type,
      venue_type: o.type,
      status: CONDITIONAL.has(type) && o.status === "NEW" ? "untriggered" : o.status === "PARTIALLY_FILLED" ? "partially_filled" : "open",
      venue_status: o.status,
      price: type === "market" ? null : price(o.price),
      trigger_price: price(o.stopPrice),
      size: o.origQty,
      filled_size: num(o.executedQty),
      remaining_size: num(o.executedQty) == null ? null : sub(o.origQty, o.executedQty),
      time_in_force: TIF[o.timeInForce] ?? (o.timeInForce ? "other" : null),
      reduce_only: o.reduceOnly === true || o.closePosition === true,
      created_at: iso(o.time),
      expires_at: o.timeInForce === "GTD" ? iso(o.goodTillDate) : null,
    },
  ];
}

function algoOrder(o, info, marginOf, held) {
  // A close-position order carries no quantity: it closes whatever is open on
  // its side when it triggers, so its size is that position's, now. With no
  // such position it has no size to give, and is left out.
  let size = num(o.quantity);
  if (size == null || sign(size) <= 0) {
    const closes = o.positionSide === "LONG" || o.positionSide === "SHORT"
      ? o.positionSide.toLowerCase()
      : String(o.side).toUpperCase() === "SELL" ? "long" : "short";
    size = o.closePosition === true ? held.get(`${o.symbol}/${closes}`) ?? null : null;
    if (size == null) return [];
  }
  const type = TYPES[o.orderType] ?? "other";
  return [
    {
      ...futuresInstrument(info, o.symbol, marginOf.get(o.symbol)),
      order_id: String(o.algoId),
      client_order_id: o.clientAlgoId || null,
      side: String(o.side).toLowerCase(),
      type,
      venue_type: o.orderType,
      status: "untriggered",
      venue_status: o.algoStatus ?? null,
      price: type === "stop_limit" || type === "take_profit_limit" ? price(o.price) : null,
      trigger_price: price(o.triggerPrice),
      size,
      time_in_force: TIF[o.timeInForce] ?? (o.timeInForce ? "other" : null),
      reduce_only: o.reduceOnly === true || o.closePosition === true,
      created_at: iso(o.createTime),
      expires_at: o.timeInForce === "GTD" ? iso(o.goodTillDate) : null,
    },
  ];
}

// ---- the run ----

const cred = credential(process.env);
const envId = environment(process.env, cred.label);
const base = (process.env.POSMAN_BINANCE_API_URL || `https://${ENVIRONMENTS[envId].host}`).replace(/\/+$/, "");
const client = makeClient({ base, cred });
const { out, errors, normalized } = ENVIRONMENTS[envId].product === "spot" ? await spot(client) : await futures(client);
if (Object.keys(errors).length) out._errors = errors;
out._normalized = normalized;
process.stdout.write(`${JSON.stringify(out)}\n`);
