---
name: analyze
description: >
  Analyse the trade the user is looking at, on whichever venue they are
  looking at it on. Invoked as `/analyze <id> <description>`, and also when
  the user asks to analyse, benchmark, or get market colour on a fill or a
  block, however they spell it. Reads the session context to find the venue's
  own analyst and follows it. Use this rather than picking an analyst
  yourself, because the host states which one applies.
metadata:
  author: tradeparadigm
  version: "1.0"
---

# Analyze

This skill picks the analyst. It does no analysis of its own.

## Step 1 — read the session context

The host sends a `<session-context>` block once per connection, as
`key=value` pairs joined by `;`. Find `analyze_skill` in it. Its value is the
name of the skill that analyses a trade on the venue the user is currently
looking at.

## Step 2 — follow that skill

Read that skill and do exactly what it says, passing on everything after the
command: the id and the description the user gave. Do not summarise its
instructions, and do not add analysis of your own around them.

Any hidden context sent with the message, such as the trade row or a tape
snapshot, belongs to that skill. Pass it on.

## When there is no `analyze_skill`

Say that the session does not name an analyst, and ask which venue the trade
is on. Do not pick one. A wrong venue produces numbers that look right and
describe a different market, which is worse than asking.

## What this skill never does

- Name a venue of its own accord. The host states it.
- Fall back to a venue when the key is missing.
- Answer from the description in the command. That string is a label the user
  saw, and the analyst resolves the real trade.
