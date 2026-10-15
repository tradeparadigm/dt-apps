---
name: telegram-bot
description: >
  Send messages, photos and files to Telegram through the user's own bot, using
  a bot token held by the DIME credential proxy. Covers finding the chats the
  bot can reach, posting to a private chat, group or channel, editing and
  deleting what it sent, and sending from a data feed. Use for ANY request to
  message, notify or alert the user on Telegram, "send this to my Telegram",
  "post alerts to my channel", or a 403 from the credential proxy on
  api.telegram.org.
metadata:
  author: tradeparadigm
---

# Telegram

Your `AGENTS.md` already explains the placeholder mechanism in general. This
file covers only what is specific to Telegram.

## What you hold

One bot token, as `CRED_TELEGRAM_API_BOT`. The name depends on what the user
picked at enrolment, and the script takes any `CRED_TELEGRAM…` variable.

The token belongs in the URL path, `https://api.telegram.org/bot<token>/<method>`.
Use the script beside this file. It puts the placeholder there for you.

Only one bot works at a time. A second Telegram credential makes every
request from both fail. If `env | grep '^CRED_TELEGRAM'` lists two, tell the
user to revoke one in Settings → Credentials.

## Finding a chat

A bot cannot start a conversation. The user has to reach it first:

- **A private chat:** the user opens their bot in Telegram and sends it any message.
- **A group:** the user adds the bot to the group and sends a message there.
- **A channel:** the user adds the bot as an administrator with permission to post.

Then:

```sh
node scripts/telegram.mjs me      # the bot's @username, to tell the user who to message
node scripts/telegram.mjs chats   # every chat that has reached the bot recently
```

`chats` reads Telegram's pending updates, which it keeps for 24 hours. If the
chat is not listed, ask the user to send the bot another message. A public
channel also works by its `@name` as the chat id.

Write the chat id down where you will find it again, such as the data feed's
config. Ask the user which chat to use when there is more than one.

## Sending

```sh
node scripts/telegram.mjs send --chat 123456789 --text "BTC crossed 100k"
echo "multi-line text" | node scripts/telegram.mjs send --chat -1001234567890
node scripts/telegram.mjs send --chat @mychannel --html --text "<b>Fill</b> 2 BTC @ 99,950"
node scripts/telegram.mjs photo --chat 123456789 --file chart.png --caption "BTC 1h"
node scripts/telegram.mjs document --chat 123456789 --file report.csv
```

Give the script's absolute path when you run it from another directory. Each
command prints Telegram's answer as JSON: `send` prints the message ids, and
`edit` and `delete` take one with `--message`.

- Text is plain unless you pass `--html`. With `--html`, escape `<`, `>` and `&`
  in anything you did not write, or Telegram refuses the message.
- `--silent` delivers without a notification sound.
- Plain text over 4096 characters is split into several messages, at a line
  break where there is one. `--html` text over 4096 characters is refused:
  split it yourself, keeping each tag inside one message.

## From a data feed

The folder holding this file is replaced when the app updates, so copy the
script into the data feed's own directory:

```sh
cp <this folder>/scripts/telegram.mjs ~/.openclaw/workspace/datafeeds/<name>/
```

Then import it in `datafeed.mjs` and call `sendMessage`:

```js
import { sendMessage } from './telegram.mjs';

await sendMessage(chatId, text, { cred: config.telegramCred });
```

Store the placeholder's value in the data feed's config as `telegramCred`
when you write it. A data feed may not have `CRED_` variables in its
environment. The placeholder is not a secret, so a config file is a fine place
for it. Without `cred`, the script reads `CRED_TELEGRAM…` from the environment.

Telegram allows about one message a second per chat and 20 a minute per group.
Batch lines into one message rather than sending one per event. A 429 error
carries `retryAfter` in seconds. Wait that long before the next send.

## When a call fails

| What you see | Causes, cheapest to check first |
|---|---|
| 403 from the proxy, body says `placeholder_absent` | You called api.telegram.org without the script, or without the placeholder in the path. Or two Telegram credentials are enrolled. |
| `401 Unauthorized` | The token was revoked in BotFather, or enrolled with a typo. Ask the user to enrol it again. |
| `404 Not Found` | A method the credential does not cover. The script sends only the ones listed above. |
| `400 Bad Request: chat not found` | The user has not messaged the bot, or the bot is not in that group or channel. |
| `403 Forbidden: bot was blocked by the user` | The user blocked the bot. Only they can unblock it. |
| `403 Forbidden: bot is not a member of the channel chat` | Add the bot to the channel as an administrator with permission to post. |
| `400 Bad Request: can't parse entities` | `--html` text with an unescaped `<`, `>` or `&`. |
| `409 Conflict` from `chats` | Something else reads the bot's updates, such as a webhook. Ask the user for the chat id, or `@name` for a public channel. |
| `several Telegram credentials` from the script | Two are enrolled. Ask the user to revoke one. |

## Never edit the script

A publish replaces it. If it does not work, change the smallest thing that
makes your command run, run it, and tell the user in one line what you
changed, so it can be fixed here.
