# dt-apps

The apps DIME Terminal offers. This repository is the store.

An **app** is a venue integration a user installs: a credential they enrol,
the hosts and routes that credential may be used on, and a skill teaching an
agent how to use it. All three live here, in one directory per app.

```
apps/<id>/app.yaml                        what the app is, and what its
                                          credential may reach
apps/<id>/skills/<skill-name>/SKILL.md    what the agent is taught
apps/<id>/skills/<skill-name>/...         anything else that skill reads
```

**Publishing does not require a release of DIME Terminal.** It reads this
repository at run time and refreshes on an interval, so a merged pull request
here is live within that window — an environment follows a ref (`main` on
testnet, a tag in production), and moving it is a config value rather than a
build.

## The manifest names no skills

The id is the join and the directory is the content. There is no index to keep
in step with the filesystem, and "how many skills does this app have" is
answered by listing a directory.

One skill per app today, for a reason that is not about this file: the agent's
publish path writes an app's files under `<skills>/apps/<id>/` and discovers a
skill by `SKILL.md` at that root, so a second has nowhere to go until that path
takes nested ones. CI refuses a second rather than letting one be silently
dropped.

## Naming

A skill is named `<venue>-<surface>`: `bybit-api` is Bybit's REST surface.

The bare venue name is deliberately left free. `bybit` belongs to whoever
writes a skill called that — including a user's own — and the app id already
carries the venue's identity, so the skill file does not need to claim it.

## Versioning

`version:` in `apps/<id>/app.yaml` is the app's version, and it is the only
one. DIME Terminal records it when a user installs and compares it to offer an
update, so **a change that does not bump it reaches nobody**: existing installs
keep the version they have until they take a new one.

## What CI checks, and why it is here

DIME Terminal fetches this repository while it is running. A malformed app is
therefore not caught by anyone's build — it is refused from a live catalogue,
with a log line nobody reads. So the checks happen against the pull request
that made the mistake:

- the manifest's `id` matches its directory, and carries what the catalogue
  renders — version, name, blurb, description, an environment, a credential
  type;
- every environment and credential type carries what the reader requires of it
  — `id`, `label`, `host`; `id`, `label`, `slug`, `secret_label`, `summary` —
  with ids that are well formed as well as present and unique, and
  `detail_fields` that carry a usable key and a label. These were checked only
  on the reading side until review found the split: a manifest missing a
  `secret_label` went green here and was dropped from the live catalogue on the
  next refresh, which is the failure this repository exists to move left;
- every app states its scope on two axes — `access` (`read-only` or
  `read-write`) and `maturity` (`stable` or `beta`). **Required here, optional
  in the consumer**, and that difference is deliberate: the consumer defaults
  an absent value so apps written before these fields existed keep working,
  but the default is the permissive pair, so an author who left them out here
  would publish a writable, stable-looking app without having said so;
- a credential type narrows itself to no more endpoints than the API accepts;
- every key is one the consumer knows, because its decoder runs with
  `KnownFields` and an unknown key refuses the whole app rather than being
  ignored — a misspelled key silently means the default, and every default is
  wider than what the author wrote;
- a delivery carries only the fields its mode uses. The consumer compares what
  a manifest declared against what canonicalising it produced and refuses a
  difference, because a field silently dropped is a line a reviewer approved
  that does not run;
- optional branding — `developer`, `developer_url`, `tint`, `icon` — is safe to
  put on a page. A tint is a six-digit hex because it lands in a style
  attribute, and an icon is **one SVG path** rather than a file: a path is
  geometry, so it cannot execute, fetch, or escape the box it is drawn into.
  Half an icon is refused rather than defaulted;
- no two apps claim the same skill name. openclaw resolves a collision by
  precedence rather than erroring, so one of them would simply never load;
- hosts are bare lowercase hostnames — no scheme, port, path or underscore —
  because the proxy matches them case-sensitively and the enrolment path
  refuses anything that is not a hostname outright;
- routes carry a path the API accepts and methods from the closed set, already
  upper case and without duplicates: the consumer canonicalises and refuses any
  difference, so `get` and a repeated path are refusals rather than tidy-ups;
- delivery modes, signing schemes, signature encodings and key encodings are
  ones the proxy has a case for. **A manifest may describe an app; it cannot
  invent a capability** — a scheme outside that list would be accepted and then
  never signed. A key encoding says how the stored key is READ before signing
  with it, which the signature encoding does not cover, and only the MAC scheme
  reads one;
- slugs can be credential labels, so a template cannot produce a credential
  nobody can enrol;
- every skill directory holds a `SKILL.md` whose frontmatter `name` matches
  it, with a description, inside the file-count and size limits.

A refusal still happens on the reading side too. Nothing here is trusted
because it passed CI here.

## Adding an app

1. `apps/<id>/app.yaml` — start from a venue whose signing scheme resembles
   yours; `bybit` for an HMAC in a header, `paradex` for a signing key.
2. `apps/<id>/skills/<id>-api/SKILL.md`, frontmatter `name` matching the
   directory.
3. Open a pull request. Merging publishes it.

Get the hosts right. That is the field with consequences: it is where a
decrypted key may be sent.

Write the skill for an agent that does not know whether a proxy is in front of
it — say what to sign and where the signature goes, not who produces it. That
is what makes a skill here replaceable by one the venue writes itself.

## Where this disagrees with the server

`scripts/check_structure.py` is a second implementation of rules the server
already has, in another language in another repository. Nothing enforces that
the two agree, and they have drifted seven times so far — a `developer_url`
accepted here and refused there, a whitespace class wider in Python than in
Go, an empty field read as absent on one side only, and a signing scheme the
server had learned and this had not.

One divergence is deliberate and is marked as such in both places: `access`
and `maturity` are required here and optional there. Everything else should
agree.

Drift in either direction costs something. Laxer here and an app merges and
then goes missing from the catalogue, because the server drops what it cannot
load rather than failing. Stricter here and a legitimate app is blocked for
no reason.

So when you change a rule, change the comment that names its counterpart, and
add a case to `scripts/test_check_structure.py` — the branding rules and the
signing branch both went unexercised until a real app tripped them.

The fix is to stop having two. The server's loader is a parser for a public
schema describing public APIs; nothing in it is private. Once it lives in a
module this repository can import, CI runs the loader itself and disagreement
stops being possible. Until then, this file is the copy and the server is the
original.

## What still needs a change to DIME Terminal

A signing scheme or delivery mode the proxy has no case for, or a bump to
`schema_version`. Everything else is a pull request here.
