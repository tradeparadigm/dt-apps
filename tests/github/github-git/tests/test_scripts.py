"""Runs the shipped git.sh and api.sh against fake git and curl binaries.

Each fake records the argv and environment it was given, so the checks read
what the agent's command would actually send.
"""

import base64
import json
import pathlib
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parents[4] / "apps/github/skills/github-git/scripts"

FAKE_GIT = """#!/bin/sh
python3 - "$@" <<'EOF'
import json, os, sys
json.dump({"argv": sys.argv[1:], "prompt": os.environ.get("GIT_TERMINAL_PROMPT")},
          open(os.environ["RECORD"], "w"))
EOF
"""

FAKE_CURL = """#!/bin/sh
python3 - "$@" <<'EOF'
import json, os, sys
argv = sys.argv[1:]
json.dump({"argv": argv}, open(os.environ["RECORD"], "w"))
with open(argv[argv.index("-D") + 1], "w") as f:
    f.write("HTTP/2 200\\r\\ncontent-type: application/json\\r\\n"
            "Link: <https://api.github.com/x?page=2>; rel=\\"next\\"\\r\\n"
            "X-RateLimit-Reset: 1791191261\\r\\n\\r\\n")
print('{"ok":true}')
print("HTTP 200")
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


def run(script, env):
    args = env.pop("_ARGS", [])
    with tempfile.TemporaryDirectory() as d:
        bin_dir = pathlib.Path(d)
        for name, body in (("git", FAKE_GIT), ("curl", FAKE_CURL)):
            exe = bin_dir / name
            exe.write_text(body)
            exe.chmod(0o755)
        record = bin_dir / "record.json"
        full_env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RECORD": str(record), **env}
        proc = subprocess.run(["sh", str(SCRIPTS / script), *args], env=full_env,
                              capture_output=True, text=True)
        rec = json.loads(record.read_text()) if record.exists() else None
        return proc, rec


def refused(proc, rec, message):
    return proc.returncode == 2 and rec is None and message in proc.stderr


def basic_user(argv):
    header = next(a for a in argv if "extraHeader=" in a)
    return base64.b64decode(header.rsplit(" ", 1)[1]).decode()


# git.sh
proc, rec = run("git.sh", {"CRED_GITHUB_GIT_GIT": "cred-github-git-git-AAA",
                           "CRED_GITHUB_GIT_GIT_META": "{}",
                           "_ARGS": ["ls-remote", "https://github.com/o/r.git"]})
check("git: runs", proc.returncode == 0 and rec is not None)
if rec:
    check("git: Basic carries x-access-token and the placeholder",
          basic_user(rec["argv"]) == "x-access-token:cred-github-git-git-AAA")
    check("git: header is scoped to github.com",
          any(a.startswith("http.https://github.com/.extraHeader=Authorization: Basic ")
              for a in rec["argv"]))
    check("git: credential helper is cleared", "credential.helper=" in rec["argv"])
    check("git: prompts are off", rec["prompt"] == "0")
    check("git: arguments pass through", rec["argv"][-2:] == ["ls-remote", "https://github.com/o/r.git"])

proc, rec = run("git.sh", {"_ARGS": ["--version"]})
check("git: no credential is refused", refused(proc, rec, "no GitHub git credential"))

proc, rec = run("git.sh", {"CRED_GITHUB_GIT_GIT": "cred-a", "CRED_GITHUB_GIT": "cred-b"})
check("git: two credentials are refused", refused(proc, rec, "several GitHub git credentials"))

proc, rec = run("git.sh", {"CRED_GITHUB_GIT_GIT": "cred-a", "CRED_GITHUB_GIT": "cred-b",
                           "GITHUB_GIT_CRED": "CRED_GITHUB_GIT", "_ARGS": ["status"]})
check("git: override picks the named credential",
      proc.returncode == 0 and rec and basic_user(rec["argv"]) == "x-access-token:cred-b")

proc, rec = run("git.sh", {"CRED_GITHUB_GIT_GIT": "cred-a", "HOME": "/home/node",
                           "GITHUB_GIT_CRED": "HOME"})
check("git: override outside CRED_GITHUB is refused",
      refused(proc, rec, "GITHUB_GIT_CRED must name a CRED_GITHUB"))

proc, rec = run("git.sh", {"GITHUB_GIT_CRED": "CRED_GITHUB_GIT_GIT"})
check("git: override naming an unset variable is refused", refused(proc, rec, "is not set"))

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

for name in failed:
    print(f"FAIL {name}")
print(f"{passed} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
