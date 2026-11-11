// What /ticker reads as symbols, shared by command.mjs and lookup.mjs so the
// suggestions offer only what /ticker accepts.
export const SYMBOL = /^[a-z0-9]+$/;

/** The words of what was typed, lowercased, split on spaces and commas, `$` dropped. */
export function symbolWords(text) {
  return (text ?? "")
    .toLowerCase()
    .split(/[\s,]+/)
    .map((word) => word.replace(/^\$/, ""))
    .filter(Boolean);
}
