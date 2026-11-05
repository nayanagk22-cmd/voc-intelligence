import Pipeline from "./Pipeline.jsx";

// Transparency panel: filters, generated SQL (incl. repair attempts), trace, tokens.
export default function Details({ result }) {
  const { sql, applied_filters: filters, warnings, usage } = result;
  const repaired = sql.attempts.filter((a) => a.error).length;
  return (
    <details className="details">
      <summary>
        How this answer was built
        <span className="muted">
          {" "}
          · {sql.attempts.length} SQL attempt{sql.attempts.length === 1 ? "" : "s"}
          {repaired > 0 && `, ${repaired} self-repaired`}
          {sql.used_fallback && ", standard aggregation used"} · {usage.llm_calls} model calls ·{" "}
          {(usage.prompt_tokens + usage.completion_tokens).toLocaleString()} tokens
        </span>
      </summary>

      <div className="details-body">
        <h4>Filters applied to every block</h4>
        {filters.length ? (
          <ul className="chips">
            {filters.map((f, i) => (
              <li key={i}>
                <code>{f.column}</code> {f.op.replace("_", " ")} {f.values.join(", ")}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">None. The full dataset was analysed.</p>
        )}

        {warnings?.length > 0 && (
          <>
            <h4>Notes</h4>
            <ul className="warnings">
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </>
        )}

        <h4>Workflow</h4>
        <Pipeline steps={result.trace} />

        {sql.attempts.filter((a) => a.error).map((a) => (
          <div key={a.attempt} className="attempt">
            <h4>Attempt {a.attempt} (rejected)</h4>
            <pre>{a.sql || "(no SQL)"}</pre>
            <p className="neg-text">{a.error}</p>
          </div>
        ))}
        <h4>Executed SQL</h4>
        <pre>{sql.final}</pre>
      </div>
    </details>
  );
}
