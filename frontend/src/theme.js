import { useEffect, useState } from "react";

// Recharts writes colors into SVG attributes, where CSS variables do not
// resolve, so read the current theme's tokens and re-read on scheme change.
const TOKENS = ["--ink", "--muted", "--line", "--accent", "--pos", "--neg", "--neu", "--ch-rr", "--ch-hml", "--ch-wots", "--surface", "--series-4", "--series-5"];

function read() {
  const cs = getComputedStyle(document.documentElement);
  return Object.fromEntries(TOKENS.map((t) => [t.slice(2), cs.getPropertyValue(t).trim()]));
}

export function useThemeColors() {
  const [colors, setColors] = useState(read);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setColors(read());
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return colors;
}

export function seriesColor(key, colors, index) {
  if (/positive/.test(key)) return colors.pos;
  if (/negative|churn/.test(key)) return colors.neg;
  if (/neutral/.test(key)) return colors.neu;
  const palette = [colors.accent, colors["ch-rr"], colors["ch-wots"], colors["series-4"], colors["series-5"]];
  return palette[index % palette.length];
}
