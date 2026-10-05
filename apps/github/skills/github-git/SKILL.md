---
name: github-git
description: >
  Git over HTTPS and the GitHub REST API using a fine-grained personal access
  token held by the DIME credential proxy. Covers cloning, fetching, pulling
  and pushing private and public repositories on github.com, and calling
  api.github.com for issues, pull requests, repository contents and the
  signed-in user. Use for ANY git operation against github.com and ANY request
  to api.github.com, including "clone my repo", "push this branch", "open a
  pull request", "list my GitHub issues", or a 403 from the credential proxy on
  a GitHub call. Read this BEFORE running git against github.com: a plain git
  command is refused by the proxy once this credential is enrolled.
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
| `CRED_GITHUB_GIT_GIT` | `github.com` | `scripts/git.sh` |
| `CRED_GITHUB_API_REST` | `api.github.com` | `scripts/api.sh` |

The names depend on what the user picked at enrolment. The scripts take any
`CRED_GITHUB…_GIT` and `CRED_GITHUB…_REST` variable. If only one of the two is
there, only that half works. Tell the user which one is missing.

`gh` is not installed. Do not install it.

## Git

Run git through the script beside this file, in place of `git`:

```sh
sh scripts/git.sh clone https://github.com/OWNER/REPO.git
sh scripts/git.sh -C REPO push origin my-branch
sh scripts/git.sh -C REPO pull
```

Give the script's absolute path when you run it from another directory. It
takes the same arguments as `git`.

The script sends the placeholder as Basic auth on every request to
github.com. Plain `git` does not: it sends nothing until GitHub answers 401,
and the proxy answers an unauthenticated fetch or push with a 403 first, so
git never retries. Never put the placeholder in a remote URL or in a stored
git config. The script adds it per command.

Use HTTPS remotes. SSH does not go through the proxy and there is no key.
Git LFS objects are not covered.

Commits need an author. If `git commit` complains, set one for the repository
with `git config user.name` and `git config user.email`, using what the user
tells you. Plain `git` is fine for local commands. Only commands that reach
github.com need the script.

## REST API

```sh
TARGET=/user sh scripts/api.sh
TARGET='/repos/OWNER/REPO/pulls?state=open' sh scripts/api.sh
METHOD=POST TARGET=/repos/OWNER/REPO/pulls \
  BODY='{"title":"…","head":"my-branch","base":"main","body":"…"}' sh scripts/api.sh
```

The script prints the response body and then `HTTP <status>` on its own line.
`TARGET` is the path and query, without the host.

## Never edit the scripts

A publish replaces them. If one does not work, change the smallest thing
that makes your command run, run it, and tell the user in one line what you
changed, so it can be fixed here.

## When a call fails

| What you see | Causes, cheapest to check first |
|---|---|
| 403 from the proxy, body says `placeholder_absent` | You ran plain `git` or `curl` in place of the script. Or `GITHUB_GIT_CRED` or `GITHUB_REST_CRED` names the wrong variable. Or the REST credential was enrolled on the Git environment, so every github.com page now needs the placeholder; ask the user to re-enrol it as Git. |
| `could not read Username for 'https://github.com': terminal prompts disabled` from git, or 401 `Bad credentials` from the API | The token is expired or revoked. Or the credential was enrolled with the other type, so the proxy never swaps it and GitHub sees the placeholder itself. Or it was enrolled on the wrong environment: git needs `CRED_GITHUB_GIT_GIT` and the API needs `CRED_GITHUB_API_REST`, and `CRED_GITHUB_API_GIT` or `CRED_GITHUB_GIT_REST` never works. |
| `Repository not found` from git, or 404 from the API on a repository you know exists | The token was not given that repository. GitHub answers 404 for a private repository the token cannot see. |
| 403 on push, `Permission to OWNER/REPO denied` | The token has read access only. It needs Contents: read and write. |
| 403 `Resource not accessible by personal access token` | The token lacks the permission for that endpoint, such as Pull requests or Issues. |
| 403 with `rate limit` in the body | GitHub's rate limit. The `x-ratelimit-reset` header gives the reset time. |
| `no GitHub … credential in the environment` from a script | The user has not enrolled that half, or enrolled it on a custom environment under another name. Run `env \| grep '^CRED_GITHUB'` and pass the right one as `GITHUB_GIT_CRED` or `GITHUB_REST_CRED`. |
| `several GitHub … credentials` from a script | More than one matches. Pick the one for the right host, `CRED_GITHUB_GIT_GIT` or `CRED_GITHUB_API_REST`, and pass it as `GITHUB_GIT_CRED` or `GITHUB_REST_CRED`. |

Report a permission failure to the user with the permission it needs. Do not
work around it.
