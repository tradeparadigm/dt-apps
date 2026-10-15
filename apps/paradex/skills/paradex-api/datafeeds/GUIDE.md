# Building a Paradex data feed

Read this before writing a data feed, or any script, that reads Paradex.

Read Paradex's WebSocket with `stream()` from the client in `lib/` beside
this file: Paradex accepts only SBE, a binary encoding, and `stream()` speaks
it. REST market reads need no key.

## Which environment

Use production, even when the `paradex__` tools answer from another
environment and their numbers differ: market data is public, and production is
the live market. Use another environment only when the user names it, by
passing `url` to `stream` and `base` to the REST helpers:

| Environment | `url` | `base` |
|---|---|---|
| production (default) | `wss://ws.api.prod.paradex.trade/v1` | `https://api.prod.paradex.trade/v1` |
| testnet | `wss://ws.api.testnet.paradex.trade/v1` | `https://api.testnet.paradex.trade/v1` |
| nightly | `wss://ws.api.nightly.paradex.trade/v1` | `https://api.nightly.paradex.trade/v1` |

## 1. Copy the client

The folder holding this file is replaced when the app updates, so copy the
three files in `lib/` into the data feed's own directory. Where the
notification-feed skill has `feeds.mjs init`, `init <name> --from paradex`
does this and writes the starting `datafeed.mjs`; otherwise:

```sh
mkdir -p ~/.openclaw/workspace/datafeeds/<name>
cp <this folder>/lib/paradex.mjs <this folder>/lib/sbe.mjs <this folder>/lib/paradex_1_0.xml \
  ~/.openclaw/workspace/datafeeds/<name>/
```

Then `import { stream, perpSummaries, rest, restPages } from "./paradex.mjs";`
in `datafeed.mjs`, and post with the notification-feed skill's
`openDataFeed`, as its SKILL.md says.

### What the client offers

**`stream(channels, onEvent, options)`** subscribes to `channels`
(`["markets_summary.BTC-USD-PERP", "bbo.ETH-USD-PERP"]`) and calls
`onEvent(event)` for each update (section 2 has the fields), reconnecting and
resubscribing for as long as the process runs. It answers
`{subscribe(channel), unsubscribe(channel), close()}`. Every option is
optional:

| Option | Default | |
|---|---|---|
| `idleMs` | 5 minutes | reconnect after this long with no frame; `0` never |
| `onOpen()` | none | called on each connect, after subscribing: read what the gap missed over REST here |
| `log(text)` | stderr, `[paradex] ...` | connects, reconnects, a refused channel, giving up |
| `giveUpMs` | 10 minutes | with no connection opening for this long, it logs Paradex's answer and calls `onGiveUp` |
| `onGiveUp()` | exit 1 | so the data feed shows as failing rather than running and posting nothing |
| `url` | `WS_URL` | the schema version is added to it |

**`rest(path, query, {base, timeoutMs})`**: one `GET REST_URL + path` with
`query`, as parsed JSON. Throws on a non-2xx, naming the path and status.
`timeoutMs` is 30 s.

**`restPages(path, query, {base, maxPages})`**: every page of a paged read
(`{results, next}`) as one list; `maxPages` is 10.

**`perpSummaries({base})`**: every perp's REST summary (section 3).

`WS_URL` and `REST_URL` are exported too.

## 2. Pick the channel

Subscribe per market. A channel name without a market (`markets_summary`)
sends every market, about 9,600 options among them, at thousands of frames a
second.

