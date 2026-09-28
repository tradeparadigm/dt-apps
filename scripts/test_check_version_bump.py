#!/usr/bin/env python3
"""check_version_bump.py, against real git repositories.

A synthetic repo per case, because the thing under test reads a merge base and
two blobs. Stubbing git would test the stub.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

CHECKER = str(Path(__file__).resolve().parent / "check_version_bump.py")
_p = _f = 0


def ok(cond, msg):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  {msg}")
    else:
        _f += 1
        print(f"  ✗   {msg}")


def git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, check=True)


def repo(version="1.0.0"):
    d = Path(tempfile.mkdtemp())
    git(d, "init", "-q", "-b", "main", ".")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    skill = d / "apps" / "demo" / "skills" / "demo-api"
    skill.mkdir(parents=True)
    (d / "apps" / "demo" / "app.yaml").write_text(f"id: demo\nversion: {version}\n")
    (skill / "SKILL.md").write_text("---\nname: demo-api\n---\nbody\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "base")
    git(d, "checkout", "-qb", "feature")
    return d


def run(d):
    return subprocess.run([sys.executable, CHECKER], cwd=d, capture_output=True, text=True,
                          env={"BASE_SHA": "main", "HEAD_SHA": "HEAD", "PATH": "/usr/bin:/bin:/usr/local/bin"})


def commit(d, msg="change"):
    git(d, "add", "-A")
    git(d, "commit", "-qm", msg)


# A skill changed and the version did not. This is the whole point.
d = repo()
(d / "apps" / "demo" / "skills" / "demo-api" / "SKILL.md").write_text("---\nname: demo-api\n---\nedited\n")
commit(d)
r = run(d)
ok(r.returncode == 1, "a changed skill with no version bump is refused")
ok("still 1.0.0" in r.stdout, "and it names the version that did not move")
ok("SKILL.md" in r.stdout, "and the file that did")

# The same edit with a bump.
d = repo()
(d / "apps" / "demo" / "skills" / "demo-api" / "SKILL.md").write_text("---\nname: demo-api\n---\nedited\n")
(d / "apps" / "demo" / "app.yaml").write_text("id: demo\nversion: 1.0.1\n")
commit(d)
ok(run(d).returncode == 0, "a changed skill with a bump passes")

# Only the manifest moved, so there is no skill edit to strand.
d = repo()
(d / "apps" / "demo" / "app.yaml").write_text("id: demo\nversion: 1.0.1\n")
commit(d)
ok(run(d).returncode == 0, "a manifest-only change passes")

# A brand new app has no earlier version to compare against.
d = repo()
fresh = d / "apps" / "fresh" / "skills" / "fresh-api"
fresh.mkdir(parents=True)
(d / "apps" / "fresh" / "app.yaml").write_text("id: fresh\nversion: 1.0.0\n")
(fresh / "SKILL.md").write_text("---\nname: fresh-api\n---\nx\n")
commit(d)
ok(run(d).returncode == 0, "a brand new app passes")

# Nothing under apps/ moved at all.
d = repo()
(d / "README.md").write_text("unrelated\n")
commit(d)
ok(run(d).returncode == 0, "a change outside apps/ passes")

# Two apps, one of them unbumped: the good one must not excuse the bad one.
d = repo()
other = d / "apps" / "other" / "skills" / "other-api"
other.mkdir(parents=True)
(d / "apps" / "other" / "app.yaml").write_text("id: other\nversion: 2.0.0\n")
(other / "SKILL.md").write_text("---\nname: other-api\n---\nx\n")
commit(d, "add the second app")
git(d, "checkout", "-q", "main")
git(d, "merge", "-q", "feature")
git(d, "checkout", "-qb", "f2")
(d / "apps" / "demo" / "skills" / "demo-api" / "SKILL.md").write_text("---\nname: demo-api\n---\nedited\n")
(other / "SKILL.md").write_text("---\nname: other-api\n---\nedited\n")
(d / "apps" / "other" / "app.yaml").write_text("id: other\nversion: 2.0.1\n")
commit(d, "bump one of two")
r = run(d)
ok(r.returncode == 1, "one bumped app does not excuse an unbumped one")
ok("demo:" in r.stdout and "other:" not in r.stdout, "and only the unbumped one is named")

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
