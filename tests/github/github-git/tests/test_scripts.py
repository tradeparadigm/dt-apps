"""Runs the shipped GitHub scripts.

api.sh runs against a fake curl that records its argv. setup.sh runs real git
in a throwaway HOME, with a fake curl answering /user. commit.mjs runs with
--dry-run against a real repository whose origin is a local bare repository.
"""

import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parents[4] / "apps/github/skills/github-git/scripts"

FAKE_CURL = """#!/bin/sh
python3 - "$@" <<'EOF'
import json, os, sys
argv = sys.argv[1:]
json.dump({"argv": argv}, open(os.environ["RECORD"], "w"))
if "-D" in argv:
    with open(argv[argv.index("-D") + 1], "w") as f:
        f.write("HTTP/2 200\\r\\ncontent-type: application/json\\r\\n"
                "Link: <https://api.github.com/x?page=2>; rel=\\"next\\"\\r\\n"
                "X-RateLimit-Reset: 1791191261\\r\\n\\r\\n")
    print('{"ok":true}')
    print("HTTP 200")
elif argv[-1].endswith("/user"):
    print('{"login":"octo","id":1}')
EOF
"""

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def run(script, env, home=None):
    args = env.pop("_ARGS", [])
    with tempfile.TemporaryDirectory() as d:
        bin_dir = pathlib.Path(d)
        exe = bin_dir / "curl"
        exe.write_text(FAKE_CURL)
        exe.chmod(0o755)
        record = bin_dir / "record.json"
        full_env = {"PATH": f"{bin_dir}:/usr/bin:/bin:{os.path.dirname(sys.executable)}",
                    "RECORD": str(record), "HOME": home or d, **env}
        proc = subprocess.run(["sh", str(SCRIPTS / script), *args], env=full_env,
                              capture_output=True, text=True)
        rec = json.loads(record.read_text()) if record.exists() else None
        return proc, rec


def refused(proc, rec, message):
    return proc.returncode == 2 and rec is None and message in proc.stderr


