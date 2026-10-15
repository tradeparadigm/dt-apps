#!/usr/bin/env node
// Telegram Bot API through the credential proxy: the placeholder goes in the
// URL path and the proxy swaps in the token. CLI, and importable from a data
// feed (`import { sendMessage } from '<this file>'`).
import { readFile } from 'node:fs/promises';
import { basename } from 'node:path';
import { pathToFileURL } from 'node:url';

const API = 'https://api.telegram.org';
// Telegram's own cap on one message's text.
const MAX_TEXT = 4096;

export function placeholder(env = process.env) {
  if (env.TELEGRAM_CRED) {
    if (!env.TELEGRAM_CRED.startsWith('CRED_TELEGRAM')) {
      throw new Error('TELEGRAM_CRED must name a CRED_TELEGRAM... variable');
    }
    const value = env[env.TELEGRAM_CRED];
    if (!value) throw new Error(`${env.TELEGRAM_CRED} is not set`);
    return value;
  }
  const vars = Object.keys(env).filter(
    (k) => k.startsWith('CRED_TELEGRAM') && !k.endsWith('_META'),
  );
  if (vars.length === 0) {
    throw new Error('no Telegram credential in the environment (want CRED_TELEGRAM_...)');
  }
  if (vars.length > 1) {
    throw new Error(`several Telegram credentials: ${vars.join(' ')}. Re-run with TELEGRAM_CRED=<the one you want>`);
  }
  return env[vars[0]];
}

export async function call(method, params = {}, { cred = placeholder() } = {}) {
  const form = params instanceof FormData;
  const res = await fetch(`${API}/bot${cred}/${method}`, {
    method: 'POST',
    headers: form ? undefined : { 'Content-Type': 'application/json' },
    body: form ? params : JSON.stringify(params),
  });
  const text = await res.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    throw new Error(`${method}: HTTP ${res.status} ${text.slice(0, 300)}`);
  }
  if (!body.ok) {
    const err = new Error(`${method}: ${body.error_code ?? res.status} ${body.description ?? ''}`.trim());
    err.status = body.error_code ?? res.status;
    err.retryAfter = body.parameters?.retry_after;
    throw err;
  }
  return body.result;
}

// Splits on whole code points, at the last newline that fits when there is one.
export function chunks(text) {
  const rest = Array.from(text);
  const out = [];
  while (rest.length > MAX_TEXT) {
    const newline = rest.lastIndexOf('\n', MAX_TEXT);
    const cut = newline > 0 ? newline : MAX_TEXT;
    out.push(rest.splice(0, cut).join(''));
    if (rest[0] === '\n') rest.shift();
  }
  out.push(rest.join(''));
  return out;
}

export async function sendMessage(chat, text, { html = false, silent = false, cred } = {}) {
  // A cut through HTML can land inside a tag, and Telegram refuses the half.
  if (html && Array.from(text).length > MAX_TEXT) {
    throw new Error(`HTML text over ${MAX_TEXT} characters: send it as several messages`);
  }
  const sent = [];
  for (const part of chunks(text)) {
    sent.push(
      await call(
        'sendMessage',
        {
          chat_id: chat,
          text: part,
          ...(html ? { parse_mode: 'HTML' } : {}),
          disable_notification: silent,
          link_preview_options: { is_disabled: true },
        },
        cred ? { cred } : undefined,
      ),
    );
  }
  return sent;
}

export async function sendFile(kind, chat, file, { caption, cred } = {}) {
  const form = new FormData();
  form.set('chat_id', String(chat));
  if (caption) form.set('caption', caption);
  form.set(kind, new Blob([await readFile(file)]), basename(file));
  const method = kind === 'photo' ? 'sendPhoto' : 'sendDocument';
  return call(method, form, cred ? { cred } : undefined);
}

// Every chat that has written to the bot or added it, from the updates
// Telegram still holds (the last 24 hours, and none once a webhook is set).
export async function recentChats({ cred } = {}) {
  const updates = await call('getUpdates', { limit: 100, timeout: 0 }, cred ? { cred } : undefined);
  const chats = new Map();
  for (const u of updates) {
    const chat =
      u.message?.chat ??
      u.channel_post?.chat ??
      u.my_chat_member?.chat ??
      u.edited_message?.chat;
    if (chat) chats.set(chat.id, chat);
  }
  return [...chats.values()].map((c) => ({
    id: c.id,
    type: c.type,
    title: c.title ?? [c.first_name, c.last_name].filter(Boolean).join(' '),
    username: c.username,
  }));
}

function parseArgs(argv) {
  const flags = {};
  const rest = [];
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--html' || a === '--silent') flags[a.slice(2)] = true;
    else if (a.startsWith('--')) flags[a.slice(2)] = argv[(i += 1)];
    else rest.push(a);
  }
  return { flags, rest };
}

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString('utf8');
}

const USAGE = `usage: telegram.mjs <command>
  me                                        the bot's own account
  chats                                     chats the bot can reach (recent updates)
  chat --chat <id|@channel>                 one chat's details
  send --chat <id|@channel> [--text <t>] [--html] [--silent]   text from --text or stdin
  photo --chat <id> --file <path> [--caption <t>]
  document --chat <id> --file <path> [--caption <t>]
  edit --chat <id> --message <id> --text <t> [--html]
  delete --chat <id> --message <id>`;

async function main() {
  const [cmd, ...argv] = process.argv.slice(2);
  const { flags } = parseArgs(argv);
  const need = (k) => {
    if (!flags[k]) throw new Error(`--${k} is required\n${USAGE}`);
    return flags[k];
  };
  let out;
  switch (cmd) {
    case 'me':
      out = await call('getMe');
      break;
    case 'chats':
      out = await recentChats();
      break;
    case 'chat':
      out = await call('getChat', { chat_id: need('chat') });
      break;
    case 'send': {
      const text = flags.text ?? (await readStdin());
      if (!text.trim()) throw new Error('nothing to send');
      out = (await sendMessage(need('chat'), text, flags)).map((m) => m.message_id);
      break;
    }
    case 'photo':
    case 'document':
      out = (await sendFile(cmd, need('chat'), need('file'), flags)).message_id;
      break;
    case 'edit':
      out = (
        await call('editMessageText', {
          chat_id: need('chat'),
          message_id: Number(need('message')),
          text: need('text'),
          ...(flags.html ? { parse_mode: 'HTML' } : {}),
        })
      ).message_id;
      break;
    case 'delete':
      out = await call('deleteMessage', { chat_id: need('chat'), message_id: Number(need('message')) });
      break;
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
