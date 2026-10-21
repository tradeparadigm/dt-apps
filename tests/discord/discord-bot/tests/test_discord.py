"""The Discord client: what it sends, with fetch stubbed in node.

Each case runs the real module, or its CLI, and records every request it
makes, so the checks are on what Discord would receive. Every request is also
checked against the routes in app.yaml, matched the way the credential proxy
matches them. Nothing reaches Discord.
"""

import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]
CLIENT = ROOT / "apps/discord/skills/discord-bot/scripts/discord.mjs"
MANIFEST = ROOT / "apps/discord/app.yaml"

# Replies are queued per case; past the queue every request gets a 200.
RUN = r"""
import { pathToFileURL } from 'node:url';
const [file, raw] = process.argv.slice(1);
const scenario = JSON.parse(raw);
const sent = [];
const replies = [...(scenario.replies ?? [])];
globalThis.fetch = async (url, init) => {
  const req = { url, method: init.method, headers: init.headers ?? {} };
  if (init.body instanceof FormData) {
    const f = init.body.get('files[0]');
    req.form = { payload: JSON.parse(init.body.get('payload_json')), file: f.name, text: await f.text() };
  } else if (init.body !== undefined) {
    req.body = JSON.parse(init.body);
  }
  sent.push(req);
  const r = replies.shift() ?? { status: 200, body: { id: String(sent.length) } };
  const text = typeof r.body === 'string' ? r.body : JSON.stringify(r.body);
  return new Response(r.status === 204 ? null : text, { status: r.status });
};
let result, error;
process.on('exit', () => console.log('@@' + JSON.stringify({ sent, result, error })));
if (scenario.cli) {
  process.argv = [process.argv[0], file, ...scenario.cli];
  await import(pathToFileURL(file).href);
} else {
  // With the file in argv[1], importing it would run its CLI.
  process.argv = [process.argv[0]];
  const mod = await import(pathToFileURL(file).href);
  try {
    result = await mod[scenario.fn](...scenario.args);
  } catch (err) {
    error = { message: err.message, retryAfter: err.retryAfter };
  }
}
"""

CRED = "cred-discord-api-bot-PH"
CHANNEL = "123456789012345678"
MESSAGE = "223456789012345678"
USER = "323456789012345678"

passed = 0
failed = []
seen = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def run(scenario, env=None):
    base = {"PATH": os.environ["PATH"]}
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", RUN, str(CLIENT), json.dumps(scenario)],
        capture_output=True, text=True, env={**base, "CRED_DISCORD_API_BOT": CRED, **(env or {})},
    )
    got = json.loads(next(line[2:] for line in proc.stdout.splitlines() if line.startswith("@@")))
    got["exit"] = proc.returncode
    seen.extend(got["sent"])
    return got


def call(fn, *args, replies=None, env=None):
    return run({"fn": fn, "args": list(args), "replies": replies or []}, env)


def texts(got):
    return [r["body"]["content"] for r in got["sent"]]


# The proxy's matcher: a trailing "/*" is a prefix, anything else is Go's
# path.Match, where "*" never crosses a "/".
def matches(pattern, path):
    if re.search(r"[?\[\\]", pattern):
        raise ValueError(f"{pattern!r} uses glob syntax this mirror does not implement")
    if pattern.endswith("/*"):
        return path.startswith(pattern[:-1]) or path == pattern[:-2]
    regex = "".join("[^/]*" if part == "*" else re.escape(part) for part in re.split(r"(\*)", pattern))
    return re.fullmatch(regex, path) is not None


def routes():
    found = []
    for path, methods in re.findall(r"- path: (\S+)\n\s+methods: \[([^\]]*)\]", MANIFEST.read_text()):
        found.append((path, [m.strip() for m in methods.split(",")]))
    return found


# --- sending ------------------------------------------------------------

got = call("sendMessage", CHANNEL, "hello")
req = got["sent"][0]
check("a short message is one POST to the channel",
      len(got["sent"]) == 1 and req["method"] == "POST"
      and req["url"] == f"https://discord.com/api/v10/channels/{CHANNEL}/messages")
check("the placeholder goes in the Authorization header as a bot token",
      req["headers"].get("Authorization") == f"Bot {CRED}")
check("the User-Agent has the shape Discord requires of a bot",
      req["headers"].get("User-Agent", "").startswith("DiscordBot ("))
check("mentions do not ping by default", req["body"]["allowed_mentions"] == {"parse": []})
check("no flags unless silent", "flags" not in req["body"])

