import { useState } from "react";

export default function Composer({ onAsk, busy, onStop }) {
  const [text, setText] = useState("");
  const submit = () => {
    if (!text.trim() || busy) return;
    onAsk(text);
    setText("");
  };
  return (
    <div className="composer">
      <label htmlFor="question" className="sr-only">
        Ask about customer feedback
      </label>
      <textarea
        id="question"
        rows={2}
        maxLength={1000}
        value={text}
        placeholder="Ask about complaints, sentiment, retailers, features or trends…"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      {busy ? (
        <button className="btn secondary" onClick={onStop}>
          Stop
        </button>
      ) : (
        <button className="btn" onClick={submit} disabled={!text.trim()}>
          Analyze
        </button>
      )}
    </div>
  );
}
