// API client. Analytical answers stream over Server-Sent Events so the UI can
// show each workflow step as it happens.

export async function fetchMeta() {
  const res = await fetch("/api/meta");
  if (!res.ok) throw new Error(`Could not load dataset info (HTTP ${res.status})`);
  return res.json();
}

export async function streamQuery(question, history, onEvent, signal) {
  const res = await fetch("/api/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history }),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail?.[0]?.msg || body.detail || detail;
    } catch {
      /* keep status text */
    }
    throw new Error(String(detail));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}
