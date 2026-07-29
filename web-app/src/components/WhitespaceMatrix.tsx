"use client";

import {
  ReferenceArea,
  ReferenceLine,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ResponsiveContainer,
} from "recharts";

type Row = Record<string, unknown>;

const REQUIRED_COLS = ["quadrant", "clinical_performance_Y", "epitope_crowding_X", "target_harmonized"];

export function isWhitespaceMatrixTable(columns: string[]): boolean {
  return REQUIRED_COLS.every((c) => columns.includes(c));
}

const QUADRANTS = {
  "TRUE WHITE SPACE": {
    color: "#34d399",
    corner: { x: [0, 0.5], y: [0.5, 1], position: "insideTopLeft" as const },
    blurb: "Validated biology, open IP",
  },
  BATTLEGROUND: {
    color: "#fbbf24",
    corner: { x: [0.5, 1], y: [0.5, 1], position: "insideTopRight" as const },
    blurb: "Validated biology, contested IP",
  },
  "R&D TRAP": {
    color: "#38bdf8",
    corner: { x: [0, 0.5], y: [0, 0.5], position: "insideBottomLeft" as const },
    blurb: "Unproven biology, open IP",
  },
  "RED FLAGS": {
    color: "#fb7185",
    corner: { x: [0.5, 1], y: [0, 0.5], position: "insideBottomRight" as const },
    blurb: "Unproven biology, contested IP",
  },
} as const;

type QuadrantName = keyof typeof QUADRANTS;

function formatTarget(t: string): string {
  return t.replace(/\|/g, " + ");
}

export function WhitespaceMatrix({ columns, rows }: { columns: string[]; rows: Row[] }) {
  if (!isWhitespaceMatrixTable(columns)) return null;

  const plotted = rows
    .filter((r) => (Object.keys(QUADRANTS) as QuadrantName[]).includes(r.quadrant as QuadrantName))
    .map((r) => ({
      target: String(r.target_harmonized ?? ""),
      quadrant: r.quadrant as QuadrantName,
      x: Number(r.epitope_crowding_X),
      y: Number(r.clinical_performance_Y),
    }))
    .filter((d) => !Number.isNaN(d.x) && !Number.isNaN(d.y));

  if (plotted.length === 0) return null;

  const grouped: Record<QuadrantName, string[]> = {
    "TRUE WHITE SPACE": [],
    BATTLEGROUND: [],
    "R&D TRAP": [],
    "RED FLAGS": [],
  };
  for (const d of plotted) grouped[d.quadrant].push(d.target);

  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4 shadow-lg shadow-black/20 backdrop-blur-xl sm:p-5">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-sky-300/70">
          2x2 whitespace matrix -- {plotted.length} target(s) placed
        </p>
        <ResponsiveContainer width="100%" height={360}>
          <ScatterChart margin={{ top: 8, right: 16, left: 4, bottom: 24 }}>
            {(Object.entries(QUADRANTS) as [QuadrantName, (typeof QUADRANTS)[QuadrantName]][]).map(
              ([name, q]) => (
                <ReferenceArea
                  key={name}
                  x1={q.corner.x[0]}
                  x2={q.corner.x[1]}
                  y1={q.corner.y[0]}
                  y2={q.corner.y[1]}
                  fill={q.color}
                  fillOpacity={0.07}
                  stroke="none"
                  label={{
                    value: name,
                    position: q.corner.position,
                    fill: q.color,
                    fontSize: 10,
                    fontWeight: 700,
                  }}
                />
              )
            )}
            <ReferenceLine x={0.5} stroke="rgba(255,255,255,0.18)" strokeDasharray="4 4" />
            <ReferenceLine y={0.5} stroke="rgba(255,255,255,0.18)" strokeDasharray="4 4" />
            <XAxis
              type="number"
              dataKey="x"
              domain={[0, 1]}
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              label={{ value: "IP / epitope crowding →", position: "insideBottom", offset: -14, fill: "#64748b", fontSize: 11 }}
            />
            <YAxis
              type="number"
              dataKey="y"
              domain={[0, 1]}
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              width={40}
              label={{ value: "Clinical performance →", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 11 }}
            />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const d = payload[0].payload as { target: string; quadrant: string; x: number; y: number };
                return (
                  <div className="rounded-lg border border-white/10 bg-slate-900/95 px-3 py-2 text-xs text-slate-200 shadow-lg">
                    <p className="font-semibold">{formatTarget(d.target)}</p>
                    <p className="text-slate-400">{d.quadrant}</p>
                    <p className="mt-1 text-slate-400">crowding {d.x.toFixed(2)} · performance {d.y.toFixed(2)}</p>
                  </div>
                );
              }}
            />
            {(Object.entries(QUADRANTS) as [QuadrantName, (typeof QUADRANTS)[QuadrantName]][]).map(
              ([name, q]) => (
                <Scatter
                  key={name}
                  name={name}
                  data={plotted.filter((d) => d.quadrant === name)}
                  fill={q.color}
                />
              )
            )}
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {(Object.entries(QUADRANTS) as [QuadrantName, (typeof QUADRANTS)[QuadrantName]][]).map(([name, q]) => (
          <div
            key={name}
            className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 shadow-lg shadow-black/20 backdrop-blur-xl"
          >
            <div className="mb-2 flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: q.color }} />
              <p className="text-sm font-semibold text-slate-100">{name}</p>
              <span className="ml-auto text-xs text-slate-500">{grouped[name].length}</span>
            </div>
            <p className="mb-2 text-[11px] text-slate-500">{q.blurb}</p>
            {grouped[name].length === 0 ? (
              <p className="text-xs text-slate-500">No targets placed here.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {grouped[name].map((t) => (
                  <span
                    key={t}
                    className="rounded-full border border-white/10 bg-white/[0.05] px-2.5 py-1 text-xs text-slate-200"
                  >
                    {formatTarget(t)}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
