"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Row = Record<string, unknown>;

const PALETTE = ["#38bdf8", "#818cf8", "#f472b6", "#34d399", "#fbbf24", "#fb7185", "#a78bfa", "#22d3ee", "#4ade80", "#facc15"];

function isNumericColumn(rows: Row[], col: string): boolean {
  let sawValue = false;
  for (const r of rows) {
    const v = r[col];
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "number") {
      sawValue = true;
      continue;
    }
    if (typeof v === "boolean" || Array.isArray(v) || (typeof v === "object" && v !== null)) return false;
    if (Number.isNaN(Number(v))) return false;
    sawValue = true;
  }
  return sawValue;
}

// Only chart columns that look like an aggregated metric (a count/score/performance value) --
// NOT arbitrary numeric fields like filing_year that happen to be numbers but aren't a quantity
// worth graphing. This keeps the chart limited to meaningful aggregate views (top sponsors,
// group breakdowns, whitespace scores) and skips it for raw per-record listings.
const CHARTABLE_VALUE_PATTERN = /count|patent(s)?_grounded|n_trials|clinical_performance|epitope_crowding|_score$/i;

// Known sponsor names shortened to their common brand name for chart axis labels only
// (the full legal name still appears in the table and narrative text).
const SPONSOR_ALIASES: [RegExp, string][] = [[/hoffmann-la roche/i, "Roche"]];

function shortenSponsorName(name: string): string {
  for (const [pattern, alias] of SPONSOR_ALIASES) {
    if (pattern.test(name)) return alias;
  }
  return name;
}

function pickChartColumns(columns: string[], rows: Row[]): { label: string; value: string } | null {
  if (rows.length < 2) return null; // nothing meaningful to compare with a single row
  const numericCols = columns.filter((c) => isNumericColumn(rows, c));
  const preferredValue = numericCols.find((c) => CHARTABLE_VALUE_PATTERN.test(c));
  if (!preferredValue) return null;
  const labelCol =
    columns.find((c) => c !== preferredValue && typeof rows[0][c] === "string") ??
    columns.find((c) => c !== preferredValue) ??
    columns[0];
  return { label: labelCol, value: preferredValue };
}

export function ResultsChart({ columns, rows }: { columns: string[]; rows: Row[] }) {
  const picked = pickChartColumns(columns, rows);
  if (!picked) return null;

  const data = rows
    .slice(0, 12)
    .map((r) => ({ name: String(r[picked.label] ?? ""), value: Number(r[picked.value]) || 0 }));
  if (data.every((d) => d.value === 0)) return null;

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4 shadow-lg shadow-black/20 backdrop-blur-xl sm:p-5">
      <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-sky-300/70">
        {picked.value.replace(/_/g, " ")} by {picked.label.replace(/_/g, " ")}
      </p>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ top: 4, right: 12, left: 20, bottom: 56 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.07)" vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            tickFormatter={(value: string) => {
              const short = shortenSponsorName(value);
              return short.length > 16 ? `${short.slice(0, 15)}\u2026` : short;
            }}
            angle={-35}
            textAnchor="end"
            interval={0}
            height={70}
          />
          <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} width={40} allowDecimals={false} />
          <Tooltip
            cursor={{ fill: "rgba(255,255,255,0.05)" }}
            contentStyle={{
              background: "rgba(15,23,42,0.95)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 10,
              fontSize: 12,
            }}
            labelStyle={{ color: "#e2e8f0" }}
            itemStyle={{ color: "#38bdf8" }}
          />
          <Bar dataKey="value" radius={[6, 6, 0, 0]} maxBarSize={48}>
            {data.map((_, i) => (
              <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
