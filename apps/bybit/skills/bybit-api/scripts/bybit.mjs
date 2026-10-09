// Every signed Bybit call goes through here. The skill names it; nothing else
// writes it.
//
// METHOD, TARGET and BODY come from the environment so one file serves every
// call. TARGET carries the path and, on a GET, its query.
//
// The payload is built once and used twice, signed then sent. Re-serialising
// it in between is what produces `10004 error sign!`.

const all = Object.keys(process.env).filter(k => (process.env[k] || '').startsWith('sign-bybit'));
const V = process.env.BYBIT_CRED || all[0];
if (!V) { console.error('no Bybit sign- credential in the environment'); process.exit(2); }
if (all.length > 1 && !process.env.BYBIT_CRED) {
  console.error('several Bybit credentials: ' + all.join(', ') + ' — re-run with BYBIT_CRED=<the one you want>');
  process.exit(2);
}
const HDR = 'X-Dime-Sign-' + V.replace(/^CRED_/, '').toLowerCase().replaceAll('_', '-');

const META = JSON.parse(process.env[V + '_META']);
const KEY = META.api_key;
// _hosts names the environment. The label is read only on a terminal too old to publish it.
const HOST = (META._hosts || '').split(',').find(h => /^api(-testnet|-demo)?\.bybit\.com$/.test(h))
           || (/TESTNET/.test(V) ? 'api-testnet.bybit.com'
             : /DEMO/.test(V)    ? 'api-demo.bybit.com'
             :                     'api.bybit.com');

const method = (process.env.METHOD || 'GET').toUpperCase();
const body = process.env.BODY || '';
// The query lives on TARGET and nowhere else. Sending it on the URL and again
// in the signed bytes is the mistake this shape exists to prevent.
const [path, query = ''] = (process.env.TARGET || '').split('?');
const get = method === 'GET';
const payload = get ? query : body;

const ts = Date.now().toString();
const res = await fetch(`https://${HOST}${path}` + (query ? '?' + query : ''), {
  method,
  headers: {
    'X-BAPI-API-KEY': KEY,
    'X-BAPI-TIMESTAMP': ts,
    'X-BAPI-RECV-WINDOW': '5000',
    'X-BAPI-SIGN': process.env[V],
    [HDR]: Buffer.from(ts + KEY + '5000' + payload).toString('base64'),
    ...(get ? {} : { 'Content-Type': 'application/json' }),
  },
  ...(get ? {} : { body }),
});
console.log(res.status, await res.text());