got = call("sendMessage", CHANNEL, "@everyone up", {"mention": True, "silent": True})
check("mention lets everyone, roles and users ping",
      sorted(got["sent"][0]["body"]["allowed_mentions"]["parse"]) == ["everyone", "roles", "users"])
check("silent sets SUPPRESS_NOTIFICATIONS", got["sent"][0]["body"].get("flags") == 4096)

got = call("sendMessage", CHANNEL, " \n ")
check("blank text is refused before anything is sent",
      got["sent"] == [] and got["error"]["message"] == "nothing to send")

for fn, args in [
    ("sendMessage", ["general", "hi"]),
    ("sendFile", ["general", str(MANIFEST)]),
    ("recentMessages", ["general"]),
    ("editMessage", [CHANNEL, "../../guilds/1", "hi"]),
    ("deleteMessage", [CHANNEL, "abc"]),
    ("openDm", ["sam"]),
]:
    got = call(fn, *args)
    check(f"{fn} refuses an id that is not a Discord id before anything is sent",
          got["sent"] == [] and "must be a Discord id" in got["error"]["message"])

got = call("editMessage", CHANNEL, MESSAGE, "@everyone fixed")
check("an edit does not ping either",
      got["sent"][0]["body"] == {"content": "@everyone fixed", "allowed_mentions": {"parse": []}})

got = call("editMessage", CHANNEL, MESSAGE, "x" * 2001)
check("an edit over 2000 characters is refused before anything is sent",
      got["sent"] == [] and "edit text must be" in got["error"]["message"])

# --- splitting at 2000 ----------------------------------------------------

got = call("sendMessage", CHANNEL, "x" * 1999 + "\U0001F600" + "y")
check("an emoji that would cross 2000 UTF-16 units moves whole to the next message",
      texts(got) == ["x" * 1999, "\U0001F600y"])

line = "a" * 1500 + "\n"
got = call("sendMessage", CHANNEL, line * 3)
check("long text splits at line breaks", texts(got) == ["a" * 1500, "a" * 1500, "a" * 1500 + "\n"])

got = call("sendMessage", CHANNEL, "a\n" + "x" * 1998)
check("exactly 2000 characters is one message", texts(got) == ["a\n" + "x" * 1998])

got = call("sendMessage", CHANNEL, "a\n" + "x" * 1998 + "\ny")
check("a newline right at the limit is where it cuts", texts(got) == ["a\n" + "x" * 1998, "y"])

got = call("sendMessage", CHANNEL, "\n" + "x" * 2500)
check("a newline at a cut starts no message", texts(got) == ["x" * 2000, "x" * 500])

got = call("sendMessage", CHANNEL, "x" * 2000 + "\n")
check("a trailing newline past the limit sends no empty message", texts(got) == ["x" * 2000])

# --- failures -------------------------------------------------------------

got = call("sendMessage", CHANNEL, "a" * 1500 + "\n" + "b" * 1500,
           replies=[{"status": 200, "body": {"id": "1"}},
                    {"status": 429, "body": {"message": "You are being rate limited.", "retry_after": 1.5}}])
check("a 429 stops the send and carries the wait",
      len(got["sent"]) == 2 and got["error"]["retryAfter"] == 1.5
      and "retry after 1.5s" in got["error"]["message"])

got = call("sendMessage", CHANNEL, "hi", replies=[{"status": 403, "body": {"message": "Missing Access", "code": 50001}}])
check("a Discord error names its status, message and code",
      got["error"]["message"] == f"POST /channels/{CHANNEL}/messages: 403 Missing Access (code 50001)")

got = call("sendMessage", CHANNEL, "hi", replies=[{"status": 502, "body": "<html>Bad Gateway</html>"}])
check("a body that is not JSON still gives the status",
      "HTTP 502" in got["error"]["message"])

# --- files ----------------------------------------------------------------

got = call("sendFile", CHANNEL, str(MANIFEST), {"caption": "the manifest", "silent": True})
req = got["sent"][0]
check("a file goes as multipart with payload_json and files[0]",
      req["form"]["file"] == "app.yaml" and req["form"]["text"] == MANIFEST.read_text()
      and req["form"]["payload"]["content"] == "the manifest"
      and req["form"]["payload"]["attachments"] == [{"id": 0, "filename": "app.yaml"}]
      and req["form"]["payload"].get("flags") == 4096)
check("multipart sets no Content-Type, so fetch adds the boundary",
      not any(k.lower() == "content-type" for k in req["headers"]))

