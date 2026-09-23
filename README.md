# dt-apps

Skill content for DIME Terminal apps.

An **app** is a venue integration a user installs: a credential they enrol, the
hosts and routes that credential may be used on, and instructions telling an
agent how to use it. This repository holds the last of those three. The other
two live in DIME Terminal itself, beside the code that enforces them.

| | Owned here | Owned in dime-terminal |
|---|---|---|
| What a venue knows — endpoints, parameters, instrument naming, error codes | ✅ | |
| Where a credential may be sent — hosts, routes, delivery mode, signing scheme | | `api/terminal/pkg/apps/bundles/<id>/app.yaml` |
| How the credential proxy works — placeholders, signing headers, what a 403 means | | the agent's `AGENTS.md`, injected once for every app |

An app id here is an app id there. `binance` is the exception for now: its
skill is ready and its credential surface is still on a branch, so the content
ships ahead of anything installable.

The split is deliberate. A pull request here changes what an agent is told; it
cannot change where anyone's secret is allowed to go.

The skills as first imported do not yet respect the third row: four of the five
still restate the proxy's placeholder and signing-header rules inline, because
they were moved verbatim rather than moved and rewritten at once. Thinning them
back to venue knowledge is a per-skill pass, and it is the same pass that makes
a skill here replaceable by one the venue writes itself.

## Layout

```
apps/<id>/skills.yaml                     the app's version and its skill list
apps/<id>/skills/<skill-name>/SKILL.md    one skill
apps/<id>/skills/<skill-name>/...         anything else that skill reads
```

`<id>` is the app id DIME Terminal knows the venue by — `bybit`, `okx` — and
`<skill-name>` is the name in the skill's own frontmatter. They must agree, and
the directory name must equal the frontmatter `name`.

## Naming

A skill is named `<venue>-<surface>`: `bybit-api` is Bybit's REST surface.

The bare venue name is deliberately left free. `bybit` belongs to whoever
writes a skill called that — including a user's own — and the app id already
carries the venue's identity, so the skill file does not need to claim it.

## Versioning

**The version is not here.** An app's version lives with its manifest in
dime-terminal (`bundles/<id>/app.yaml`), because that is what a user installs
and what the install is recorded against — a version in this repo would be a
second number, and the two would drift.

What makes a change here reach anyone is the **pin bump** in dime-terminal's
`go.mod`. That side's catalogue test hashes the skill files it embeds, so a
pin bump that changes this text and does not bump the app's version fails
there, in the pull request that does the bumping.

Neither file carries a version in a skill's frontmatter, for the same reason.

## What CI checks here

`scripts/check_structure.py` checks the shape dime-terminal refuses to start
on, so a mistake fails in this repo rather than in someone else's deploy: the
manifest's `id` matches its directory, every listed skill has a directory with
a `SKILL.md`, every directory is listed, each frontmatter `name` matches its
directory and carries a description, and the file count and sizes are inside
the consumer's limits.

## Adding a skill

1. `apps/<id>/skills/<venue>-<surface>/SKILL.md`, frontmatter `name` matching
   the directory.
2. Add the name to `skills:` in that app's `skills.yaml`.
3. Open a pull request. Merging it changes nothing on its own — the change
   ships when dime-terminal bumps its pin, and bumps the app's version there.

Write for an agent that does not know whether a proxy is in front of it: say
what to sign and where the signature goes, not who produces it. That is what
makes a skill here replaceable by one the venue writes itself.
