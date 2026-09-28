# How Bybit signs a request

Read this when a call fails. The shipped client already does all of it.

## Every private request

Four headers, always, and they are coupled — three of them also go into the
string that gets signed.

```
X-BAPI-API-KEY:     <api_key from _META>
X-BAPI-TIMESTAMP:   <milliseconds since epoch>
X-BAPI-RECV-WINDOW: 5000
X-BAPI-SIGN:        <the sign- placeholder>
```

The string to sign is these three values concatenated with the request payload,
with **no separators at all**:

```
timestamp + api_key + recv_window + payload
```

where `payload` is:

- for **GET / DELETE**: the query string exactly as it appears in the URL,
  without the leading `?` — e.g. `category=linear&symbol=BTCUSDT`
- for **POST**: the raw request body, byte for byte, exactly as you will send
  it

**Compute the timestamp ONCE.** The value in `X-BAPI-TIMESTAMP` and the value
you concatenated must be the same variable, and so must `recv_window`. Building
one for the header and another for the signed string is the single most common
way to produce a signature Bybit rejects, and it fails intermittently — the two
agree whenever they land in the same millisecond.

**Byte-exact matters more than anything else on this page.** Bybit recomputes
the HMAC over what it received. If you build the JSON body one way for signing
and let an HTTP library re-serialise it another way when sending — different
key order, added whitespace, a float rendered differently — the signature is
over a string Bybit never sees and you get `10004 error sign!`. Serialise the
body **once**, into a string, sign that string, and send that same string.