# setup.sh
def git_config(home, key):
    r = subprocess.run(["git", "config", "--global", "--get-all", key],
                       env={"HOME": home, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    return r.stdout.split("\n")[:-1]


with tempfile.TemporaryDirectory() as home:
    env = {"CRED_GITHUB_GIT_GIT": "cred-github-git-git-AAA", "CRED_GITHUB_GIT_GIT_META": "{}",
           "CRED_GITHUB_API_REST": "cred-github-api-rest-BBB"}
    proc, rec = run("setup.sh", dict(env), home)
    check("setup: runs both halves", proc.returncode == 0 and "configured: git gh" in proc.stdout)
    run("setup.sh", dict(env), home)
    headers = git_config(home, "http.https://github.com/.extraHeader")
    check("setup: a second run leaves one header", len(headers) == 1)
    if headers:
        check("setup: Basic carries x-access-token and the git placeholder",
              base64.b64decode(headers[0].rsplit(" ", 1)[1]).decode()
              == "x-access-token:cred-github-git-git-AAA")
    check("setup: git@ and ssh:// remotes go over HTTPS",
          git_config(home, "url.https://github.com/.insteadOf") == ["git@github.com:", "ssh://git@github.com/"])
    hosts = pathlib.Path(home, ".config/gh/hosts.yml")
    text = hosts.read_text() if hosts.exists() else ""
    check("setup: gh holds the REST placeholder", 'oauth_token: "cred-github-api-rest-BBB"' in text)
    check("setup: gh knows the login", 'user: "octo"' in text)
    check("setup: hosts.yml is private", hosts.exists() and hosts.stat().st_mode & 0o077 == 0)

with tempfile.TemporaryDirectory() as home:
    # The pod image links ~/.gitconfig and ~/.config/gh into the persistent
    # ~/.openclaw volume before either target exists.
    keep = pathlib.Path(home, ".openclaw")
    keep.mkdir()
    pathlib.Path(home, ".gitconfig").symlink_to(keep / "gitconfig")
    pathlib.Path(home, ".config").mkdir()
    pathlib.Path(home, ".config/gh").symlink_to(keep / "gh")
    proc, rec = run("setup.sh", {"CRED_GITHUB_GIT_GIT": "cred-a", "CRED_GITHUB_API_REST": "cred-b"}, home)
    check("setup: writes through links into the persistent volume",
          proc.returncode == 0 and (keep / "gitconfig").is_file() and (keep / "gh/hosts.yml").is_file()
          and pathlib.Path(home, ".gitconfig").is_symlink())

with tempfile.TemporaryDirectory() as home:
    proc, rec = run("setup.sh", {"CRED_GITHUB_API_REST": "cred-a"}, home)
    check("setup: REST alone configures only gh",
          proc.returncode == 0 and "configured: gh" in proc.stdout
          and git_config(home, "http.https://github.com/.extraHeader") == [])

proc, rec = run("setup.sh", {})
check("setup: no credential is refused", proc.returncode == 2 and "no GitHub credential" in proc.stderr)

proc, rec = run("setup.sh", {"CRED_GITHUB_GIT_GIT": "cred-a", "CRED_GITHUB_GIT": "cred-b"})
check("setup: two git credentials are refused", refused(proc, rec, "several GitHub GIT credentials"))

# api.sh
proc, rec = run("api.sh", {"CRED_GITHUB_API_REST": "cred-github-api-rest-BBB",
                           "CRED_GITHUB_API_REST_META": "{}",
                           "METHOD": "POST", "TARGET": "/repos/o/r/pulls",
                           "BODY": "@/etc/passwd"})
check("api: runs", proc.returncode == 0 and rec is not None)
if rec:
    argv = rec["argv"]
    check("api: bearer placeholder", "Authorization: Bearer cred-github-api-rest-BBB" in argv)
    check("api: url", argv[-1] == "https://api.github.com/repos/o/r/pulls")
    check("api: method", argv[argv.index("-X") + 1] == "POST")
    check("api: body is sent literally",
          "--data-raw" in argv and argv[argv.index("--data-raw") + 1] == "@/etc/passwd")
    check("api: body on stdout", '{"ok":true}' in proc.stdout and "HTTP 200" in proc.stdout)
    check("api: link and rate limit on stderr",
          proc.stderr.splitlines() == ['Link: <https://api.github.com/x?page=2>; rel="next"',
                                       "X-RateLimit-Reset: 1791191261"])

proc, rec = run("api.sh", {"CRED_GITHUB_API_REST": "cred-a", "TARGET": "/user"})
check("api: no BODY sends no data", rec is not None and "--data-raw" not in rec["argv"])

proc, rec = run("api.sh", {"CRED_GITHUB_API_REST": "cred-a", "TARGET": ".evil.com/x"})
check("api: TARGET without a leading slash is refused", refused(proc, rec, "starting with /"))

proc, rec = run("api.sh", {"TARGET": "/user"})
check("api: no credential is refused", refused(proc, rec, "no GitHub REST credential"))

proc, rec = run("api.sh", {"CRED_GITHUB_API_REST": "cred-a", "CRED_GITHUB_REST": "cred-b",
                           "TARGET": "/user"})
check("api: two credentials are refused", refused(proc, rec, "several GitHub REST credentials"))

proc, rec = run("api.sh", {"CRED_GITHUB_API_REST": "cred-a", "GITHUB_REST_CRED": "PATH",
                           "TARGET": "/user"})
check("api: override outside CRED_GITHUB is refused",
      refused(proc, rec, "GITHUB_REST_CRED must name a CRED_GITHUB"))

proc, rec = run("api.sh", {"GITHUB_REST_CRED": "CRED_GITHUB_API_REST", "TARGET": "/user"})
check("api: override naming an unset variable is refused", refused(proc, rec, "is not set"))


# commit.mjs
class Repo:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.env = {"HOME": str(self.root), "PATH": os.environ["PATH"],
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.git("init", "-q", "--bare", "-b", "main", str(self.root / "origin.git"))
        self.git("clone", "-q", str(self.root / "origin.git"), str(self.root / "work"))
        self.work = self.root / "work"

    def git(self, *a, cwd=None):
        return subprocess.run(["git", *a], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def w(self, *a):
        return self.git(*a, cwd=self.work)

    def write(self, path, text, mode=0o644):
        f = self.work / path
        f.write_text(text)
        f.chmod(mode)

    def commit(self, message):
        self.w("add", "-A")
        self.w("commit", "-q", "-m", message)

    def dry_run(self, *a):
        proc = subprocess.run(["node", str(SCRIPTS / "commit.mjs"), "--dry-run", "--repo", "o/r", *a],
                              cwd=self.work, env=self.env, capture_output=True, text=True)
        out = json.loads(proc.stdout) if proc.returncode == 0 else None
        return proc, out


with tempfile.TemporaryDirectory() as d:
    r = Repo(d)
    r.write("keep.txt", "keep\n")
    r.write("gone.txt", "gone\n")
    r.write("run.sh", "#!/bin/sh\n", 0o755)
    r.commit("base")
    r.w("push", "-q", "origin", "main")
    base = r.w("rev-parse", "HEAD")

    proc, out = r.dry_run()
    check("commit: nothing to push is refused",
          proc.returncode == 2 and "nothing to push" in proc.stderr)

    r.write("keep.txt", "keep\nmore\n")
    r.commit("first change")
    r.write("new.txt", "new\n")
    r.w("rm", "-q", "gone.txt")
    r.write("run.sh", "#!/bin/sh\necho hi\n", 0o755)
    r.commit("second change\n\nWhy it changed.")
    proc, out = r.dry_run()
    check("commit: dry run succeeds", out is not None)
    if out:
        i = out["input"]
        adds = {a["path"]: base64.b64decode(a["contents"]).decode() for a in i["fileChanges"]["additions"]}
        check("commit: expectedHeadOid is the remote head", i["expectedHeadOid"] == base)
        check("commit: additions carry HEAD's contents",
              adds == {"keep.txt": "keep\nmore\n", "new.txt": "new\n", "run.sh": "#!/bin/sh\necho hi\n"})
        check("commit: deletions", i["fileChanges"]["deletions"] == [{"path": "gone.txt"}])
        check("commit: headline is HEAD's subject", i["message"]["headline"] == "second change")
        check("commit: body keeps HEAD's body and lists every commit",
              i["message"].get("body") == "Why it changed.\n\nIncludes:\n- first change\n- second change")
        check("commit: an existing branch is not created", out["createBranchAt"] is None)

    r.w("switch", "-q", "-c", "feature")
    r.write("f.txt", "f\n")
    r.commit("feature work")
    proc, out = r.dry_run()
    check("commit: a branch missing remotely starts at the merge base with the default branch",
          out is not None and out["createBranchAt"] == base and out["input"]["expectedHeadOid"] == base)

    r.write("tool.sh", "#!/bin/sh\n", 0o755)
    r.commit("new tool")
    proc, out = r.dry_run()
    check("commit: a new executable is refused",
          proc.returncode == 2 and "tool.sh is a new executable file" in proc.stderr)

with tempfile.TemporaryDirectory() as d:
    r = Repo(d)
    r.write("a.txt", "a\n")
    r.commit("base")
    r.w("push", "-q", "origin", "main")
    other = pathlib.Path(d, "other")
    r.git("clone", "-q", str(r.root / "origin.git"), str(other))
    (other / "b.txt").write_text("b\n")
    r.git("add", "-A", cwd=other)
    r.git("commit", "-q", "-m", "someone else", cwd=other)
    r.git("push", "-q", "origin", "main", cwd=other)
    r.write("a.txt", "mine\n")
    r.commit("mine")
    proc, out = r.dry_run()
    check("commit: a branch behind its remote is refused",
          proc.returncode == 2 and "has commits this branch does not" in proc.stderr)

for name in failed:
    print(f"FAIL {name}")
print(f"{passed} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
