export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Dataset = { id: string; tables: string[]; schema: Record<string, string> };

export type Step = {
  index: number;
  thought: string;
  tool: string;
  input: Record<string, unknown>;
  ok: boolean;
  output: string;
  error: string | null;
  duration_ms: number;
  images: string[];
};

export type Final = {
  answer: string;
  value: unknown;
  answerable: boolean;
  assumptions: string[];
  evidence: string[];
};

export type Summary = {
  status: string;
  steps: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  elapsed_s: number;
};

export type AgentEvent =
  | { type: "step"; step: Step }
  | { type: "done"; final: Final | null; summary: Summary }
  | { type: "trace"; trace_id: string }
  | { type: "error"; message: string };

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${r.status})`);
  }
  return r.json();
}

export const getSamples = () => fetch(`${API}/datasets/samples`).then((r) => json<Dataset[]>(r));
export const getHealth = () =>
  fetch(`${API}/health`).then((r) => json<{ samples_only: boolean }>(r));

export function uploadCsv(file: File): Promise<Dataset> {
  const form = new FormData();
  form.append("file", file);
  return fetch(`${API}/datasets`, { method: "POST", body: form }).then((r) => json<Dataset>(r));
}

/** POST /ask returns Server-Sent Events; EventSource only does GET, so parse the stream. */
export async function ask(
  datasetId: string,
  question: string,
  onEvent: (e: AgentEvent) => void,
): Promise<void> {
  const r = await fetch(`${API}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset_id: datasetId, question }),
  });
  if (!r.ok || !r.body) await json(r);
  const reader = r.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      if (chunk.startsWith("data: ")) onEvent(JSON.parse(chunk.slice(6)));
    }
  }
}
