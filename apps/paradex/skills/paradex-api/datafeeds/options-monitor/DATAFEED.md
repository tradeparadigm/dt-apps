---
name: options-monitor
title: Global Options Monitor
description: Every options block trade on Paradex as it prints, with its legs, premium, notional and mark IV.
needs: Nothing. It reads public market data.
---

# Set up the Global Options Monitor

The user asked for this data feed by name, or picked it from Paradex's
suggestions. It posts one line per options block trade on Paradex: any
user's filled RFQ package, the list Paradex's own Option Monitor shows. For
example:

```
BTC block · +25 27JUN26 70000C, -25 27JUN26 80000C · hedge -10 PERP · taker pays $19.4K · $3.5M notional · mark IV 54%
```

+ and - are the taker's side. It uses no credential.

`<notification-feed>` below is the notification-feed skill's folder, and
`<this folder>` is the one holding this file. Write both out as absolute
paths.

## 1. Ask, offering the defaults

One message, then go with what they say:

- **Which underlyings.** Default: all of them. Otherwise a list such as BTC, ETH.
- **A minimum size.** Default: every block. Otherwise a minimum premium or
  notional in USD, for example only blocks over $250K notional.
- **The perp hedge legs.** Default: shown.
- **Where it posts.** If the chat opened from New Feed, a new feed of its own.
  Otherwise Notifications, unless they name a feed.

## 2. Install

Copy `monitor.mjs` and `datafeed.mjs` from `<this folder>` into
`~/.openclaw/workspace/datafeeds/options-monitor/`. Use another name there if
the user already has one called that. Beside them, write `config.json` with
only what differs from the defaults at the top of `datafeed.mjs`, for example:

```json
{ "name": "options-monitor", "baseAssets": ["BTC", "ETH"], "minNotional": 250000 }
```

`name` must be the name you register it under.

## 3. Test

Blocks are infrequent, so look back an hour instead of waiting:

```sh
cd ~/.openclaw/workspace/datafeeds/options-monitor
FEED_DRY_RUN=1 OPTIONS_MONITOR_ONCE=1 OPTIONS_MONITOR_BACKFILL_MINUTES=60 node datafeed.mjs
```

It prints the lines it would post, each with `[options-monitor]` in front.
- No lines and no error means nothing in the hour passed the filters. Try
  again with a longer window before changing anything.
- An error naming `/trades` or `/markets` means Paradex's API answered
  something else, which is the thing to fix.

## 4. Register

```sh
node <notification-feed>/scripts/feeds.mjs add options-monitor \
  --title "Global Options Monitor" \
  --description "Every options block trade on Paradex, with its legs, premium, notional and mark IV." \
  --to <feed> --cwd ~/.openclaw/workspace/datafeeds/options-monitor \
  -- node ~/.openclaw/workspace/datafeeds/options-monitor/datafeed.mjs
```

Check that `feeds.mjs list` shows it `running`, then tell the user where it
posts and that they can ask you to change any of it: the filters, the line,
or the polling, which is every 15 seconds.

## If something here does not fit

Change the smallest thing that works, run the test again, and tell the user
the change in one line. The data feed is theirs, so adapt it to what they
asked rather than to this file.

## How it works, for changing it

- It polls `GET /v1/trades?trade_type=BLOCK_TRADE`, public and without a key.
  It pages with `cursor`, reads two minutes back each time, and skips prints
  it has seen.
- Each print is one leg. The legs of a package share a `block_id`, so it posts
  a package once a whole poll goes by without adding a leg to it.
- It reads `/v1/markets` to tell option legs from hedges, with expiry, strike
  and type. It reads `/v1/markets/summary?market=ALL` for the underlying's
  price and each option's mark IV. A print carries neither.
- Paradex's WebSocket `trades` channel sends one message per leg, binary, with
  no `block_id`, so it cannot put a package back together. That is why this
  polls.
- On start it skips the blocks already there; only new ones post.
