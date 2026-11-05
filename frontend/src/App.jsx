import { useCallback, useEffect, useRef, useState } from "react";
import { fetchMeta, streamQuery } from "./api.js";
import Sidebar from "./components/Sidebar.jsx";
import Composer from "./components/Composer.jsx";
import Turn from "./components/Turn.jsx";

let nextId = 1;

export default function App() {
  const [meta, setMeta] = useState(null);
  const [metaError, setMetaError] = useState("");
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    fetchMeta().then(setMeta).catch((e) => setMetaError(e.message));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length]);

  const patchTurn = (id, fn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));

  const ask = useCallback(
    async (question) => {
      const q = question.trim();
      if (!q || busy) return;
      const id = nextId++;
      const history = turns.filter((t) => t.result?.type === "analysis").map((t) => t.question).slice(-3);
      setTurns((ts) => [...ts, { id, question: q, steps: [], result: null, error: "" }]);
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamQuery(
          q,
          history,
          (event) => {
            if (event.type === "start") patchTurn(id, (t) => ({ ...t, steps: event.steps }));
            if (event.type === "step")
              patchTurn(id, (t) => ({
                ...t,
                steps: t.steps.map((s) => (s.step === event.step ? { ...s, ...event } : s)),
              }));
            if (event.type === "result") patchTurn(id, (t) => ({ ...t, result: event.data }));
          },
          controller.signal
        );
      } catch (e) {
        if (e.name !== "AbortError") patchTurn(id, (t) => ({ ...t, error: e.message }));
        else patchTurn(id, (t) => ({ ...t, error: "Stopped." }));
      } finally {
        setBusy(false);
        abortRef.current = null;
      }
    },
    [busy, turns]
  );

  return (
    <div className="shell">
      <Sidebar meta={meta} error={metaError} onAsk={ask} busy={busy} turns={turns} />
      <main className="main">
        <div className="thread" aria-live="polite">
          {turns.length === 0 && <Welcome meta={meta} onAsk={ask} />}
          {turns.map((t) => (
            <Turn key={t.id} turn={t} />
          ))}
          <div ref={endRef} />
        </div>
        <Composer onAsk={ask} busy={busy} onStop={() => abortRef.current?.abort()} />
      </main>
    </div>
  );
}

function Welcome({ meta, onAsk }) {
  return (
    <section className="welcome">
      <h1>What are customers telling you?</h1>
      <p>
        Ask a question in plain language. Each answer is built from live queries over{" "}
        {meta ? `${meta.feedback_units.toLocaleString()} pieces of feedback` : "your feedback data"} from reviews,
        support chats and in-store observations, with the numbers, quotes and channel mix behind it.
      </p>
      {meta && (
        <div className="welcome-examples">
          {meta.example_questions.slice(0, 3).map((q) => (
            <button key={q} className="example-card" onClick={() => onAsk(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
