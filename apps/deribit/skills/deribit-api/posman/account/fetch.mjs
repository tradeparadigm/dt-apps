// A posman adapter: one Deribit account, fetched once.
//
// DIME Terminal's posman skill runs this as `node fetch.mjs` on the account's
// interval and stores what it prints, unchanged. POSMAN.md beside it says how
// to read each dataset. Nothing else runs it: an agent tests it with
// `posman.mjs test --adapter deribit/account`.
//
// What it reads from the environment (the runner sets all of it):
//   POSMAN_CRED               the NAME of the variable holding the client
//                             secret's placeholder: CRED_DERIBIT_<ENV>_<SLUG>
//   <POSMAN_CRED>_META        the credential's public details: `client_id`
//   POSMAN_PARAM_ENVIRONMENT  optional: mainnet or testnet; by default the one
//                             in the credential's label (deribit-<env>-<slug>)
//
// AUTHENTICATION, exactly as the deribit-api skill does it. Deribit does not
// sign: ONE call carries the credential, GET /api/v2/public/auth with
// grant_type=client_credentials, the client id from _META and the
// placeholder as client_secret, all in the query string, which is the one
// shape the credential proxy watches (it swaps the placeholder for the real
// secret on that path alone). Deribit answers with a short-lived access
// token, sent as `Authorization: Bearer` on every other call. Never the
// refresh_token grant: it hits the same path without the placeholder, which
// the proxy refuses.
//
// What it prints: one JSON object, Deribit's records as Deribit sends them:
//   positions    private/get_positions?currency=any (every currency and kind)
//   open_orders  private/get_open_orders (every currency, kind and type)
//   balances     private/get_account_summaries, its `summaries`: one
//                record per currency (balance, equity, margin, greeks totals)
// A dataset that fails is named in `_errors` instead, so the others are kept.
//
// And the same records as posman's rows (`_normalized`: positions,
// open_orders, balances; posman's SCHEMA.md), which is what the agent and the
// terminal read. Public reads fill them in: public/get_instrument for each
// instrument held or ordered (its kind, currencies, contract type, expiry,
// strike) and public/ticker for each perpetual and option held (funding,
// IV) and each coin-margined option ordered (the index, for its USD price).
// How each field is mapped is in POSMAN.md, "posman's rows".
//
// Exit 3: the credential was refused (the proxy's 401/403, Deribit's
// 13004 invalid_credentials at the token exchange, or every private call
// refused). Exit 2: the source is set up wrong (no credential, no client id,
// an environment it cannot tell). Any other failure, exit 1.

import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

const EXIT_CONFIG = 2;
const EXIT_AUTH = 3;

// The app's environments (app.yaml), by the id a credential's label carries.
const HOSTS = { mainnet: "www.deribit.com", testnet: "test.deribit.com" };
const TIMEOUT_MS = 20_000;
// Public lookups for the rows: at most this many instruments, this many at once.
const MAX_INSTRUMENTS = 200;
const CONCURRENCY = 4;
// Decimal places for a size converted from USD (an inverse future's order).
const CONVERTED_SCALE = 10;

// Deribit answers every error with HTTP 400 and a JSON-RPC error; these are
// the ones that say the credential (or the token it minted) is not good.
const REFUSAL_CODES = new Set([13004, 13009, 13021]);
const REFUSAL_MESSAGES = new Set([
  "invalid_credentials",
  "unauthorized",
  "forbidden",
  "insufficient_scope",
  "token_revoked",
]);

class Refused extends Error {}

function fail(message, code = 1) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

