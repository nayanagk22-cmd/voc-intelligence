import { CHANNEL_LABELS, fmtDate } from "../format.js";

function Stars({ n }) {
  if (n == null) return <span className="stars muted">No rating</span>;
  return (
    <span className="stars" aria-label={`${n} out of 5 stars`}>
      {"★".repeat(n)}
      <span className="stars-off">{"★".repeat(5 - n)}</span>
    </span>
  );
}

export default function Quotes({ quotes }) {
  if (!quotes?.length) return <p className="muted">No quotes matched this slice.</p>;
  return (
    <div className="quotes">
      {quotes.map((q) => (
        <figure key={q.uuid} className={`quote ${q.sentiment?.toLowerCase()}`}>
          <blockquote>{q.customer_voice}</blockquote>
          <figcaption>
            <span className={`badge ${q.sentiment?.toLowerCase()}`}>{q.sentiment}</span>
            <span className={`channel ch-${q.data_source_type}`}>{CHANNEL_LABELS[q.data_source_type] || q.data_source_type}</span>
            <Stars n={q.customer_rating} />
            <span className="quote-meta">
              {q.retailer_name} · {q.product_family} · {q.subtheme} · {fmtDate(q.engagement_date)}
            </span>
          </figcaption>
        </figure>
      ))}
    </div>
  );
}
