// What /ticker reads as symbols, shared by command.mjs and lookup.mjs so the
// suggestions offer only what /ticker accepts.
export const SYMBOL = /^[a-z0-9]+$/;

const SEPARATOR = /[\s,]+/;
const word = (w) => w.replace(/^\$/, "");

/** The words of what was typed, lowercased, split on spaces and commas, `$` dropped. */
export function symbolWords(text) {
  return (text ?? "").toLowerCase().split(SEPARATOR).map(word).filter(Boolean);
}

/** The word still being typed: empty right after a separator, or a lone `$`. */
export function partialWord(text) {
  return word((text ?? "").toLowerCase().split(SEPARATOR).pop());
}
