"use client";

import { useEffect, useState } from "react";

type Cell = { mean: number | null; std: number };
type Summary = Record<string, Record<string, Cell>>;

const LOOP = [
  ["Question", "User asks in plain English about a sample or uploaded CSV."],
  ["Inspect", "describe_schema: columns, types, null counts, stats, 5 sample rows."],
  ["Act", "run_sql (DuckDB, read-only), run_python (sandbox) or make_chart."],
  ["Observe", "The tool result or error goes back to the model as the next message."],
  ["Correct", "On error the model gets the message plus a hint; up to 3 retries."],
  ["Verify", "Optional: re-check key numbers with an independent query."],
  ["Answer", "final_answer with value, assumptions and evidence; trace saved."],
];

const fmt = (c?: Cell, money = false) =>
  !c || c.mean === null
    ? "n/a"
    : money
      ? `$${c.mean.toFixed(4)}`
      : `${c.mean.toFixed(1)} ± ${c.std.toFixed(1)}`;

export default function HowItWorks() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    fetch("/benchmark/summary.json")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setSummary)
      .catch(() => setMissing(true));
  }, []);

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h1 className="text-2xl font-semibold">How it works</h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          DataPilot is a hand-written agent loop on top of native LLM tool calling: no agent
          framework. Each step picks a tool, runs it, and feeds the observation back.
        </p>
        <ol className="grid gap-2 sm:grid-cols-2">
          {LOOP.map(([title, text], i) => (
            <li key={title} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="font-medium">
                {i + 1}. {title}
              </p>
              <p className="text-sm text-zinc-600 dark:text-zinc-400">{text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="space-y-3">
        <h2 className="text-xl font-semibold">Sandbox</h2>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>SQL: parsed with sqlglot (one SELECT/WITH/DESCRIBE over known tables only).</li>
          <li>DuckDB opened read-only with external file and network access disabled.</li>
          <li>Python: separate container, no network, read-only filesystem, non-root user.</li>
          <li>1 CPU, 512 MB memory, 64 processes, 15 s wall clock; outputs truncated.</li>
        </ul>
      </section>

      <section className="space-y-3">
        <h2 className="text-xl font-semibold">Benchmark</h2>
        {missing && (
          <p className="text-sm text-zinc-500">
            No benchmark run published yet. Results appear here after a saved run.
          </p>
        )}
        {summary && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-zinc-200 dark:border-zinc-800">
                  {["Config", "Success %", "Refusal %", "Steps", "Cost / q"].map((h) => (
                    <th key={h} className="py-2 pr-4 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(summary).map(([config, m]) => (
                  <tr key={config} className="border-b border-zinc-100 dark:border-zinc-900">
                    <td className="py-2 pr-4 font-mono">{config}</td>
                    <td className="pr-4">{fmt(m.success_rate)}</td>
                    <td className="pr-4">{fmt(m.correct_refusal_rate)}</td>
                    <td className="pr-4">{fmt(m.avg_steps)}</td>
                    <td className="pr-4">{fmt(m.cost_per_question, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
