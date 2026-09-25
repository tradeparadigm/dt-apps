# dt-apps — writing an app

The README says what an app is and what the manifest fields mean. This file is
what to get right when writing or changing one, and most of it comes from
skills that were wrong in a way the checker cannot see.

## The skill is read by an agent under pressure

It is not documentation for a person browsing. It is read once, mid-task, by
something that will act on it immediately and will not go back to check.

- **Say what to do, in the order it is done.** A fact the agent needs at step
  three is useless in a section it reaches after step five.
- **Anything the agent must reproduce byte-for-byte, show as bytes.** A signed
  string described in prose is a signed string that will be built wrong.
- **Write down what the venue's own documentation leaves out.** That is most of
  the value here. If the skill only restates the public docs, the agent could
  have read those.

## Public details go out verbatim

`detail_fields` are non-secret values the user pastes and the agent transmits —
an api key, a client id, an account address. Four of the six apps have them.

An agent masks credential-shaped values by reflex, and a masked public detail
breaks the call. Worse, venues report it as an authentication or signature
failure, so the agent goes looking at the wrong layer. `AGENTS.md` in
dime-terminal now carries this rule for every app; repeat it in a skill only
where that venue makes it easy to get wrong.

## A failure table must list every cause of a symptom

This is the one that has already cost real time. Bybit's table said an
empty-bodied 401 means the signed bytes are wrong. It also means a bad api key
header, and an agent that had made exactly that mistake read the table, went to
the signature, and stayed there.

So: for each symptom, list every cause you know of, ordered by what to check
first, and check the cheap one before the clever one. One symptom mapping to
one cause is a table that will confidently mislead.

Two venues answering the same mistake differently is normal — the same bad key
gets `10003` from one Bybit endpoint and an empty 401 from another. Say so.

## Run it before merging

A skill change is not verified by the checker. The checker reads structure; the
skill is judged by whether an agent following it reaches the venue.

Install the app in a terminal, ask the agent to do the thing, and read what it
actually sent. Probe the edges too — the empty payload, the multi-parameter
query, the public endpoint, the deliberate misuse that should be refused. Those
are where skills are wrong.

## The loop, when iterating

A merge here is live in the catalogue within the refresh window, but an agent
only gets the new text when the app is **reinstalled** — publishing replaces an
app's skills directory wholesale. So: merge, wait for the refetch, toggle the
app off and on, re-test. Batch fixes rather than shipping one line at a time.

## Checks

```sh
python3 -m venv .venv && .venv/bin/pip install pyyaml pytest
.venv/bin/python scripts/check_structure.py
.venv/bin/python -m pytest scripts/test_check_structure.py -q
```

`check_structure.py` mirrors the server's loader rather than defining the
format — the Go struct tags are the schema, and the README records where the
two disagree. A manifest that passes here can still be refused on load, so a
change to either side is checked against the other.
