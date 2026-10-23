#!/usr/bin/env node
// Discord REST API through the credential proxy: the placeholder goes in the
// Authorization header and the proxy swaps in the token. CLI, and importable
// from a data feed (`import { sendMessage } from '<this file>'`).
import { readFile } from 'node:fs/promises';
import { basename } from 'node:path';
import { pathToFileURL } from 'node:url';

const API = 'https://discord.com/api/v10';
// Discord's own cap on one message's content.
const MAX_TEXT = 2000;
// Discord refuses a bot request without a User-Agent of this shape.
const USER_AGENT = 'DiscordBot (https://github.com/tradeparadigm/dt-apps, 1.0.0)';
// Text and announcement channels: the two a bot can post a message into.
const POSTABLE = new Set([0, 5]);
const SUPPRESS_NOTIFICATIONS = 1 << 12;

// Ids go into the URL path, so anything but a snowflake is refused.
function snowflake(v, what) {
  const s = String(v);
  if (!/^\d{17,20}$/.test(s)) throw new Error(`${what} must be a Discord id (17 to 20 digits), got ${JSON.stringify(s)}`);
  return s;
}

// The only place a channel or message id enters a URL.
function messagesPath(channel, message) {
  const base = `/channels/${snowflake(channel, 'channel')}/messages`;
  return message === undefined ? base : `${base}/${snowflake(message, 'message')}`;
}

export function placeholder(env = process.env) {
  if (env.DISCORD_CRED) {
    if (!env.DISCORD_CRED.startsWith('CRED_DISCORD')) {
      throw new Error('DISCORD_CRED must name a CRED_DISCORD... variable');
    }
    const value = env[env.DISCORD_CRED];
    if (!value) throw new Error(`${env.DISCORD_CRED} is not set`);
    return value;
  }
  const vars = Object.keys(env).filter(
    (k) => k.startsWith('CRED_DISCORD') && !k.endsWith('_META'),
  );
  if (vars.length === 0) {
    throw new Error('no Discord credential in the environment (want CRED_DISCORD_...)');
  }
  if (vars.length > 1) {
    throw new Error(`several Discord credentials: ${vars.join(' ')}. Re-run with DISCORD_CRED=<the one you want>`);
  }
  return env[vars[0]];
}

export async function call(method, path, body, { cred = placeholder() } = {}) {
  const form = body instanceof FormData;
  const headers = { Authorization: `Bot ${cred}`, 'User-Agent': USER_AGENT };
  if (body !== undefined && !form) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  if (res.status === 204) return null;
  const text = await res.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(`${method} ${path}: HTTP ${res.status} ${text.slice(0, 300)}`);
  }
  if (!res.ok) {
    const code = parsed.code ? ` (code ${parsed.code})` : '';
    const detail = parsed.errors ? ` ${JSON.stringify(parsed.errors).slice(0, 300)}` : '';
    const wait = parsed.retry_after ? ` retry after ${parsed.retry_after}s` : '';
    const err = new Error(`${method} ${path}: ${res.status} ${parsed.message ?? ''}${code}${wait}${detail}`.trim());
    err.status = res.status;
    err.code = parsed.code;
    err.retryAfter = parsed.retry_after;
    throw err;
  }
  return parsed;
}

// Splits on whole code points, at the last newline that fits when there is one.
// Length is counted in UTF-16 units, the larger of the two counts, so a part
// never runs over however Discord counts it. Blank parts are dropped: Discord
// refuses an empty message.
export function chunks(text) {
  const rest = Array.from(text);
  const out = [];
  for (;;) {
    let units = 0;
    let fit = 0;
    while (fit < rest.length && units + rest[fit].length <= MAX_TEXT) {
      units += rest[fit].length;
      fit += 1;
    }
    if (fit === rest.length) break;
    const newline = rest.lastIndexOf('\n', fit);
    const cut = newline >= 0 ? newline : fit;
    out.push(rest.splice(0, cut).join(''));
    if (rest[0] === '\n') rest.shift();
  }
  out.push(rest.join(''));
  return out.filter((part) => part.trim() !== '');
}

// No @everyone, role or user pings unless asked: feed text is not ours.
function mentions(allow) {
  return allow ? { parse: ['users', 'roles', 'everyone'] } : { parse: [] };
}

export async function sendMessage(channel, text, { silent = false, mention = false, cred } = {}) {
  const parts = chunks(text);
  if (parts.length === 0) throw new Error('nothing to send');
  const sent = [];
  for (const part of parts) {
    sent.push(
      await call(
        'POST',
        messagesPath(channel),
        {
          content: part,
          allowed_mentions: mentions(mention),
          ...(silent ? { flags: SUPPRESS_NOTIFICATIONS } : {}),
        },
        cred ? { cred } : undefined,
      ),
    );
  }
  return sent;
}