function parseJson(text) {
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

/** The credential's variable, its placeholder, its client id and its label. */
function credential(env) {
  const name = env.POSMAN_CRED || "";
  if (!name) fail("no credential: track this account with a Deribit client secret (--cred CRED_DERIBIT_...)", EXIT_CONFIG);
  const placeholder = env[name] || "";
  if (!placeholder) fail(`${name} is not among the agent's credentials`, EXIT_CONFIG);
  const meta = parseJson(env[`${name}_META`]);
  const clientId = meta && typeof meta.client_id === "string" ? meta.client_id.trim() : "";
  if (!clientId) {
    fail(`${name}_META has no client_id: enrol the credential again with its client id`, EXIT_CONFIG);
  }
  const label = name.replace(/^CRED_/, "").toLowerCase().replaceAll("_", "-");
  return { name, placeholder, clientId, label };
}

/** The REST root for the account's environment. */
function baseUrl(env, label) {
  // Tests only: a local stand-in for Deribit. Never set on a pod.
  if (env.POSMAN_DERIBIT_API_URL) return env.POSMAN_DERIBIT_API_URL.replace(/\/+$/, "");
  const parts = label.split("-");
  const wanted = env.POSMAN_PARAM_ENVIRONMENT || (parts.length >= 3 && parts[0] === "deribit" ? parts[1] : "");
  if (!Object.hasOwn(HOSTS, wanted)) {
    fail(
      `cannot tell the Deribit environment from the credential ${JSON.stringify(label)}: ` +
        `add --param ENVIRONMENT=<${Object.keys(HOSTS).join("|")}>`,
      EXIT_CONFIG,
    );
  }
  return `https://${HOSTS[wanted]}/api/v2`;
}

/** One JSON-RPC-over-GET call: its `result`, or a throw (Refused for the credential). */
async function call(base, method, params = {}, token = null) {
  const query = new URLSearchParams(params).toString();
  const url = `${base}/${method}${query ? `?${query}` : ""}`;
  const headers = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(url, { headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (err) {
    throw new Error(`${method}: ${err.name === "TimeoutError" ? "no answer in 20s" : err.message}`);
  }
  const text = await res.text();
  if (res.status === 401 || res.status === 403) {
    throw new Refused(`${method}: HTTP ${res.status}${text ? `: ${text.slice(0, 200).trim()}` : ""}`);
  }
  const body = parseJson(text);
  const error = body && typeof body === "object" ? body.error : null;
  if (error && typeof error === "object") {
    const why = `${method}: ${error.code} ${error.message}`;
    if (REFUSAL_CODES.has(error.code) || REFUSAL_MESSAGES.has(error.message)) throw new Refused(why);
    throw new Error(why);
  }
  if (!res.ok) throw new Error(`${method}: HTTP ${res.status}`);
  if (!body || typeof body !== "object" || !("result" in body)) throw new Error(`${method}: an answer with no result`);
  return body.result;
}

/** Run fn over items, at most `limit` at once; settles every one. */
async function pool(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      try {
        results[i] = { ok: true, value: await fn(items[i]) };
      } catch (err) {
        results[i] = { ok: false, error: err };
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

// ---- decimals: exact, as strings ----
//
// Deribit sends JSON numbers (and writes small ones as 1.898e-05), and
// posman's rows want decimal strings with no exponent. Every conversion and
// every product below is done on digits with BigInt, never in floating point.

/** A number or numeric string as a plain decimal string; null when it is not one. */
export function dec(value) {
  if (value == null || value === "") return null;
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "number" && !Number.isFinite(value)) return null;
  const m = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(String(value).trim());
  if (!m || (m[2] === "" && (m[3] ?? "") === "")) return null;
  const sign = m[1] === "-" ? "-" : "";
  let digits = (m[2] || "") + (m[3] || "");
  let point = (m[2] || "").length + Number(m[4] || 0);
  if (point < 0) {
    digits = "0".repeat(-point) + digits;
    point = 0;
  }
  if (point > digits.length) digits += "0".repeat(point - digits.length);
  const int = digits.slice(0, point).replace(/^0+/, "") || "0";
  const frac = digits.slice(point).replace(/0+$/, "");
  const out = frac ? `${int}.${frac}` : int;
  return out === "0" ? "0" : sign + out;
}

/** A decimal string as [BigInt units, scale]. */
function toUnits(d) {
  const neg = d.startsWith("-");
  const [int, frac = ""] = (neg ? d.slice(1) : d).split(".");
  const units = BigInt(int + frac);
  return [neg ? -units : units, frac.length];
}

function fromUnits(units, scale) {
  const neg = units < 0n;
  let s = (neg ? -units : units).toString();
  if (scale > 0) {
    s = s.padStart(scale + 1, "0");
    s = `${s.slice(0, -scale)}.${s.slice(-scale)}`;
  }
  return dec((neg ? "-" : "") + s);
}

/** a × b, exactly. */
export function mul(a, b) {
  const [ua, sa] = toUnits(a);
  const [ub, sb] = toUnits(b);
  return fromUnits(ua * ub, sa + sb);
}

/** a ÷ b, rounded half away from zero to `scale` places; null for b = 0. */
export function div(a, b, scale = CONVERTED_SCALE) {
  const [ua, sa] = toUnits(a);
  const [ub, sb] = toUnits(b);
  if (ub === 0n) return null;
  // a/b = (ua/10^sa) / (ub/10^sb); scaled by 10^scale, rounded.
  let num = ua * 10n ** BigInt(sb + scale);
  let den = ub * 10n ** BigInt(sa);
  if (den < 0n) {
    num = -num;
    den = -den;
  }
  const q = num / den;
  const r = num % den;
  const twice = (r < 0n ? -r : r) * 2n;
  const rounded = twice >= den ? q + (num < 0n ? -1n : 1n) : q;
  return fromUnits(rounded, scale);
}

/** a − b, exactly. */
export function sub(a, b) {
  const [ua, sa] = toUnits(a);
  const [ub, sb] = toUnits(b);
  const s = Math.max(sa, sb);
  return fromUnits(ua * 10n ** BigInt(s - sa) - ub * 10n ** BigInt(s - sb), s);
}

const abs = (d) => (d.startsWith("-") ? d.slice(1) : d);
const isZero = (d) => d == null || !/[1-9]/.test(d);
const signed = (d, negative) => (isZero(d) ? "0" : negative ? `-${abs(d)}` : abs(d));
const iso = (ms) => (Number.isFinite(ms) && ms > 0 ? new Date(ms).toISOString() : null);

// ---- posman's rows ----

const PERPETUAL = "perpetual";
const COMBO_KINDS = new Set(["future_combo", "option_combo"]);

/** What a row says about its instrument; null for one posman's schema has no row for (a combo). */
export function instrumentFields(inst) {
  const kind =
    inst.kind === "option"
      ? "option"
      : inst.kind === "spot"
        ? "spot"
        : inst.kind === "future"
          ? inst.settlement_period === PERPETUAL
            ? "perp"
            : "future"
          : null;
  if (kind == null) return null;
  const inverse = inst.instrument_type === "reversed";
  // A coin-margined option is priced in its coin (quote_currency BTC) with its
  // strike and greeks in USD (counter_currency): its rows are in USD.
  const quote = kind === "option" && inverse ? inst.counter_currency : inst.quote_currency;
  return {
    symbol: inst.instrument_name,
    kind,
    underlying: inst.base_currency,
    quote_asset: quote || null,
    settle_asset: kind === "spot" ? null : inst.settlement_currency || null,
    contract: kind === "spot" ? null : inverse ? "inverse" : inst.instrument_type === "linear" ? "linear" : null,
    expiry: kind === "future" || kind === "option" ? iso(inst.expiration_timestamp) : null,
    strike: kind === "option" ? dec(inst.strike) : null,
    option_type: kind === "option" && (inst.option_type === "call" || inst.option_type === "put") ? inst.option_type : null,
  };
}

const isInverseOption = (inst) => inst.kind === "option" && inst.instrument_type === "reversed";

/** One position's row, or null for a position with nothing open (or a combo). */
export function positionRow(p, inst, ticker) {
  const fields = instrumentFields(inst);
  if (!fields || (p.direction !== "buy" && p.direction !== "sell")) return null;
  const short = p.direction === "sell";
  // Futures (perpetuals included) size in USD (inverse) or the coin (linear):
  // size_currency is the position in the coin either way. An option's size is
  // in the coin already.
  const raw = fields.kind === "option" ? dec(p.size) : dec(p.size_currency);
  if (isZero(raw)) return null;
  const size = signed(raw, short);
  const option = fields.kind === "option";
  const inverseOption = isInverseOption(inst);
  const markCoin = dec(p.mark_price);
  const index = dec(p.index_price);
  const row = {
    ...fields,
    side: short ? "short" : "long",
    size,
    entry_price: inverseOption ? dec(p.average_price_usd) : dec(p.average_price),
    // A coin-margined option's mark is in the coin; in USD it is that times
    // the position's own index price, Deribit's coin-to-USD rate for it.
    mark_price: inverseOption ? (markCoin != null && index != null ? mul(markCoin, index) : null) : markCoin,
    liquidation_price: null,
    unrealized_pnl: dec(p.total_profit_loss),
    funding_pnl: null,
    pnl_asset: inst.settlement_currency || null,
    leverage: option ? null : dec(p.leverage),
    funding_rate: null,
    delta: option ? dec(p.delta) : size,
    gamma: option ? dec(p.gamma) : "0",
    vega: option ? dec(p.vega) : "0",
    theta: option ? dec(p.theta) : "0",
    iv: null,
  };
  if (row.unrealized_pnl == null) row.pnl_asset = null;
  if (fields.kind === "perp" && ticker) {
    const f8 = dec(ticker.funding_8h);
    // Deribit's funding_8h is per 8 hours; posman's is per hour: ÷ 8 is × 0.125, exactly.
    row.funding_rate = f8 == null ? null : mul(f8, "0.125");
  }
  if (option && ticker) {
    const iv = dec(ticker.mark_iv);
    row.iv = iv == null ? null : div(iv, "100", 12);
  }
  return row;
}

const ORDER_TYPES = {
  limit: "limit",
  market: "market",
  stop_limit: "stop_limit",
  stop_market: "stop_market",
  take_limit: "take_profit_limit",
  take_market: "take_profit_market",
  trailing_stop: "trailing_stop",
};

const TIME_IN_FORCE = {
  good_til_cancelled: "gtc",
  good_til_day: "gtd",
  fill_or_kill: "fok",
  immediate_or_cancel: "ioc",
};

/** One open order's row, or null for one posman cannot size (or a combo). */
export function orderRow(o, inst, ticker) {
  const fields = instrumentFields(inst);
  if (!fields || (o.direction !== "buy" && o.direction !== "sell")) return null;
  const inverseFuture = (fields.kind === "perp" || fields.kind === "future") && inst.instrument_type === "reversed";
  const limit = typeof o.price === "number" ? dec(o.price) : null;
  const trigger = dec(o.trigger_price);
  const amount = dec(o.amount);
  const filled = dec(o.filled_amount) ?? "0";
  if (isZero(amount)) return null;
  let size = amount;
  let filledSize = filled;
  let remaining = sub(amount, filled);
  if (inverseFuture) {
    // An inverse future's order is sized in USD; in the coin it is that over
    // its price (the limit, else the trigger), to CONVERTED_SCALE places.
    const at = !isZero(limit) ? limit : !isZero(trigger) ? trigger : null;
    if (at == null) return null;
    size = div(amount, at);
    filledSize = div(filled, at);
    remaining = div(remaining, at);
    if (isZero(size)) return null;
  }
  let price = limit;
  if (isInverseOption(inst)) {
    // A coin-margined option's limit is in the coin; its USD price is that
    // times the index this fetch read (null without it).
    const index = ticker ? dec(ticker.index_price) : null;
    price = limit != null && index != null ? mul(limit, index) : null;
  }
  const status =
    o.order_state === "untriggered" ? "untriggered" : !isZero(filled) ? "partially_filled" : "open";
  const label = typeof o.label === "string" && o.label.trim() ? o.label : null;
  return {
    ...fields,
    order_id: String(o.order_id),
    client_order_id: label,
    side: o.direction,
    type: ORDER_TYPES[o.order_type] ?? "other",
    venue_type: o.order_type,
    status,
    venue_status: o.order_state ?? null,
    price,
    trigger_price: trigger,
    size,
    filled_size: filledSize,
    remaining_size: remaining != null && remaining.startsWith("-") ? "0" : remaining,
    time_in_force: o.post_only === true ? "post_only" : (TIME_IN_FORCE[o.time_in_force] ?? (o.time_in_force ? "other" : null)),
    reduce_only: typeof o.reduce_only === "boolean" ? o.reduce_only : null,
    created_at: iso(o.creation_timestamp),
    expires_at: null,
  };
}

/** One currency's row: its cash balance, for every currency whose balance is not 0. */
export function balanceRow(s) {
  const total = dec(s.balance);
  if (typeof s.currency !== "string" || !s.currency || isZero(total)) return null;
  return { asset: s.currency, total };
}

// ---- the fetch ----

async function main(env = process.env) {
  const cred = credential(env);
  const base = baseUrl(env, cred.label);

  let token;
  try {
    const auth = await call(base, "public/auth", {
      grant_type: "client_credentials",
      client_id: cred.clientId,
      client_secret: cred.placeholder,
    });
    token = auth && typeof auth.access_token === "string" ? auth.access_token : null;
    if (!token) fail("public/auth: an answer with no access_token");
  } catch (err) {
    fail(err.message, err instanceof Refused ? EXIT_AUTH : 1);
  }

  const out = {};
  const errors = {};
  const refused = [];
  const fetchers = {
    positions: () => call(base, "private/get_positions", { currency: "any" }, token),
    open_orders: () => call(base, "private/get_open_orders", {}, token),
    balances: async () => {
      const result = await call(base, "private/get_account_summaries", {}, token);
      if (!result || !Array.isArray(result.summaries)) throw new Error("private/get_account_summaries: no summaries");
      return result.summaries;
    },
  };
  await Promise.all(
    Object.entries(fetchers).map(async ([dataset, fetchOne]) => {
      try {
        const value = await fetchOne();
        if (dataset !== "balances" && !Array.isArray(value)) throw new Error(`${dataset}: the answer is not a list`);
        out[dataset] = value;
      } catch (err) {
        if (err instanceof Refused) refused.push(err.message);
        errors[dataset] = err instanceof Refused ? `refused: ${err.message}` : err.message;
      }
    }),
  );
  if (refused.length === Object.keys(fetchers).length) fail(refused.join("; "), EXIT_AUTH);

  // The instruments the rows need, and the tickers for funding, IV and the index.
  const normalized = {};
  const want = new Set();
  const held = (out.positions ?? []).filter((p) => p && (p.direction === "buy" || p.direction === "sell"));
  for (const p of held) want.add(p.instrument_name);
  for (const o of out.open_orders ?? []) if (o && typeof o.instrument_name === "string") want.add(o.instrument_name);
  const names = [...want].filter((n) => typeof n === "string" && n).sort();
  const instruments = new Map();
  let instrumentsOk = true;
  if (names.length > MAX_INSTRUMENTS) {
    errors.instruments = `${names.length} instruments, more than ${MAX_INSTRUMENTS} looked up a fetch`;
    instrumentsOk = false;
  } else {
    const looked = await pool(names, CONCURRENCY, (name) =>
      call(base, "public/get_instrument", { instrument_name: name }, token),
    );
    looked.forEach((r, i) => {
      if (r.ok && r.value && typeof r.value === "object") instruments.set(names[i], r.value);
      else if (!errors.instruments) errors.instruments = r.ok ? `${names[i]}: no instrument` : r.error.message;
    });
  }
  const tickerNames = new Set();
  for (const p of held) {
    const inst = instruments.get(p.instrument_name);
    if (inst && (inst.kind === "option" || inst.settlement_period === PERPETUAL)) tickerNames.add(p.instrument_name);
  }
  for (const o of out.open_orders ?? []) {
    const inst = o && instruments.get(o.instrument_name);
    if (inst && isInverseOption(inst)) tickerNames.add(o.instrument_name);
  }
  const tickers = new Map();
  const tNames = [...tickerNames].sort();
  const got = await pool(tNames, CONCURRENCY, (name) => call(base, "public/ticker", { instrument_name: name }, token));
  got.forEach((r, i) => {
    if (r.ok && r.value && typeof r.value === "object") tickers.set(tNames[i], r.value);
    else if (!errors.tickers) errors.tickers = r.ok ? `${tNames[i]}: no ticker` : r.error.message;
  });

  // A kind is mapped only when every instrument it names was found: rows
  // missing from a kind would read as positions closed. Left out, the kind
  // keeps its last rows, and `_errors.instruments` says why.
  const covered = (list) => instrumentsOk && list.every((r) => instruments.has(r.instrument_name));
  if (out.positions && covered(held)) {
    normalized.positions = held
      .map((p) => positionRow(p, instruments.get(p.instrument_name), tickers.get(p.instrument_name)))
      .filter(Boolean);
  }
  if (out.open_orders && covered(out.open_orders)) {
    normalized.open_orders = out.open_orders
      .map((o) => orderRow(o, instruments.get(o.instrument_name), tickers.get(o.instrument_name)))
      .filter(Boolean);
  }
  if (out.balances) normalized.balances = out.balances.map(balanceRow).filter(Boolean);

  if (Object.keys(errors).length) out._errors = errors;
  out._normalized = normalized;
  process.stdout.write(`${JSON.stringify(out)}\n`);
}

/** Run as a script (the runner's `node fetch.mjs`), not when a test imports it. */
function isMain() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMain()) await main();
