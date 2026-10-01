---
name: paradex-structure-analyst
description: >
  Analysis of a Paradex options structure the user is CONSIDERING, not one
  they have traded. Invoked as `/paradex_structure_analyst structure <product>
  <n> legs`, which the Paradex order builder sends from its Analyze with DT
  button, and the legs arrive as hidden context. Prices the structure at live
  marks, says what it costs or collects to put on, draws the payoff, and
  proposes changes the user can load straight back into the builder. Use when
  the user asks what a structure costs, whether it is priced well, or what a
  different strike or expiry would do. A trade that already filled belongs to
  paradex-trade-analyst. Paradigm RFQ blocks belong to
  paradigm-block-analyst.
compatibility: >
  Reads public Paradex market data only, so it needs NO credential. Marks,
  books and the instrument list are all public. Reports what it could not read
  rather than estimating.
metadata:
  author: tradeparadigm
  version: "1.0"
---

# Paradex structure analyst

Prices an options structure nobody has traded yet, and shows what it would
pay.

## Input

`/paradex_structure_analyst structure <product> <n> legs`. The visible line
is a label. The structure itself arrives as hidden context holding JSON: each
leg's `optionType`, `side`, `strike`, `size`, `price` (the live mark, not a
fill), `expiry` and `symbol`, plus the underlying's `spot` and
`dropped_non_option_legs`.

`symbol` is the market the order builder already resolved, so Step 4 can send
it straight back without a lookup. A leg the builder could not name arrives
without the field.

Nothing here has been traded. There is no fill to resolve, no position behind
it and no funding paid, so never report any of those.

Say what the dropped legs were when `dropped_non_option_legs` is above zero. A
perp hedge is part of the trade even where the payoff cannot draw it, and an
answer that ignores it describes a risk the user does not have.

## Step 1 — draw it, before anything else

Do this FIRST, before any venue read, any skill file and any credential
check. The legs and the spot are already in the context, so the chart needs
no API call and no helper. Send it, then do the rest.


Render the payoff as a component spec:

```json
{
  "id": "payoff-1",
  "layout": "stack",
  "children": [
    {
      "component": "options_payoff",
      "props": {
        "product": "BTC",
        "spot": 83356.2,
        "legs": [
          {"optionType": "CALL", "side": "BUY", "strike": 86000,
           "size": 1, "price": 1234.5,
           "symbol": "BTC-USD-3OCT26-86000-C"}
        ]
      }
    }
  ]
}
```

Write that object as the last thing in your reply, with your prose above it.
The terminal reads the outermost `{...}` of a message and renders it when it
carries `layout` and `children`, keeping the prose as the text of the bubble.
An object without both keys is not a spec and shows as raw JSON, which is what
a bare `{"mode": ..., "component": ..., "props": ...}` does.

Send `symbol` on every leg. For the structure as it arrived it is already in
the context, so pass it through rather than looking it up; for a leg you are
proposing, read it from `GET /v1/markets` and spell it exactly as that
returns it.

Send a string or leave the field out. Never send null, an empty string or a
guess: null is refused by the renderer and costs the whole chart, while
leaving the field out costs only the button.

Send the legs and nothing else. The chart works out the curve, the max loss,
the breakeven and the max profit from them, so a payoff number you calculated
yourself has no field to go in and must not appear in the prose either.

## Step 2 — what it costs

Sum the premiums, signed against each leg's side, and state it as a debit paid
or a credit collected, in quote currency and per contract. Say which legs pay
and which collect.

## Step 3 — is that a fair price

Compare each leg's mark against the book through the `paradex-api` skill:
`GET /v1/bbo/{market}` for the touch, `GET /v1/orderbook/{market}` for depth.
Say which legs sit inside the spread and which do not, in basis points of the
mark. These reads are public, so a missing credential is not a reason to skip
them.

Report the spread you would actually cross for the size asked, not the touch
alone, where the book reaches far enough. Say so when it does not.

## Step 4 — the shape

State max loss, max profit and every breakeven, as the payoff gives them, and
where spot sits against them now. Say when a loss or a profit is unbounded
rather than naming the edge of a window.


## Changing the trade

When the user asks what a different strike, expiry or ratio would do, look up
the new legs' marks, then write a new spec for the changed structure rather
than describing it in prose. A tweak they can load in one click is the
point of this skill.

Say what changed and what it cost: the new debit or credit against the old
one, and how the breakevens moved. Never place an order, and never tell the
user you have changed anything in their builder. Loading the structure is
their click.

## Output

The chart goes out first. Then the cost, the fairness and the shape. Say
what was unavailable rather than filling it in.

Never fabricate a mark, a book level or a premium. A missing venue read is a
missing line, and the line says which read failed.
