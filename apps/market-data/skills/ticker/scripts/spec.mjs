// The pieces of a reply that /ticker and /stock both draw: the terminal's
// catalog components, the plain line ahead of the spec, a price chart and a
// valuation multiple.
import { finite } from "./format.mjs";

const ACCENT = "#1bc1cf";

export const card = (label, value, dir) => ({
  component: "metric_card",
  props: { label, value, ...(dir ? { direction: dir } : {}) },
});
export const row = (label, value, dir) => ({
  component: "labeled_output",
  props: { label, value, ...(dir ? { direction: dir } : {}) },
});
export const markdown = (content) => ({ component: "markdown", props: { content } });
export const joined = (...parts) => parts.filter((p) => typeof p === "string" && p !== "").join(" · ");

/** The reply: the line every host shows, then the spec, which the terminal finds from the first brace. */
export const reply = (line, children) =>
  `${line.replace(/[{}]/g, "")}\n\n${JSON.stringify({ layout: "stack", children })}`;

/** A one-series chart, or null with fewer than two points to draw. */
export function chart(label, points, yAxis) {
  const values = points.filter((p) => finite(p.value));
  if (values.length < 2) return null;
  return {
    component: "performance_chart",
    props: { label, yAxis, series: [{ name: label, color: ACCENT, values }] },
  };
}

/** `20x`, `4.5x`. Past 1,000x the denominator is too small for the figure to mean anything. */
export function multiple(n) {
  if (!finite(n) || n <= 0) return null;
  if (n >= 1000) return "over 1,000x";
  return `${n >= 10 ? n.toFixed(0) : n.toFixed(1)}x`;
}