export async function sendFile(channel, file, { caption, silent = false, mention = false, cred } = {}) {
  if (caption && chunks(caption).length > 1) {
    throw new Error(`caption over ${MAX_TEXT} characters: send the text as a message`);
  }
  const form = new FormData();
  form.set(
    'payload_json',
    JSON.stringify({
      ...(caption ? { content: caption } : {}),
      allowed_mentions: mentions(mention),
      attachments: [{ id: 0, filename: basename(file) }],
      ...(silent ? { flags: SUPPRESS_NOTIFICATIONS } : {}),
    }),
  );
  form.set('files[0]', new Blob([await readFile(file)]), basename(file));
  return call('POST', messagesPath(channel), form, cred ? { cred } : undefined);
}

export async function editMessage(channel, message, text, { mention = false, cred } = {}) {
  if (chunks(text).length !== 1) throw new Error(`edit text must be 1 to ${MAX_TEXT} characters`);
  return call(
    'PATCH',
    messagesPath(channel, message),
    { content: text, allowed_mentions: mentions(mention) },
    cred ? { cred } : undefined,
  );
}

export async function deleteMessage(channel, message, { cred } = {}) {
  return call('DELETE', messagesPath(channel, message), undefined, cred ? { cred } : undefined);
}

// The direct-message channel with one user. They must share a server with the bot.
export async function openDm(user, { cred } = {}) {
  return call('POST', '/users/@me/channels', { recipient_id: snowflake(user, 'user') }, cred ? { cred } : undefined);
}

// Every text and announcement channel in every server the bot is in.
export async function recentChannels({ cred } = {}) {
  const opts = cred ? { cred } : undefined;
  const guilds = await call('GET', '/users/@me/guilds', undefined, opts);
  const out = [];
  for (const g of guilds) {
    const channels = await call('GET', `/guilds/${g.id}/channels`, undefined, opts);
    for (const c of channels) {
      if (POSTABLE.has(c.type)) out.push({ id: c.id, name: c.name, server: g.name, serverId: g.id });
    }
  }
  return out;
}

export async function recentMessages(channel, { limit = 20, cred } = {}) {
  const n = Math.min(Math.max(Number(limit) || 20, 1), 100);
  const list = await call('GET', `${messagesPath(channel)}?limit=${n}`, undefined, cred ? { cred } : undefined);
  return list.map((m) => ({
    id: m.id,
    author: m.author?.username,
    bot: m.author?.bot === true,
    at: m.timestamp,
    content: m.content,
    attachments: (m.attachments ?? []).map((a) => a.url),
  }));
}

function parseArgs(argv) {
  const flags = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--silent' || a === '--mention') flags[a.slice(2)] = true;
    else if (a.startsWith('--')) flags[a.slice(2)] = argv[(i += 1)];
  }
  return flags;
}

async function readStdin() {
  const parts = [];
  for await (const c of process.stdin) parts.push(c);
  return Buffer.concat(parts).toString('utf8');
}

const USAGE = `usage: discord.mjs <command>
  me                                                the bot's own account
  channels                                          text channels in every server the bot is in
  messages --channel <id> [--limit <n>]             recent messages, newest first (max 100)
  send --channel <id> [--text <t>] [--silent] [--mention]   text from --text or stdin
  file --channel <id> --file <path> [--caption <t>] [--silent] [--mention]
  edit --channel <id> --message <id> --text <t> [--mention]
  delete --channel <id> --message <id>
  dm --user <id> [--text <t>]                       the DM channel id, and sends --text there`;

async function main() {
  const [cmd, ...argv] = process.argv.slice(2);
  const flags = parseArgs(argv);
  const need = (k) => {
    if (!flags[k]) throw new Error(`--${k} is required\n${USAGE}`);
    return flags[k];
  };
  let out;
  switch (cmd) {
    case 'me': {
      const me = await call('GET', '/users/@me');
      out = { id: me.id, username: me.username, bot: me.bot };
      break;
    }
    case 'channels':
      out = await recentChannels();
      break;
    case 'messages':
      out = await recentMessages(need('channel'), { limit: flags.limit });
      break;
    case 'send': {
      const text = flags.text ?? (await readStdin());
      out = (await sendMessage(need('channel'), text, flags)).map((m) => m.id);
      break;
    }
    case 'file':
      out = (await sendFile(need('channel'), need('file'), flags)).id;
      break;
    case 'edit':
      out = (await editMessage(need('channel'), need('message'), need('text'), flags)).id;
      break;
    case 'delete':
      await deleteMessage(need('channel'), need('message'));
      out = 'deleted';
      break;
    case 'dm': {
      const dm = await openDm(need('user'));
      out = { channel: dm.id };
      if (flags.text) out.sent = (await sendMessage(dm.id, flags.text, flags)).map((m) => m.id);
      break;
    }
    default:
      console.error(USAGE);
      process.exit(2);
  }
  console.log(JSON.stringify(out, null, 2));
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  main().catch((err) => {
    console.error(err.message);
    process.exit(1);
  });
}
