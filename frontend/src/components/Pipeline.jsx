// Live view of the workflow. The three middle branches run concurrently on the
// backend, so they are drawn side by side.
const PARALLEL = new Set(["sql", "verbatims", "sources"]);

function Step({ s }) {
  return (
    <li className={`step ${s.status}`}>
      <span className="step-dot" aria-hidden="true" />
      <span className="step-label">{s.label}</span>
      {s.status === "done" && (
        <span className="step-detail">
          {s.detail}
          {s.ms > 0 && ` · ${s.ms} ms`}
        </span>
      )}
      {s.status === "error" && <span className="step-detail">{s.detail}</span>}
      {s.status === "skipped" && <span className="step-detail">skipped</span>}
    </li>
  );
}

export default function Pipeline({ steps, live = false }) {
  if (!steps?.length) return live ? <p className="muted">Starting analysis…</p> : null;
  const before = steps.filter((s) => ["route", "plan", "slice"].includes(s.step));
  const branches = steps.filter((s) => PARALLEL.has(s.step));
  const after = steps.filter((s) => s.step === "synthesize");
  return (
    <div className={`pipeline ${live ? "live" : ""}`}>
      <ol className="steps">
        {before.map((s) => (
          <Step key={s.step} s={s} />
        ))}
      </ol>
      <ol className="steps parallel" aria-label="Parallel branches">
        {branches.map((s) => (
          <Step key={s.step} s={s} />
        ))}
      </ol>
      <ol className="steps">
        {after.map((s) => (
          <Step key={s.step} s={s} />
        ))}
      </ol>
    </div>
  );
}
