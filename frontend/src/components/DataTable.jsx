import { fmtCell } from "../format.js";

// Adds an inline sentiment-mix bar when the row carries the three sentiment shares.
function SentimentMix({ row }) {
  const parts = [
    ["pos", row.positive_pct],
    ["neu", row.neutral_pct],
    ["neg", row.negative_pct],
  ];
  return (
    <span className="mix" aria-label={`Positive ${row.positive_pct}%, neutral ${row.neutral_pct}%, negative ${row.negative_pct}%`}>
      {parts.map(([k, v]) => (
        <span key={k} className={`mix-${k}`} style={{ width: `${v}%` }} />
      ))}
    </span>
  );
}

export default function DataTable({ table }) {
  const { columns, rows } = table;
  if (!rows?.length) return <p className="muted">The query returned no rows.</p>;
  const hasMix = ["positive_pct", "neutral_pct", "negative_pct"].every((k) => columns.some((c) => c.key === k));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.type === "number" ? "num" : ""} scope="col">
                {c.label}
              </th>
            ))}
            {hasMix && <th scope="col">Sentiment mix</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c, j) =>
                j === 0 ? (
                  <th key={c.key} scope="row">{fmtCell(c.key, r[c.key])}</th>
                ) : (
                  <td key={c.key} className={`${c.type === "number" ? "num" : ""} ${c.key === "net_sentiment_score" ? (r[c.key] < 0 ? "neg-text" : "pos-text") : ""}`}>
                    {fmtCell(c.key, r[c.key])}
                  </td>
                )
              )}
              {hasMix && (
                <td>
                  <SentimentMix row={r} />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
