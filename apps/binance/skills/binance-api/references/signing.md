# How Binance signs a request

Read this when a call fails. The shipped client already does all of it.

## Every signed request

Binance signs **`totalParams`: the query string concatenated with the request
body, with no separator between them.** Everything else follows from that.

```
GET /fapi/v2/balance?timestamp=1790000000000&recvWindow=5000&signature=<placeholder>
X-Dime-Sign-<label>: <base64 of "timestamp=1790000000000&recvWindow=5000">
```

1. Build the query string you are going to send, **without** `signature`.
2. Base64 that exact string into `X-Dime-Sign-<label>`.
3. Append `&signature=<placeholder>` as the LAST parameter.
4. Send it. The proxy computes HMAC-SHA256 over your bytes, writes the hex
   digest in place of the placeholder, strips the `X-Dime-Sign-` header, and
   forwards.

**The bytes you sign must be the bytes you send, in order.** The proxy
substitutes the placeholder without reordering or re-encoding anything else, so
what arrives at Binance is your query string with one value swapped.

**Percent-encode before signing.** Since 2026-01-15 Binance requires the payload
to be percent-encoded *before* the signature is computed; a request signed over
the raw form is rejected with `-1022`. Encode once, sign the encoded string,
send the encoded string.

**Put `signature` last.** USD-M futures requires it — "make sure the signature
is the end part of your query string or request body". Spot does not state the
rule, so always appending it works on both.

**Send parameters in the query string, including on POST and DELETE.** Binance
allows either, and mixing them means the signature has to cover the
concatenation of both, which is a needless way to get `-1022`. One place, one
string, one signature. (If a parameter appears in both, Binance uses the query
string one.)

`timestamp` is milliseconds. `recvWindow` defaults to 5000 ms and caps at
60000; futures additionally rejects a timestamp more than 1000 ms ahead of
server time.

### Worked example

Read your USD-M futures balance.

```
ts=1790000000000
QS="timestamp=${ts}&recvWindow=5000"
X-Dime-Sign-<label>: base64(QS)
GET https://fapi.binance.com/fapi/v3/balance?${QS}&signature=<placeholder>
```
