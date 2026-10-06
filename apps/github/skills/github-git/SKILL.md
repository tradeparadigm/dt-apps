---
name: github-git
description: >
  Git, gh and the GitHub REST and GraphQL APIs using a fine-grained personal
  access token held by the DIME credential proxy. Covers cloning, fetching,
  pulling and pushing over HTTPS or git@ remotes, gh commands such as gh pr
  create and gh api, Verified commits, and calls to api.github.com. Use for ANY
  git operation against github.com, ANY gh command and ANY request to
  api.github.com, including "clone my repo", "push this branch", "open a pull
  request", "make a signed commit", "list my GitHub issues", or a 403 from the
  credential proxy on a GitHub call. Read this BEFORE the first GitHub command
  in a chat: one setup script has to run first.
metadata:
  author: tradeparadigm
---

# GitHub

Your `AGENTS.md` already explains the placeholder mechanism in general. This
file covers only what is specific to GitHub.

## What you hold

The user enrols the same fine-grained personal access token twice, because git
and the API are on different hosts:

| Variable | Host | Used by |
|---|---|---|
| `CRED_GITHUB_GIT_GIT` | `github.com` | git, `scripts/commit.mjs` |
| `CRED_GITHUB_API_REST` | `api.github.com` | `gh`, `scripts/api.sh`, `scripts/commit.mjs` |

The names depend on what the user picked at enrolment. The scripts take any
`CRED_GITHUB…_GIT` and `CRED_GITHUB…_REST` variable. If only one is there,
only that half works. Tell the user which one is missing.

## Run setup first

Before the first GitHub command in a chat, run the script beside this file:

```sh
sh scripts/setup.sh
```

It prints `configured:` and the halves it set up. It is safe to run again, and
you must run it again after the user re-enrols a token, because the
placeholder changes. It writes:

- a git config entry that sends the git placeholder as Basic auth on every
  request to github.com. The proxy refuses a fetch or push without it, and
  plain git would not send it until GitHub asked;
- `insteadOf` rules, so `git@github.com:` and `ssh://git@github.com/` remotes
  go over HTTPS. SSH itself does not go through the proxy;
- `gh`'s `hosts.yml`, holding the REST placeholder.

Everything it writes is a placeholder, so none of it is secret.

## Git

After setup, use plain `git` with any remote form. Commits need an author: if
`git commit` complains, set `user.name` and `user.email` with what the user
tells you.

Git LFS objects are not covered.

## gh

After setup, `gh` works as normal: `gh pr create`, `gh pr list`, `gh issue
view`, `gh api`, `gh repo clone`. If `gh` is not installed, call the API with
the script:

```sh
TARGET=/user sh scripts/api.sh
TARGET='/repos/OWNER/REPO/pulls?state=open' sh scripts/api.sh
METHOD=POST TARGET=/repos/OWNER/REPO/pulls \
  BODY='{"title":"…","head":"my-branch","base":"main","body":"…"}' sh scripts/api.sh
```

`api.sh` prints the response body, then `HTTP <status>`. On stderr it prints
the `link` header, which holds the next page's URL, and the `x-ratelimit-*`
headers.

## Verified commits

`git push` sends commits unsigned. To push commits that GitHub shows as
Verified, commit locally as normal, then run this from inside the repository
in place of `git push`:

```sh
node scripts/commit.mjs
node scripts/commit.mjs --dry-run      # print what it would send
node scripts/commit.mjs --base main    # branch not on GitHub yet, made from main
```

It reads the repository from the `origin` remote. Pass `--remote NAME` for
another remote, or `--repo OWNER/NAME` when the remote URL is not github.com.
It needs both halves: git to fetch, and the API to commit.

It sends every local commit ahead of the remote branch as ONE commit through
GitHub's API. GitHub signs it, and the user is the author. Then it moves your
local branch onto that commit, so `git status` is clean.

It refuses:

- when the remote branch has commits yours does not. Pull or rebase first;
- symlinks, submodules, new executable files and mode changes. Push those
  with plain `git push` and tell the user that commit is not Verified.

The token needs Contents: read and write on the repository.

## Never edit the scripts

A publish replaces them. If one does not work, change the smallest thing
that makes your command run, run it, and tell the user in one line what you
changed, so it can be fixed here.

## When a call fails

| What you see | Causes, cheapest to check first |
|---|---|
| 403 from the proxy, body says `placeholder_absent` | Setup has not run in this pod, or ran before the git credential existed. Run `sh scripts/setup.sh`. Or the REST credential was enrolled on the Git environment, so every github.com page now needs the placeholder; ask the user to re-enrol it as Git. |
| `could not read Username for 'https://github.com'` from git, 401 `Bad credentials` from `gh` or the API | The user re-enrolled and the config holds the old placeholder: run setup again. Or the token is expired or revoked. Or it was enrolled on the wrong environment: git needs `CRED_GITHUB_GIT_GIT` and the API needs `CRED_GITHUB_API_REST`, and `CRED_GITHUB_API_GIT` or `CRED_GITHUB_GIT_REST` never works. |
| `Repository not found` from git, or 404 on a repository you know exists | The token was not given that repository. GitHub answers 404 for a private repository the token cannot see. |
| 403 on push, `Permission to OWNER/REPO denied`, or `commit.mjs` gets a permission error | The token has read access only. It needs Contents: read and write. |
| 403 `Resource not accessible by personal access token` | The token lacks the permission for that endpoint, such as Pull requests or Issues. |
| 403 with `rate limit` in the body | GitHub's rate limit. `x-ratelimit-reset` gives the reset time. |
| `commit.mjs` says `expectedHeadOid` does not match | Someone pushed to the branch since your fetch. Pull or rebase, then run it again. |
| `no GitHub … credential in the environment` from a script | The user has not enrolled that half, or enrolled it on a custom environment under another name. Run `env \| grep '^CRED_GITHUB'` and pass the right one as `GITHUB_GIT_CRED` or `GITHUB_REST_CRED`. |
| `several GitHub … credentials` from a script | More than one matches. Pass the one for the right host, `CRED_GITHUB_GIT_GIT` or `CRED_GITHUB_API_REST`, as `GITHUB_GIT_CRED` or `GITHUB_REST_CRED`. |

Report a permission failure to the user with the permission it needs. Do not
work around it.
