import { fmtDate } from "../format.js";

export default function Sidebar({ meta, error, onAsk, busy, turns }) {
  const asked = turns.map((t) => t.question).reverse();
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <div>
          <div className="brand-name">VOC 360</div>
          <div className="brand-sub">Customer intelligence</div>
        </div>
      </div>

      {error && <p className="sidebar-error">Backend unreachable: {error}</p>}

      {meta && (
        <dl className="facts">
          <div>
            <dt>Feedback units</dt>
            <dd>{meta.feedback_units.toLocaleString()}</dd>
          </div>
          <div>
            <dt>Topic mentions</dt>
            <dd>{meta.rows.toLocaleString()}</dd>
          </div>
          <div className="wide">
            <dt>Coverage</dt>
            <dd>
              {fmtDate(meta.date_range.start)} to {fmtDate(meta.date_range.end)}
            </dd>
          </div>
          <div className="wide">
            <dt>Brands</dt>
            <dd>{meta.brands.join(", ")}</dd>
          </div>
        </dl>
      )}

      {meta && !meta.llm_configured && (
        <p className="sidebar-error">OPENAI_API_KEY is not set. Greetings work; analysis needs the key in .env.</p>
      )}

      {meta && (
        <nav className="side-section" aria-label="Example questions">
          <h2>Try asking</h2>
          {meta.example_questions.map((q) => (
            <button key={q} className="side-link" disabled={busy} onClick={() => onAsk(q)}>
              {q}
            </button>
          ))}
        </nav>
      )}

      {asked.length > 0 && (
        <nav className="side-section" aria-label="This session">
          <h2>This session</h2>
          {asked.slice(0, 8).map((q, i) => (
            <button key={`${q}-${i}`} className="side-link quiet" disabled={busy} onClick={() => onAsk(q)}>
              {q}
            </button>
          ))}
        </nav>
      )}
    </aside>
  );
}