got = call("sendFile", CHANNEL, str(MANIFEST), {"caption": "c" * 2001})
check("a caption over 2000 is refused before anything is sent",
      got["sent"] == [] and "caption over 2000" in got["error"]["message"])

# --- reading --------------------------------------------------------------

guilds = [{"id": "900000000000000001", "name": "Desk"}]
channels = [
    {"id": "1", "name": "general", "type": 0},
    {"id": "2", "name": "voice", "type": 2},
    {"id": "3", "name": "Text Channels", "type": 4},
    {"id": "4", "name": "news", "type": 5},
]
got = call("recentChannels", replies=[{"status": 200, "body": guilds}, {"status": 200, "body": channels}])
check("channels lists text and announcement channels, with their server",
      got["result"] == [
          {"id": "1", "name": "general", "server": "Desk", "serverId": "900000000000000001"},
          {"id": "4", "name": "news", "server": "Desk", "serverId": "900000000000000001"},
      ])

got = call("recentMessages", CHANNEL, {"limit": 500}, replies=[{"status": 200, "body": [
    {"id": "5", "author": {"username": "sam"}, "timestamp": "t", "content": "hi", "attachments": []}]}])
check("messages caps the limit at Discord's 100",
      got["sent"][0]["url"].endswith("?limit=100") and got["result"][0]["content"] == "hi")

got = call("openDm", USER)
check("a DM opens with the recipient's id", got["sent"][0]["body"] == {"recipient_id": USER})

# --- the CLI's own calls --------------------------------------------------

got = run({"cli": ["me"]})
check("me reads the bot's account", got["exit"] == 0 and got["sent"][0]["url"].endswith("/users/@me"))

got = run({"cli": ["edit", "--channel", CHANNEL, "--message", MESSAGE, "--text", "fixed"]})
check("edit PATCHes the message",
      got["exit"] == 0 and got["sent"][0]["method"] == "PATCH"
      and got["sent"][0]["url"].endswith(f"/channels/{CHANNEL}/messages/{MESSAGE}")
      and got["sent"][0]["body"]["content"] == "fixed")

got = run({"cli": ["delete", "--channel", CHANNEL, "--message", MESSAGE], "replies": [{"status": 204}]})
check("delete sends DELETE and takes the 204",
      got["exit"] == 0 and got["sent"][0]["method"] == "DELETE" and got["sent"][0]["url"].endswith(f"/messages/{MESSAGE}"))

# --- choosing the credential ---------------------------------------------

two = {"CRED_DISCORD_API_OTHER": "cred-other"}
got = call("sendMessage", CHANNEL, "hi", env=two)
check("two credentials without DISCORD_CRED is refused",
      got["sent"] == [] and "several Discord credentials" in got["error"]["message"])

got = call("sendMessage", CHANNEL, "hi", env={**two, "DISCORD_CRED": "CRED_DISCORD_API_OTHER"})
check("DISCORD_CRED picks one", got["sent"][0]["headers"]["Authorization"] == "Bot cred-other")

got = call("sendMessage", CHANNEL, "hi", env={"OTHER_SECRET": "s3cret", "DISCORD_CRED": "OTHER_SECRET"})
check("DISCORD_CRED cannot point at a variable that is not a Discord credential",
      got["sent"] == [] and "must name a CRED_DISCORD" in got["error"]["message"])

got = call("sendMessage", CHANNEL, "hi", env={"CRED_DISCORD_API_BOT_META": "{}"})
check("a _META variable is not a credential", got["sent"][0]["headers"]["Authorization"] == f"Bot {CRED}")

# --- every request is one the proxy will swap the token into ---------------

declared = routes()
check("app.yaml's routes are read", len(declared) == 6 and
      ("/api/v10/channels/*/messages", ["GET", "POST"]) in declared)
uncovered = []
for r in seen:
    path = r["url"].removeprefix("https://discord.com").split("?")[0]
    if not any(matches(p, path) and r["method"] in ms for p, ms in declared):
        uncovered.append(f"{r['method']} {path}")
check("every request matches a route", uncovered == [])
covered = {(p, m) for p, ms in declared for m in ms
           if any(matches(p, r["url"].removeprefix("https://discord.com").split("?")[0]) and r["method"] == m
                  for r in seen)}
check("every route is exercised", covered == {(p, m) for p, ms in declared for m in ms})

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
for u in uncovered:
    print(f"  not covered by app.yaml: {u}")
sys.exit(1 if failed else 0)
