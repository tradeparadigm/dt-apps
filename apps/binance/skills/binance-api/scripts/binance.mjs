// Every signed Binance call goes through here. The skill names it; nothing else
// writes it.
//
// METHOD and TARGET come from the environment so one file serves every call.
// TARGET carries the path and its query, without `signature`.
//
// Binance signs the query string, so the query is built once and used twice:
// base64 into the X-Dime-Sign header, then sent with the placeholder appended.

const all = Object.keys(process.env).filter(k => (process.env[k] || '').startsWith('sign-binance'));
const V = process.env.BINANCE_CRED || all[0];
if (!V) { console.error('no Binance sign- credential in the environment'); process.exit(2); }
if (all.length > 1 && !process.env.BINANCE_CRED) {
  console.error('several Binance credentials: ' + all.join(', ') + ' — re-run with BINANCE_CRED=<the one you want>');
  process.exit(2);
}
const HDR = 'X-Dime-Sign-' + V.replace(/^CRED_/, '').toLowerCase().replaceAll('_', '-');

const method = (process.env.METHOD || 'GET').toUpperCase();
const [path, query = ''] = (process.env.TARGET || '').split('?');

// Spot and futures are different hosts and the path says which: /fapi is USD-M
// futures, /api is spot. Demo and live are NOT distinguishable from the path,
// so the credential's name has to say, or you do.
const demo = /DEMO|TESTNET/i.test(V);
const futures = path.startsWith('/fapi');
if (!futures && !path.startsWith('/api')) {
  console.error(`cannot tell which Binance host ${path} belongs to: set BINANCE_HOST`);
  process.exit(2);
}
const HOST = process.env.BINANCE_HOST
  || (demo ? (futures ? 'demo-fapi.binance.com' : 'demo-api.binance.com')
           : (futures ? 'fapi.binance.com' : 'api.binance.com'));
if (!process.env.BINANCE_HOST && !demo && !/LIVE|SPOT|USDM/i.test(V)) {
  console.error(`${V} does not say which environment it belongs to, and guessing would risk a live order.`);
  console.error(`Re-run with BINANCE_HOST=${futures ? 'fapi.binance.com' : 'api.binance.com'} for live, or the demo- host for demo.`);
  process.exit(2);
}

// Percent-encoded before signing, because Binance rejects a signature taken
// over the raw form with -1022.
const qs = new URLSearchParams(query);
if (!qs.has('timestamp')) qs.set('timestamp', Date.now().toString());
const signed = qs.toString();

const res = await fetch(`https://${HOST}${path}?${signed}&signature=${process.env[V]}`, {
  method,
  headers: { [HDR]: Buffer.from(signed).toString('base64') },
});
console.log(res.status, await res.text());
