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

`version:` in `apps/<id>/skills.yaml` is the app's version. DIME Terminal
records it when a user installs, and compares it to offer an update, so a
content change that does not bump it reaches nobody.

It is the only version. A skill's frontmatter carries no version of its own,
because a second number is one that drifts and nothing reads.

CI requires a bump whenever anything under `apps/<id>/` changes, and requires
it to be one clean step — `1.0.0` → `1.0.1`, `1.1.0` or `2.0.0`. A jump of two,
a multi-component change, and a decrease all fail.

## Adding a skill

1. `apps/<id>/skills/<venue>-<surface>/SKILL.md`, frontmatter `name` matching
   the directory.
2. Add the name to `skills:` in that app's `skills.yaml`, and bump `version:`.
3. Open a pull request.

Write for an agent that does not know whether a proxy is in front of it: say
what to sign and where the signature goes, not who produces it. That is what
makes a skill here replaceable by one the venue writes itself.