| You want | Channel | The event's fields |
|---|---|---|
| Funding rate, mark, index, last price, open interest, 24h volume, an option's IV and greeks | `markets_summary.<market>` (about one a second) | `fundingRatePrecise` (prefer it; `fundingRate` when it is null), `markPrice`, `indexPrice`, `lastPrice`, `openInterest`, `volumeUsd24h`, `priceChangeRate24h`, `bid`, `ask`, `markIv`, `delta`, `gamma`, `vega` |
| Funding settlement detail | `funding_data.<market>` (every few seconds) | `fundingRate`, `fundingRate8h`, `fundingPremium`, `fundingIndex`, `fundingPeriodHours` |
| Best bid and offer | `bbo.<market>` | `bidPrice`, `bidSize`, `askPrice`, `askSize` |
| Trades | `trades.<market>` | `side` (`BUY`, `SELL`), `price`, `size`, `tradeType` (`FILL`, `LIQUIDATION`, `BLOCK_TRADE`...), `tradeIdStr` (not `tradeId`, which wraps) |
| Order book, 15 levels | `order_book.<market>.snapshot@15@100ms` (or `@50ms`) | `bids`, `asks`: `[{price, size}]`, best first |
| Options block trades on Paradex, Derive, Deribit, Bullish and OKX | `options_tape.ALL` | Paradex sends this one as JSON: the event is `{type, channel, data}`. The `options-monitor` suggestion shows it in use |

Every event also has `type` (the channel's kind, `"markets_summary"`),
`market` (`"BTC-USD-PERP"`) and `ts`.

- Prices and sizes are plain numbers. Rates are fractions: `0.0001` is
  0.01%.
- A funding rate is per funding period, 8 hours on every perp today.
- `ts` and `createdAt` are microseconds since the epoch; `ts / 1000` is
  milliseconds.
- A field Paradex leaves empty is `null`.

## 3. List the markets once, not per tick

`perpSummaries()` answers every perp's REST summary (`symbol`,
`funding_rate`, `mark_price`... as strings). Paradex's REST market reads take
no filter but a single market, so this read is every market, options
included: 6.5 MB and about 2 seconds. Call it once at start, for the list of
perps and where they stand, then follow them on the stream. Never put it, or
`GET /markets` (14.5 MB), on a timer.

Market symbols: `BTC-USD-PERP` for a perp, `BTC-USD-27JUN26-70000-C` for an
option.

## Example: a whole data feed

Every trade on the markets it names, one line each:

```js
import { openDataFeed } from "__FEED_HELPER__"; // keep the path init filled in
import { stream } from "./paradex.mjs";

const MARKETS = ["BTC-USD-PERP", "ETH-USD-PERP"];

const out = await openDataFeed("paradex-trades");
stream(MARKETS.map((m) => `trades.${m}`), (e) => {
  const kind = e.tradeType === "FILL" ? "" : ` ${e.tradeType}`;
  out.post(`${e.market} ${e.side} ${e.size} @ ${e.price}${kind}`);
});
```

A line reads `BTC-USD-PERP SELL 0.00015 @ 83742.7`.

## When it does not work

The client logs to stderr with `[paradex]` in front. Cheapest cause first:

| Log line | Causes |
|---|---|
| `connected` and then no events | The market symbol is misspelt or not listed: Paradex accepts any market name and sends nothing, so compare it with `perpSummaries()`; the market is quiet (a `trades` channel can be silent for minutes). `bbo.BTC-USD-PERP` updates every second or so, to check the connection |
| `<channel> refused: invalid subscribe request` | The channel name is malformed (an order book needs `.snapshot@15@100ms`); the channel is a private one (`orders`, `fills`, `positions`, `account`), which needs an authenticated connection this client does not make |
| `reconnecting`, again and again | The socket does not open. After 10 minutes of it the client says which cause (next row) and exits 1, so the data feed shows as restarting, and as failed after five quick exits, rather than running and posting nothing |
| `no connection for 10 minutes: Paradex refused the URL (HTTP 400) ...` | Paradex stopped serving the schema version this copy of the client names (`SBE_SCHEMA` in `paradex.mjs`): the Paradex app needs an update, and each data feed built on the old copy needs rebuilding from the new one, so tell the user. The `connected (paradex <version> client)` line in a test run shows which copy a data feed has |
| `no connection for 10 minutes: cannot reach ...` | The pod cannot reach `ws.api.prod.paradex.trade`: its network, or Paradex down |
| `undecodable frame skipped` | Paradex sent a frame `paradex_1_0.xml` does not describe: the app needs an update, so tell the user |
| A REST read throws `HTTP 429` | Too many REST reads; read on the stream instead |

If something here is wrong for what the user asked, change the smallest thing
that works, run it, and tell the user the change in one line.
