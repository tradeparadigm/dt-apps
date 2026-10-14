// Decodes Paradex's binary WebSocket frames (SBE) into plain objects, by
// reading the schema Paradex publishes (paradex_1_0.xml, beside this file).
// paradex.mjs uses it; a data feed does not need to call it.
//
// There is no code per message: the schema says each message's fields, in
// order, and this reads them. A new schema version is a new XML file and the
// version number in paradex.mjs, nothing else.
//
// What a frame is (SBE, little-endian throughout):
//   header   8 bytes: blockLength, templateId, schemaId, version (uint16 each)
//   block    blockLength bytes: the message's fixed-size fields, in schema
//            order. A field the frame's block is too short for is absent:
//            that is how an older version reads a newer frame, and the reverse.
//   groups   for each repeating group: blockLength and count (uint16 each),
//            then count entries of blockLength bytes
//   data     for each variable-length field: a uint8 length, then UTF-8. One
//            missing at the end of the frame is absent, not an error (schema 1:2
//            appended some in place).
//
// What a value becomes:
//   a price, size or rate (a mantissa with a constant exponent): a number,
//     e.g. mantissa 4200050000000 at -8 is 42000.5; null for the null value
//   a timestamp: a number, microseconds since the epoch, as on the wire
//   an enum: its name ("BUY"); a value the schema does not name, its number
//   a set: the names of the bits that are on
//   a 32-byte address: "0x" and 64 hex digits
//   any other integer: a number, or a string past 2^53 (a trade id)

const SIZES = { char: 1, int8: 1, uint8: 1, int16: 2, uint16: 2, int32: 4, uint32: 4, float: 4, int64: 8, uint64: 8, double: 8 };
// SBE's null values for an optional field that names none.
const NULLS = {
  int8: -128n, uint8: 255n, int16: -32768n, uint16: 65535n, int32: -2147483648n, uint32: 4294967295n,
  int64: -9223372036854775808n, uint64: 18446744073709551615n,
};

// ---- the schema ----

