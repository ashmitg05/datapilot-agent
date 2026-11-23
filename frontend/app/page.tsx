"use client";

import { useEffect, useState } from "react";
import {
  ask,
  getHealth,
  getSamples,
  uploadCsv,
  type Dataset,
  type Final,
  type Step,
  type Summary,
} from "@/lib/api";

const EXAMPLES: Record<string, string[]> = {
  retail: [
    "Which country's revenue dropped the most from Q2 2011 to Q3 2011?",
    "What are the top 3 countries by revenue, excluding the UK?",
    "Which month of 2011 had the highest revenue?",
    "How many distinct cancelled invoices are there?",
    "Chart monthly revenue for 2011",
  ],
  bikes: [
    "By what percent did rentals grow from 2011 to 2012?",
    "Which month has the highest average daily rentals?",
    "What is the correlation between temperature and rentals?",
    "Chart daily rentals over time",
    "What was the longest streak of days above 5000 rentals?",
  ],
  nyc311: [
    "What is the most common complaint type?",
    "Which borough had the most complaints?",
    "What is the median resolution time in hours?",
    "How many complaints have no borough recorded?",
    "Chart complaints by hour of day",
  ],
};
const DEFAULT_EXAMPLES = [
  "Describe this dataset",
  "Which columns have missing values?",
  "What are the top 5 values of the first category column?",
  "Show a chart of the main numeric column",
  "Summarize the key numbers",
];

type Turn = {
  question: string;
  steps: Step[];
  final?: Final | null;
  summary?: Summary;
  error?: string;
};

