---
name: options-monitor
title: Global Options Monitor
description: Options block trades across Paradex, Derive, Deribit, Bullish and OKX as they print, one line per package with its legs, premium and notional.
needs: Nothing. It reads public market data.
---

# Set up the Global Options Monitor

The user asked for this data feed by name, or picked it from Paradex's
suggestions. It posts what Paradex's Global Option Monitor
(app.paradex.trade/option-monitor) lists: every options block trade on
Paradex, Derive, Deribit, Bullish and OKX, one line per package, as it
prints. For example:

```
Deribit · BTC iron condor · +40 16OCT26 80000P, -40 16OCT26 83500P, -40 16OCT26 84000C, +40 16OCT26 87000C · receives $51.2K · $13.4M notional
Paradex · BTC call · +3.205 23OCT26 90000C · pays $586 · $268K notional · via Paradigm
```

+ and - are each leg's side as the venue reports it, and "pays" or
"receives" is the option premium for that side. "via Paradigm" marks a
package Paradigm brokered. It uses no credential.

`<notification-feed>` below is the notification-feed skill's folder, and
`<this folder>` is the one holding this file. Write both out as absolute
paths.

## 1. Settle the settings without asking

Take them from the user's message, and use the default for anything it does
not mention. These are what the Option Monitor page itself shows, so a user
who named nothing gets that page as a feed.

- **Venues:** all five, unless they name some ("Deribit blocks").
- **Underlyings:** all of them, unless they name some ("BTC and ETH").
- **Size:** every block, a few hundred a day. A minimum applies only when
  they ask for one. If they give no number ("only the big ones"), use $250K
  notional and say so.
- **Perp hedge legs:** shown.
- **Where it posts:** a feed of its own if the chat opened from New Feed.
  Otherwise Notifications, unless they name a feed.

The one thing worth a question: they name a venue the tape does not carry
(Binance, say). Tell them which five it covers, and ask whether to go ahead
with those.

## 2. Install

Copy `monitor.mjs` and `datafeed.mjs` from `<this folder>` into
`~/.openclaw/workspace/datafeeds/options-monitor/`. Use another name there if
the user already has one called that. Beside them, write `config.json` with
only what differs from the defaults at the top of `datafeed.mjs`, for example:

```json
{ "name": "options-monitor", "underlyings": ["BTC", "ETH"], "minNotional": 250000 }
```

`name` must be the name you register it under. Venues are names or codes
(`PRDX`, `DRV`, `DBT`, `BLSH`, `OKX`).

## 3. Test

Blocks can be minutes apart, so look back three hours instead of waiting:

```sh
cd ~/.openclaw/workspace/datafeeds/options-monitor
FEED_DRY_RUN=1 OPTIONS_MONITOR_ONCE=1 OPTIONS_MONITOR_BACKFILL_MINUTES=180 node datafeed.mjs
```

It prints the lines it would post, each with `[options-monitor]` in front, and
exits 0.

- No lines and exit 0: nothing in those three hours passed the filters, or
  `config.json` names a venue or underlying the tape spells differently. Try a
  longer window (up to a day), then check the names against a run with no
  filters.
- `reading the tape failed: /v1/options-tape: HTTP <status>` and exit 1:
  Paradex refused or moved the route. A 404 means it moved; a 429, that it
  was read too often; a 5xx, an outage to retry later.
- `reading the tape failed: fetch failed` or a timeout: the pod cannot reach
  `api.prod.paradex.trade`. Check with `curl -sI https://api.prod.paradex.trade/v1/system/state`.
- `no results list`: Paradex changed the answer's shape. "How it works" below
  has what the script expects.

Then check the socket for 20 seconds:

```sh
FEED_DRY_RUN=1 timeout 20 node datafeed.mjs
```

It should log `connected` and nothing else unless a block prints meanwhile.
`reconnecting` repeated means the socket is refused: the HTTP reads still post
every five minutes, so say so to the user rather than stopping there.

## 4. Register

```sh
node <notification-feed>/scripts/feeds.mjs add options-monitor \
  --title "Global Options Monitor" \
  --description "Options block trades across Paradex, Derive, Deribit, Bullish and OKX, with legs, premium and notional." \
  --to <feed> --cwd ~/.openclaw/workspace/datafeeds/options-monitor \
  -- node ~/.openclaw/workspace/datafeeds/options-monitor/datafeed.mjs
```

Check that `feeds.mjs list` shows it `running`. Then tell the user where it
posts and the settings you chose (venues, underlyings, any minimum), and that
they can ask you to change any of it.

## If something here does not fit

Change the smallest thing that works, run the test again, and tell the user
the change in one line. The data feed is theirs, so adapt it to what they
asked rather than to this file.

## How it works, for changing it

- Paradex's options tape holds each block as one package with its legs,
  whichever venue printed it. Both reads are public and need no key; neither is
  in Paradex's API reference.
- The socket: `wss://ws-public.api.prod.paradex.trade/v1?cancel-on-disconnect=false&sbeSchemaId=1&sbeSchemaVersion=3`,
  then `{"id":1,"jsonrpc":"2.0","method":"subscribe","params":{"channel":"options_tape.ALL"}}`.
  Each package comes as JSON text in
  `{"method":"subscription","params":{"channel":"options_tape.ALL","data":<package>}}`.
  Without the `sbeSchema` parameters the handshake is refused.
- Over HTTP: `GET https://api.prod.paradex.trade/v1/options-tape?start_time=<ms>&page_size=1000`,
  newest first, `{results, next}`; the next page is `?cursor=<next>` alone.
  It also takes `end_time` and `venue`. `/v1/options-tape/status` says when
  each venue was last read.
- A package: `venue`, `block_id`, `route` (`DIRECT` or `PARADIGM`),
  `traded_at`, `underlying`, `notional_usd`, `complete`, `revision`,
  `removed`, and `legs[]` with `kind` (`OPTION` or `PERP`), `option_type`,
  `expiry` (ms), `strike`, `side`, `size`, `price_usd`.
- It posts a package once, by venue and block id, when it is complete and not
  removed. A later revision of one already posted is not posted again.
- It reads the tape over HTTP on every connect and every `pollMinutes`
  (default 5), half an hour behind the newest package, so one the socket
  missed still posts. What was on the tape when it started never posts.
- The strategy name (spread, iron condor, butterfly...) is worked out from the
  option legs in `monitor.mjs`; anything it does not recognise is "custom".
