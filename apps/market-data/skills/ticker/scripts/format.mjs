// How /ticker writes numbers. A missing number is null, and every caller skips it.
const finite = (n) => typeof n === "number" && Number.isFinite(n);

const priceFormat = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumSignificantDigits: 7 });
const compactFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumSignificantDigits: 3,
});
const countFormat = new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 3 });

export const price = (n) => (finite(n) ? priceFormat.format(n) : null);
export const money = (n) => (finite(n) ? compactFormat.format(n) : null);
export const count = (n) => (finite(n) ? countFormat.format(n) : null);

/** `+1.2%`, `−8.1%`: a signed percent. */
export function pct(n, digits = 2) {
  if (!finite(n)) return null;
  const text = Math.abs(n).toFixed(digits);
  return `${n < 0 && Number(text) !== 0 ? "−" : "+"}${text}%`;
}

/** `+0.2 pts`: a change in a share that is itself a percent. */
export function pts(n) {
  if (!finite(n)) return null;
  return `${pct(n, 1).slice(0, -1)} pts`;
}

/** `up` or `down` for a signed number, for a value's colour. */
export function direction(n) {
  if (!finite(n) || n === 0) return undefined;
  return n > 0 ? "up" : "down";
}

/** `Nov 13`, a chart point's name. */
export function day(timestamp) {
  const ms = /^\d+$/.test(String(timestamp)) ? Number(timestamp) * 1000 : Date.parse(timestamp);
  if (!Number.isFinite(ms)) return null;
  return new Date(ms).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export { finite };
