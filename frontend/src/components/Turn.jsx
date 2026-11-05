import Pipeline from "./Pipeline.jsx";
import AnalysisView from "./AnalysisView.jsx";

export default function Turn({ turn }) {
  const { question, steps, result, error } = turn;
  const running = !result && !error;
  return (
    <article className="turn">
      <p className="turn-question">{question}</p>

      {running && <Pipeline steps={steps} live />}

      {error && <div className="notice error">Request failed: {error}</div>}

      {result?.type === "conversation" && <div className="reply">{result.message}</div>}

      {result?.type === "empty" && (
        <div className="notice">
          {result.message}
          {result.warnings?.length > 0 && (
            <ul>
              {result.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {result?.type === "error" && (
        <div className="notice error">
          {result.message}
          <span className="notice-ref">Request {result.request_id}</span>
        </div>
      )}

      {result?.type === "analysis" && <AnalysisView result={result} />}
    </article>
  );
}
