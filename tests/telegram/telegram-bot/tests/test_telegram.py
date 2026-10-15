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
check("plain text sets no parse mode", "parse_mode" not in sent[0]["body"])

texts, _, err = send(" \n ")
check("blank text is refused before anything is sent", texts == [] and err == "nothing to send")

texts, _, _ = send("x" * 4095 + "\U0001F600" + "y")
check("an emoji on the boundary stays whole",
      len(texts) == 2 and texts[0].endswith("\U0001F600") and texts[1] == "y")

line = "a" * 3000 + "\n"
texts, _, _ = send(line * 3)
check("long text splits at line breaks", texts == ["a" * 3000, "a" * 3000, "a" * 3000 + "\n"])

texts, _, _ = send("a\n" + "x" * 4094)
check("exactly 4096 characters is one message", texts == ["a\n" + "x" * 4094])

texts, _, _ = send("a\n" + "x" * 4094 + "\ny")
check("a newline right at the limit is where it cuts", texts == ["a\n" + "x" * 4094, "y"])

texts, _, _ = send("\n" + "x" * 5000)
check("a newline at a cut starts no message", texts == ["x" * 4096, "x" * 904])

texts, _, _ = send("x" * 4096 + "\n")
check("a trailing newline past the limit sends no empty message", texts == ["x" * 4096])

texts, _, err = send("<b>" + "x" * 4100 + "</b>", html=True)
check("long HTML is refused before anything is sent", texts == [] and "several messages" in (err or ""))

texts, sent, _ = send("<b>bold</b>", html=True)
check("short HTML is sent whole, as HTML",
      texts == ["<b>bold</b>"] and sent[0]["body"].get("parse_mode") == "HTML")

print(f"{passed} passed, {len(failed)} failed")
for name in failed:
    print(f"  FAILED: {name}")
sys.exit(1 if failed else 0)
