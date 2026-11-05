---
name: discord-bot
description: >
  Send messages and files to Discord through the user's own bot, using a bot
  token held by the DIME credential proxy. Covers finding the channels the bot
  can reach, posting to a server channel or a direct message, reading recent
  messages, editing and deleting what it sent, and sending from a data feed.
  Use for ANY request to message, notify or alert the user on Discord, "send
  this to my Discord", "post alerts to my channel", or a 403 from the
  credential proxy on discord.com.
metadata:
  author: tradeparadigm
---

# Discord

Your `AGENTS.md` already explains the placeholder mechanism in general. This
file covers only what is specific to Discord.

## What you hold

One bot token, as `CRED_DISCORD_API_BOT`. The name depends on what the user
picked at enrolment, and the script takes any `CRED_DISCORD…` variable.

The token goes in the `Authorization` header as `Bot <token>`. Use the script
beside this file. It sends the placeholder there for you.

Only one bot works at a time. A second Discord credential makes every request
from both fail. If `env | grep '^CRED_DISCORD'` lists two, tell the user to
revoke one in Settings → Credentials.

## Finding a channel

A bot sees only the servers it has been invited to. If `channels` lists
nothing, ask the user to invite it: in the Developer Portal, **OAuth2 → URL
Generator**, tick the `bot` scope and the View Channels, Send Messages, Attach
Files and Read Message History permissions, then open the link.

```sh
node scripts/discord.mjs me         # the bot's username, so the user knows which bot it is
node scripts/discord.mjs channels   # every text channel in every server the bot is in
```

`channels` also lists channels the bot cannot see. A send there fails with
code 50001.

Ids are long numbers. Keep them as strings. Write the channel id down where you
will find it again, such as the data feed's config. Ask the user which channel
to use when there is more than one.

For a direct message, the user's id comes from Discord itself: with Developer
Mode on (User Settings → Advanced), the user right-clicks their own name and
picks **Copy User ID**. They must share a server with the bot.

```sh
node scripts/discord.mjs dm --user 123456789012345678 --text "Hello"
```

`dm` prints the DM's channel id. Use it with every other command.

## Sending

```sh
node scripts/discord.mjs send --channel 123456789012345678 --text "BTC crossed 100k"
echo "multi-line text" | node scripts/discord.mjs send --channel 123456789012345678
node scripts/discord.mjs file --channel 123456789012345678 --file chart.png --caption "BTC 1h"
node scripts/discord.mjs messages --channel 123456789012345678 --limit 10
```

Give the script's absolute path when you run it from another directory. Each
command prints JSON: `send` prints the message ids, and `edit` and `delete`
take one with `--message`.

- Text is Discord markdown: `**bold**`, `` `code` ``, `> quote`.
- Mentions never ping by default, so `@everyone` in feed text stays inert.
  `--mention` lets them ping. Use it only when the user asks for one.
- `--silent` delivers without a notification.
- Text over 2000 characters is split into several messages, at a line break
  where there is one. A caption over 2000 characters is refused.

`messages` shows empty `content` for other people's server messages until the
user turns on **Message Content Intent** under Bot in the Developer Portal.
Direct messages, messages that mention the bot and the bot's own messages
show their text either way.

## From a data feed

The folder holding this file is replaced when the app updates, so copy the
script into the data feed's own directory:

```sh
cp <this folder>/scripts/discord.mjs ~/.openclaw/workspace/datafeeds/<name>/
```

Then import it in `datafeed.mjs` and call `sendMessage`:

```js
import { sendMessage } from './discord.mjs';

await sendMessage(channelId, text, { cred: config.discordCred });
```

Store the placeholder's value in the data feed's config as `discordCred` when
you write it. A data feed may not have `CRED_` variables in its environment.
The placeholder is not a secret, so a config file is a fine place for it.
Without `cred`, the script reads `CRED_DISCORD…` from the environment.

Discord allows about five messages every five seconds per channel. Batch lines
into one message rather than sending one per event. A 429 error carries
`retryAfter` in seconds, and the CLI prints it as `retry after <n>s`. Wait
that long before the next send.

## When a call fails

| What you see | Causes, cheapest to check first |
|---|---|
| 403 from the proxy, body says `placeholder_absent` | You called discord.com without the script, or without the placeholder in the header. Or two Discord credentials are enrolled. |
| `401 Unauthorized` | The token was reset in the Developer Portal, or enrolled with a typo. Ask the user to enrol it again. A path the credential does not cover also answers 401, because the placeholder goes out unswapped. |
| `403 Missing Access (code 50001)` | The bot is not in that server, or cannot see that channel. |
| `403 Missing Permissions (code 50013)` | The bot's role lacks Send Messages or Attach Files in that channel. |
| `404 Unknown Channel (code 10003)` | A wrong id, or a channel that was deleted. |
| `403 Cannot send messages to this user (code 50007)` | The user shares no server with the bot, or blocks DMs from server members. |
| `400 … (code 50035)` | The request body is wrong. The error lists the field. |
| Empty `content` from `messages` | Message Content Intent is off. |
| `must be a Discord id` from the script | A channel, message or user id that is not 17 to 20 digits. |
| `several Discord credentials` from the script | Two are enrolled. Ask the user to revoke one. |

## Never edit the script

A publish replaces it. If it does not work, change the smallest thing that
makes your command run, run it, and tell the user in one line what you
changed, so it can be fixed here.
