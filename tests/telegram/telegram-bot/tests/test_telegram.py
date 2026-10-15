"""The Telegram client: what sendMessage sends, with fetch stubbed in node.

Each case runs the real module and records every request it makes, so the
checks are on the messages Telegram would receive. Nothing reaches Telegram.
"""

import json
import pathlib
import subprocess
import sys

CLIENT = (
    pathlib.Path(__file__).resolve().parents[4]
    / "apps/telegram/skills/telegram-bot/scripts/telegram.mjs"
)

RUN = """
const { sendMessage } = await import(process.argv[1]);
const sent = [];
globalThis.fetch = async (url, init) => {
  sent.push({ url, body: JSON.parse(init.body) });
  return new Response(JSON.stringify({ ok: true, result: { message_id: sent.length } }));
};
const { text, html } = JSON.parse(process.argv[2]);
try {
  await sendMessage(42, text, { html, cred: 'cred-telegram-api-bot-PH' });
  console.log(JSON.stringify({ sent }));
} catch (err) {
  console.log(JSON.stringify({ sent, error: err.message }));
}
"""

passed = 0
failed = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(name)


def send(text, html=False):
    out = subprocess.run(
        ["node", "--input-type=module", "-e", RUN, CLIENT.as_uri(), json.dumps({"text": text, "html": html})],
        capture_output=True, text=True, check=True,
    ).stdout
    got = json.loads(out)
    return [s["body"]["text"] for s in got["sent"]], got["sent"], got.get("error")


texts, sent, _ = send("hello")
check("a short message is one request", texts == ["hello"])
check("the placeholder is the token in the path",
      sent[0]["url"] == "https://api.telegram.org/botcred-telegram-api-bot-PH/sendMessage")
check("the chat id is sent", sent[0]["body"]["chat_id"] == 42)

texts, _, _ = send("x" * 4095 + "\U0001F600" + "y")
check("an emoji on the boundary stays whole",
      len(texts) == 2 and texts[0].endswith("\U0001F600") and texts[1] == "y")

line = "a" * 3000 + "\n"
texts, _, _ = send(line * 3)
check("long text splits at line breaks", texts == ["a" * 3000, "a" * 3000, "a" * 3000 + "\n"])

texts, _, _ = send("x" * 4096 + "\n")
check("a trailing newline past the limit sends no empty message", texts == ["x" * 4096])

texts, _, err = send("<b>" + "x" * 4100 + "</b>", html=True)
check("long HTML is refused before anything is sent", texts == [] and "several messages" in (err or ""))

texts, _, _ = send("<b>bold</b>", html=True)
check("short HTML is sent whole", texts == ["<b>bold</b>"])

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
