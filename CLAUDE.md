# dt-apps

The README says what an app is and what the manifest means. This is the part
the checker cannot catch.

## Anything an agent must run verbatim needs an escape hatch

A skill that says "run this exactly" and then hands over a line that is wrong
creates a deadlock, and the agent pays for it in the turn you were trying to
make cheap. One did: it spotted that a credential lookup depended on
environment ordering, and spent a minute trapped between the defect and the
instruction not to deviate.

So every block that asks to be run as written also says what to do when it
does not fit: change the smallest thing that works, run it, report the change
in one line. Prescriptive by default, with permission to deviate and an
obligation to say so. Being told not to think is expensive whenever the thing
you were given is wrong.

## A failure table must list every cause of a symptom

Bybit's table said an empty-bodied 401 means the signed bytes are wrong. It
also means a bad api key header. An agent that had made exactly that mistake
read the table, went to the signature, and stayed there.

For each symptom list every cause you know of, cheapest to check first. One
symptom mapped to one cause is a table that will confidently mislead. The same
mistake can also surface differently per endpoint — a bad Bybit key gives
`10003` on one and an empty 401 on another — so say that too.

## Public details are sent verbatim

`detail_fields` are values the agent transmits, not secrets. Masking one breaks
the call, and the venue names whatever it checks first — a signature on Bybit,
`invalid_credentials` on Deribit — rather than the field that was blanked.
`AGENTS.md` in dime-terminal carries this rule for every app; repeat it in a
skill only where the venue makes it easy to get wrong.

## Run it before merging

The checker reads structure. Whether an agent following the skill reaches the
venue is a separate question and only a live run answers it. Probe the edges —
empty payload, multi-parameter query, a public endpoint, a deliberate misuse
that should be refused.

## Changing a skill means bumping the version

`app_versions` is keyed `(app_id, version)` and written `ON CONFLICT DO
NOTHING`, so the first files ever installed under a version string are the ones
kept for good. Edit `SKILL.md` and leave `version:` alone and nobody who has
the app installed will ever see the change — reinstalling republishes the
pinned row, not what is in the store now.

So a skill change comes with a `version:` bump in `app.yaml`, every time. Then:
merge, wait for the refresh window, take the update the app now offers, and
re-test. Batch fixes rather than shipping a line at a time.

## Checks

```sh
python3 -m venv .venv && .venv/bin/pip install pyyaml pytest
.venv/bin/python scripts/check_structure.py
.venv/bin/python -m pytest scripts/test_check_structure.py -q
```