export default function Home() {
  const [samples, setSamples] = useState<Dataset[]>([]);
  const [samplesOnly, setSamplesOnly] = useState(true);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    getSamples().then(setSamples).catch((e) => setNotice(String(e.message ?? e)));
    getHealth().then((h) => setSamplesOnly(h.samples_only)).catch(() => {});
  }, []);

  async function onUpload(file: File) {
    setNotice("Uploading…");
    try {
      setDataset(await uploadCsv(file));
      setNotice("");
    } catch (e) {
      setNotice((e as Error).message);
    }
  }

  async function submit(q: string) {
    if (!dataset || !q.trim() || busy) return;
    setBusy(true);
    setQuestion("");
    const idx = turns.length;
    setTurns((t) => [...t, { question: q, steps: [] }]);
    const update = (fn: (t: Turn) => Turn) =>
      setTurns((ts) => ts.map((t, i) => (i === idx ? fn(t) : t)));
    try {
      await ask(dataset.id, q, (e) => {
        if (e.type === "step") update((t) => ({ ...t, steps: [...t.steps, e.step] }));
        if (e.type === "done") update((t) => ({ ...t, final: e.final, summary: e.summary }));
        if (e.type === "error") update((t) => ({ ...t, error: e.message }));
      });
    } catch (e) {
      update((t) => ({ ...t, error: (e as Error).message }));
    } finally {
      setBusy(false);
    }
  }

  const examples = (dataset && EXAMPLES[dataset.id]) || DEFAULT_EXAMPLES;

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h1 className="text-2xl font-semibold">Ask a question about your data</h1>
        <div className="flex flex-wrap items-center gap-2">
          {samples.map((s) => (
            <button
              key={s.id}
              onClick={() => setDataset(s)}
              className={`rounded-full border px-3 py-1 text-sm ${
                dataset?.id === s.id
                  ? "border-zinc-900 bg-zinc-900 text-white dark:border-white dark:bg-white dark:text-zinc-900"
                  : "border-zinc-300 dark:border-zinc-700"
              }`}
            >
              {s.id}
            </button>
          ))}
          {!samplesOnly && (
            <label className="cursor-pointer rounded-full border border-dashed border-zinc-400 px-3 py-1 text-sm">
              Upload CSV (max 10 MB)
              <input
                type="file"
                accept=".csv,text/csv"
                className="sr-only"
                onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
              />
            </label>
          )}
        </div>
        {notice && <p className="text-sm text-red-600">{notice}</p>}
        {dataset && (
          <details className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <summary className="cursor-pointer font-medium">
              Schema: {dataset.tables.join(", ")}
            </summary>
            {Object.entries(dataset.schema).map(([t, s]) => (
              <pre key={t} className="mt-2 overflow-x-auto whitespace-pre text-xs">{s}</pre>
            ))}
          </details>
        )}
      </section>

      {turns.map((t, i) => (
        <TurnView key={i} turn={t} />
      ))}

      <section className="sticky bottom-0 space-y-2 bg-inherit pb-4 pt-2">
        {dataset && (
          <div className="flex flex-wrap gap-2">
            {examples.map((q) => (
              <button
                key={q}
                disabled={busy}
                onClick={() => submit(q)}
                className="rounded-full bg-zinc-100 px-3 py-1 text-xs text-zinc-700 hover:bg-zinc-200 disabled:opacity-50 dark:bg-zinc-800 dark:text-zinc-300"
              >
                {q}
              </button>
            ))}
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit(question);
          }}
          className="flex gap-2"
        >
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={500}
            placeholder={dataset ? "Ask in plain English…" : "Pick a dataset first"}
            disabled={!dataset || busy}
            className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-transparent px-3 py-2 dark:border-zinc-700"
          />
          <button
            disabled={!dataset || busy || !question.trim()}
            className="rounded-lg bg-zinc-900 px-4 py-2 text-white disabled:opacity-40 dark:bg-white dark:text-zinc-900"
          >
            {busy ? "Working…" : "Ask"}
          </button>
        </form>
      </section>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  const [showCode, setShowCode] = useState(false);
  const charts = turn.steps.flatMap((s) => s.images);
  const code = turn.steps.filter((s) => s.tool === "run_sql" || s.tool === "run_python");
  return (
    <article className="space-y-3">
      <p className="font-medium">{turn.question}</p>
      <div className="space-y-2">
        {turn.steps.map((s) => (
          <StepCard key={s.index} step={s} />
        ))}
        {!turn.summary && !turn.error && (
          <p className="animate-pulse text-sm text-zinc-500">Thinking…</p>
        )}
      </div>
      {turn.error && <p className="text-sm text-red-600">{turn.error}</p>}
      {turn.summary && (
        <div className="space-y-3 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          {turn.final ? (
            <>
              <p className="whitespace-pre-wrap">{turn.final.answer}</p>
              {turn.final.value !== null && turn.final.value !== undefined && (
                <p className="text-lg font-semibold">
                  {typeof turn.final.value === "object"
                    ? JSON.stringify(turn.final.value)
                    : String(turn.final.value)}
                </p>
              )}
              {turn.final.assumptions.length > 0 && (
                <p className="text-sm text-zinc-500">
                  Assumptions: {turn.final.assumptions.join("; ")}
                </p>
              )}
              {turn.final.evidence.length > 0 && (
                <ul className="list-disc pl-5 text-sm">
                  {turn.final.evidence.map((e, i) => (
                    <li key={i} className="break-words">{e}</li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="text-sm text-zinc-500">No answer ({turn.summary.status}).</p>
          )}
          {charts.map((img, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={i} src={`data:image/png;base64,${img}`} alt="Chart" className="w-full rounded" />
          ))}
          {code.length > 0 && (
            <button onClick={() => setShowCode(!showCode)} className="text-sm underline">
              {showCode ? "Hide the code" : "Show the code"}
            </button>
          )}
          {showCode &&
            code.map((s) => (
              <pre key={s.index} className="overflow-x-auto rounded bg-zinc-100 p-2 text-xs dark:bg-zinc-900">
                {String(s.input.query ?? s.input.code)}
              </pre>
            ))}
          <p className="text-xs text-zinc-500">
            {turn.summary.steps} steps · {turn.summary.input_tokens + turn.summary.output_tokens}{" "}
            tokens · ${turn.summary.cost_usd.toFixed(4)} · {turn.summary.elapsed_s.toFixed(1)}s
          </p>
        </div>
      )}
    </article>
  );
}

function StepCard({ step }: { step: Step }) {
  const body = step.input.query ?? step.input.code ?? step.input.table ?? step.input.spec;
  return (
    <details
      className={`rounded-lg border p-2 text-sm ${
        step.ok ? "border-zinc-200 dark:border-zinc-800" : "border-amber-400"
      }`}
    >
      <summary className="cursor-pointer">
        <span className="font-mono">{step.tool}</span>
        <span className="ml-2 text-xs text-zinc-500">
          {step.ok ? "ok" : "error, retrying"} · {step.duration_ms} ms
        </span>
      </summary>
      {step.thought && <p className="mt-2 text-zinc-500">{step.thought}</p>}
      {body !== undefined && (
        <pre className="mt-2 overflow-x-auto rounded bg-zinc-100 p-2 text-xs dark:bg-zinc-900">
          {typeof body === "string" ? body : JSON.stringify(body, null, 2)}
        </pre>
      )}
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre text-xs">
        {step.ok ? step.output : step.error}
      </pre>
    </details>
  );
}
