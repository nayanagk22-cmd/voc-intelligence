export const fmtNumber = (v) =>
  typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : v;

export const isPctKey = (key) => /(_pct|percent|share)$/.test(key);

export function fmtCell(key, value) {
  if (typeof value !== "number") return value;
  if (isPctKey(key)) return `${value.toFixed(1)}%`;
  if (key === "net_sentiment_score") return (value > 0 ? "+" : "") + value.toFixed(1);
  return fmtNumber(value);
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00`);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export const humanize = (key) =>
  String(key).replace(/_pct$/, " %").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export const CHANNEL_LABELS = {
  RATINGS_REVIEWS: "Ratings & Reviews",
  HML: "Chatbot / Support",
  WOTS: "In-store field",
};
