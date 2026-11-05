import { useThemeColors } from "../theme.js";

const KEY = { RATINGS_REVIEWS: "ch-rr", HML: "ch-hml", WOTS: "ch-wots" };

export default function SourceBar({ dist }) {
  const c = useThemeColors();
  if (!dist?.channels?.length) return <p className="muted">Source distribution unavailable.</p>;
  return (
    <div className="sources">
      <div className="source-bar" role="img"
        aria-label={dist.channels.map((ch) => `${ch.label} ${ch.pct}%`).join(", ")}>
        {dist.channels.filter((ch) => ch.pct > 0).map((ch) => (
          <span key={ch.channel} style={{ width: `${ch.pct}%`, background: c[KEY[ch.channel]] }} />
        ))}
      </div>
      <ul className="source-legend">
        {dist.channels.map((ch) => (
          <li key={ch.channel}>
            <span className="swatch" style={{ background: c[KEY[ch.channel]] }} />
            <span className="source-name">
              {ch.label}
              <small>{ch.channel}</small>
            </span>
            <span className="source-pct">{ch.pct.toFixed(1)}%</span>
            <span className="source-n">
              {ch.feedback_units.toLocaleString()} units · {ch.mentions.toLocaleString()} mentions
            </span>
          </li>
        ))}
      </ul>
      <p className="footnote">
        Share of {dist.total_feedback_units.toLocaleString()} unique feedback units (COUNT DISTINCT uuid).
      </p>
    </div>
  );
}
