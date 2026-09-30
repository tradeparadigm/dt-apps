---
name: recap
description: >
  Market recap for the venue the user is looking at. Invoked as
  `/recap [asset] [options] [window]`, and also when the user asks for a
  recap, a flow summary, "what happened in BTC options", or the last few
  hours of flow. Reads the session context to find the venue's own recap
  skill and follows it. Use this rather than picking a recap skill yourself,
  because the host states which one applies.
metadata:
  author: tradeparadigm
  version: "1.0"
---

# Recap

Pick the recap skill here. Write no recap in this skill.

## Step 1 — read the session context

The host sends a `<session-context>` block once per connection, as
`key=value` pairs joined by `;`. Find `recap_skill` in it. Its value is the
name of the skill that writes a recap for the venue the user is currently
looking at.

## Step 2 — follow that skill

Read that skill and do exactly what it says, passing on the asset, the window
and any other token the user gave. Relay its output as the whole answer.

## When there is no `recap_skill`

Say that the session names no recap skill, and ask which venue the user
wants. Do not pick one.

## Never

- Name a venue yourself. The host states it.
- Fall back to a venue when the key is missing.
- Merge two venues into one recap. One session names one skill.
