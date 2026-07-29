"use client";

import { useState } from "react";
import { AuroraBackground } from "@/components/AuroraBackground";
import { ResultsChart } from "@/components/ResultsChart";
import { VoiceOrb } from "@/components/VoiceOrb";
import { isWhitespaceMatrixTable, WhitespaceMatrix } from "@/components/WhitespaceMatrix";
import { useVoiceInput } from "@/hooks/useVoiceInput";

type ToolCall = { name: string; arguments: Record<string, unknown> };
type Table = { columns: string[]; rows: Record<string, unknown>[]; note: string } | null;
type AgentResult = {
  narrative: string | null;
  tool_calls: ToolCall[];
  table: Table;
  error: string | null;
};

const EXAMPLE_QUERIES = [
  "Top 10 sponsors for bispecific antibody patent",
  "Sort the ADC patent counts by source country",
  "Compare IP crowding vs clinical progress for HER2",
  "Show me the 2x2 whitespace matrix for prostate cancer",
];

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  async function submitQuery(q: string) {
    if (!q.trim()) return;
    setLoading(true);
    setRequestError(null);
    setResult(null);
    try {
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      });
      const data: AgentResult = await res.json();
      if (!res.ok) {
        setRequestError((data as any).error ?? "Something went wrong."); // eslint-disable-line @typescript-eslint/no-explicit-any
        return;
      }
      setResult(data);
    } catch {
      setRequestError("Network error -- please try again.");
    } finally {
      setLoading(false);
    }
  }

  const { listening, level, supported, error: voiceError, toggle } = useVoiceInput((transcript) => {
    setQuery(transcript);
    submitQuery(transcript);
  });

  return (
    <main className="relative min-h-screen px-4 py-10 text-slate-100 sm:px-6">
      <AuroraBackground />

      <div className="mx-auto flex max-w-4xl flex-col gap-7">
        <header className="flex items-center justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-sky-400/80">
              Data Intelligence
            </p>
            <h1 className="bg-gradient-to-r from-sky-300 via-white to-fuchsia-300 bg-clip-text text-2xl font-semibold text-transparent sm:text-3xl">
              Oncology Antibody IP &amp; Clinical Intelligence
            </h1>
            <p className="mt-1 text-sm text-slate-400">Ask a question in plain English, or use the mic.</p>
          </div>
        </header>

        <div className="flex flex-col items-center gap-4 rounded-3xl border border-white/10 bg-white/[0.04] p-6 shadow-2xl shadow-black/30 backdrop-blur-xl sm:p-8">
          <button
            type="button"
            onClick={toggle}
            aria-label={listening ? "Stop voice input" : "Start voice input"}
            className={`relative flex items-center justify-center rounded-full transition ${
              listening ? "shadow-[0_0_60px_-5px_rgba(244,114,182,0.55)]" : "shadow-[0_0_40px_-10px_rgba(56,189,248,0.4)]"
            }`}
          >
            <VoiceOrb listening={listening} level={level} size={132} />
          </button>
          <p className="text-xs font-medium text-slate-400">
            {listening
              ? "Listening... speak your question"
              : supported
                ? "Tap the orb to speak"
                : "Voice input not supported in this browser"}
          </p>
          {voiceError && (
            <p className="max-w-sm text-center text-xs font-medium text-rose-300">{voiceError}</p>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitQuery(query);
            }}
            className="flex w-full gap-2"
          >
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. Top 10 sponsors for bispecific antibody"
              className="flex-1 rounded-full border border-white/10 bg-black/20 px-4 py-2.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-500 focus:border-sky-400/60 focus:bg-black/30"
            />
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="rounded-full bg-gradient-to-r from-sky-500 to-fuchsia-500 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
            >
              {loading ? "Thinking..." : "Ask"}
            </button>
          </form>

          <div className="flex flex-wrap justify-center gap-2">
            {EXAMPLE_QUERIES.map((q) => (
              <button
                key={q}
                onClick={() => {
                  setQuery(q);
                  submitQuery(q);
                }}
                className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs text-slate-400 transition hover:border-sky-400/40 hover:text-slate-200"
              >
                {q}
              </button>
            ))}
          </div>
        </div>

        {requestError && (
          <div className="animate-fade-in-up rounded-2xl border border-red-500/30 bg-red-950/40 px-4 py-3 text-sm text-red-300 backdrop-blur">
            {requestError}
          </div>
        )}

        {loading && (
          <div className="animate-fade-in-up flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.03] px-5 py-4 text-sm text-slate-400 backdrop-blur">
            <span className="h-2 w-2 animate-ping rounded-full bg-sky-400" />
            Consulting the data analytics agent...
          </div>
        )}

        {result && (
          <div className="animate-fade-in-up flex flex-col gap-5">
            {result.narrative && (
              <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 shadow-lg shadow-black/20 backdrop-blur-xl sm:p-6">
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{result.narrative}</p>
                {result.tool_calls.length > 0 && (
                  <p className="mt-3 border-t border-white/10 pt-3 text-[11px] uppercase tracking-wider text-slate-500">
                    Tool call(s): {result.tool_calls.map((tc) => tc.name).join(", ")}
                  </p>
                )}
              </div>
            )}

            {result.table && result.table.rows.length > 0 && isWhitespaceMatrixTable(result.table.columns) && (
              <div className="flex flex-col gap-2">
                <WhitespaceMatrix columns={result.table.columns} rows={result.table.rows} />
                <p className="rounded-xl border border-white/10 bg-black/20 px-4 py-2.5 text-xs text-slate-500">
                  {result.table.note}
                </p>
              </div>
            )}

            {result.table && result.table.rows.length > 0 && !isWhitespaceMatrixTable(result.table.columns) && (
              <ResultsChart columns={result.table.columns} rows={result.table.rows} />
            )}

            {result.table && result.table.rows.length > 0 && !isWhitespaceMatrixTable(result.table.columns) && (
              <div className="overflow-hidden rounded-2xl border border-white/10 bg-white/[0.03] shadow-lg shadow-black/20 backdrop-blur-xl">
                <div className="max-h-[420px] overflow-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="sticky top-0 z-10 bg-slate-900/95 text-slate-300 backdrop-blur">
                      <tr>
                        {result.table.columns.map((col) => (
                          <th
                            key={col}
                            className="whitespace-nowrap px-3.5 py-2.5 text-xs font-semibold uppercase tracking-wide"
                          >
                            {col.replace(/_/g, " ")}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.table.rows.map((row, i) => (
                        <tr key={i} className="border-t border-white/5 transition hover:bg-sky-500/[0.06]">
                          {result.table!.columns.map((col) => (
                            <td key={col} className="whitespace-nowrap px-3.5 py-2 text-slate-200">
                              {row[col] === null || row[col] === undefined ? "" : String(row[col])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="border-t border-white/10 bg-black/20 px-4 py-2.5 text-xs text-slate-500">
                  {result.table.note}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