/** The XML's elements as {name, attrs, children, text}, comments dropped. Enough XML for an SBE schema. */
function parseXml(text) {
  const root = { name: "#root", attrs: {}, children: [], text: "" };
  const stack = [root];
  const tag = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<(\/?)([\w:.-]+)((?:\s+[\w:.-]+\s*=\s*"[^"]*")*)\s*(\/?)>|([^<]+)/g;
  for (const m of text.matchAll(tag)) {
    const [, close, name, attrText, selfClose, chars] = m;
    const top = stack[stack.length - 1];
    if (chars !== undefined) {
      top.text += chars;
    } else if (name === undefined) {
      continue; // a comment or the declaration
    } else if (close) {
      stack.pop();
    } else {
      const attrs = {};
      for (const a of attrText.matchAll(/([\w:.-]+)\s*=\s*"([^"]*)"/g)) attrs[a[1]] = a[2];
      const el = { name: name.replace(/^\w+:/, ""), attrs, children: [], text: "" };
      top.children.push(el);
      if (!selfClose) stack.push(el);
    }
  }
  return root;
}

function primitive(attrs, text) {
  const prim = attrs.primitiveType;
  const length = Number(attrs.length ?? 1);
  const presence = attrs.presence ?? "required";
  return {
    kind: "primitive",
    prim,
    length,
    presence,
    nullValue: attrs.nullValue !== undefined ? BigInt(attrs.nullValue) : presence === "optional" ? NULLS[prim] : undefined,
    constant: presence === "constant" ? Number(text.trim()) : undefined,
    size: presence === "constant" ? 0 : SIZES[prim] * length,
  };
}

function typeDef(el) {
  switch (el.name) {
    case "type":
      return primitive(el.attrs, el.text);
    case "composite": {
      const members = el.children.map((c) => ({ name: c.attrs.name, def: typeDef(c) }));
      return { kind: "composite", members, size: members.reduce((n, m) => n + m.def.size, 0) };
    }
    case "enum": {
      const values = new Map(el.children.map((c) => [Number(c.text.trim()), c.attrs.name]));
      return { kind: "enum", prim: el.attrs.encodingType, values, size: SIZES[el.attrs.encodingType] };
    }
    case "set": {
      const choices = el.children.map((c) => ({ name: c.attrs.name, bit: Number(c.text.trim()) }));
      return { kind: "set", prim: el.attrs.encodingType, choices, size: SIZES[el.attrs.encodingType] };
    }
    default:
      throw new Error(`schema: unknown type element <${el.name}>`);
  }
}

/** One message or group's layout: its fixed fields with offsets, its groups, its variable-length fields. */
function layout(el, types) {
  const fields = [];
  const groups = [];
  const data = [];
  let offset = 0;
  for (const c of el.children) {
    const since = Number(c.attrs.sinceVersion ?? 0);
    if (c.name === "field") {
      const def = types.get(c.attrs.type) ?? (SIZES[c.attrs.type] ? primitive({ primitiveType: c.attrs.type, presence: c.attrs.presence }, "") : null);
      if (!def) throw new Error(`schema: field ${c.attrs.name} has unknown type ${c.attrs.type}`);
      const at = c.attrs.offset !== undefined ? Number(c.attrs.offset) : offset;
      fields.push({ name: c.attrs.name, def, offset: at, since });
      offset = at + def.size;
    } else if (c.name === "group") {
      groups.push({ name: c.attrs.name, dimension: types.get(c.attrs.dimensionType ?? "groupSizeEncoding"), since, ...layout(c, types) });
    } else if (c.name === "data") {
      data.push({ name: c.attrs.name, def: types.get(c.attrs.type), since });
    }
  }
  return { fields, groups, data };
}

/** Read paradex_1_0.xml (its text) into what decode() needs. */
export function parseSchema(xml) {
  const doc = parseXml(xml);
  const schemaEl = doc.children.find((e) => e.name === "messageSchema");
  if (!schemaEl) throw new Error("schema: no <messageSchema>");
  const types = new Map();
  for (const t of schemaEl.children.filter((e) => e.name === "types")) {
    for (const el of t.children) types.set(el.attrs.name, typeDef(el));
  }
  const messages = new Map();
  for (const el of schemaEl.children.filter((e) => e.name === "message")) {
    messages.set(Number(el.attrs.id), { name: el.attrs.name, ...layout(el, types) });
  }
  return {
    id: Number(schemaEl.attrs.id),
    version: Number(schemaEl.attrs.version),
    header: types.get(schemaEl.attrs.headerType ?? "messageHeader"),
    types,
    messages,
  };
}

// ---- a frame ----

function readInt(view, at, prim) {
  switch (prim) {
    case "char":
    case "uint8":
      return BigInt(view.getUint8(at));
    case "int8":
      return BigInt(view.getInt8(at));
    case "uint16":
      return BigInt(view.getUint16(at, true));
    case "int16":
      return BigInt(view.getInt16(at, true));
    case "uint32":
      return BigInt(view.getUint32(at, true));
    case "int32":
      return BigInt(view.getInt32(at, true));
    case "uint64":
      return view.getBigUint64(at, true);
    case "int64":
      return view.getBigInt64(at, true);
    default:
      throw new Error(`no integer read for ${prim}`);
  }
}

function plainNumber(n) {
  return n >= BigInt(Number.MIN_SAFE_INTEGER) && n <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(n) : n.toString();
}

/** mantissa x 10^exponent, exactly, then the nearest number. */
function decimal(mantissa, exponent) {
  if (exponent >= 0) return Number(mantissa * 10n ** BigInt(exponent));
  const neg = mantissa < 0n;
  const digits = (neg ? -mantissa : mantissa).toString().padStart(-exponent + 1, "0");
  const cut = digits.length + exponent;
  return Number(`${neg ? "-" : ""}${digits.slice(0, cut)}.${digits.slice(cut)}`);
}

function readValue(view, at, def) {
  switch (def.kind) {
    case "primitive": {
      if (def.presence === "constant") return def.constant;
      if (def.prim === "float") return view.getFloat32(at, true);
      if (def.prim === "double") return view.getFloat64(at, true);
      if (def.length > 1) {
        const bytes = new Uint8Array(view.buffer, view.byteOffset + at, def.length);
        if (def.prim === "char") return new TextDecoder().decode(bytes).replace(/\0+$/, "");
        return `0x${Buffer.from(bytes).toString("hex")}`;
      }
      const n = readInt(view, at, def.prim);
      if (def.nullValue !== undefined && n === def.nullValue) return null;
      return plainNumber(n);
    }
    case "composite": {
      const [mantissa, exponent] = def.members;
      if (def.members.length === 2 && mantissa.name === "mantissa" && exponent.def.presence === "constant") {
        const n = readInt(view, at, mantissa.def.prim);
        if (mantissa.def.nullValue !== undefined && n === mantissa.def.nullValue) return null;
        return decimal(n, exponent.def.constant);
      }
      const out = {};
      let off = at;
      for (const m of def.members) {
        out[m.name] = readValue(view, off, m.def);
        off += m.def.size;
      }
      return out;
    }
    case "enum": {
      const n = Number(readInt(view, at, def.prim));
      return def.values.get(n) ?? n;
    }
    case "set": {
      const n = Number(readInt(view, at, def.prim));
      return def.choices.filter((c) => (n >> c.bit) & 1).map((c) => c.name);
    }
    default:
      throw new Error(`no read for ${def.kind}`);
  }
}

/** Fixed fields from `at`, a block of `blockLength` bytes, then groups and data. Answers where it ended. */
function readBlock(view, at, blockLength, version, lay, out) {
  for (const f of lay.fields) {
    if (f.since > version || f.offset + f.def.size > blockLength) continue;
    out[f.name] = readValue(view, at + f.offset, f.def);
  }
  let pos = at + blockLength;
  for (const g of lay.groups) {
    if (g.since > version) continue;
    const dim = {};
    let off = pos;
    for (const m of g.dimension.members) {
      dim[m.name] = Number(readInt(view, off, m.def.prim));
      off += m.def.size;
    }
    pos = off;
    out[g.name] = [];
    for (let i = 0; i < dim.numInGroup; i++) {
      const entry = {};
      pos = readBlock(view, pos, dim.blockLength, version, g, entry);
      out[g.name].push(entry);
    }
  }
  for (const d of lay.data) {
    if (d.since > version) continue;
    if (pos >= view.byteLength) break; // appended and not sent: absent
    const [len, bytes] = d.def.members;
    const n = Number(readInt(view, pos, len.def.prim));
    pos += len.def.size;
    if (pos + n > view.byteLength) throw new Error(`frame ends inside ${d.name}`);
    const raw = new Uint8Array(view.buffer, view.byteOffset + pos, n);
    out[d.name] = bytes.def.prim === "char" || bytes.def.prim === "uint8" ? new TextDecoder().decode(raw) : Buffer.from(raw).toString("hex");
    pos += n;
  }
  return pos;
}

/**
 * One frame (an ArrayBuffer, Buffer or Uint8Array) as `{message, ...fields}`,
 * `message` being the schema's name for it ("MarketSummaryEvent"). null for a
 * message the schema does not have: skip it, as Paradex's own decoders do.
 */
export function decode(schema, frame) {
  const bytes = frame instanceof ArrayBuffer ? new Uint8Array(frame) : frame;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const header = {};
  let at = 0;
  for (const m of schema.header.members) {
    header[m.name] = Number(readInt(view, at, m.def.prim));
    at += m.def.size;
  }
  if (header.schemaId !== schema.id) throw new Error(`frame is schema ${header.schemaId}, this reads ${schema.id}`);
  const lay = schema.messages.get(header.templateId);
  if (!lay) return null;
  const out = { message: lay.name };
  readBlock(view, at, header.blockLength, header.version, lay, out);
  return out;
}
