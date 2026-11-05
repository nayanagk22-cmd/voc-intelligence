import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { fmtCell, humanize } from "../format.js";
import { seriesColor, useThemeColors } from "../theme.js";

export default function ChartBlock({ chart, rows }) {
  const c = useThemeColors();
  if (!chart?.x || !chart?.y?.length || !rows?.length) {
    return <p className="muted">No chartable metrics in this result.</p>;
  }
  const { type, x, y } = chart;
  const tooltip = (
    <Tooltip
      formatter={(v, name, item) => [fmtCell(item.dataKey, v), humanize(item.dataKey)]}
      contentStyle={{ background: c.surface, border: `1px solid ${c.line}`, borderRadius: 6, color: c.ink }}
      cursor={{ fill: c.line, opacity: 0.35 }}
    />
  );
  const axis = { stroke: c.muted, fontSize: 12, tickLine: false };
  const legend = y.length > 1 ? <Legend formatter={(v) => humanize(v)} wrapperStyle={{ fontSize: 12 }} /> : null;

  if (type === "pie") {
    return (
      <ResponsiveContainer width="100%" height={300}>
        <PieChart>
          <Pie data={rows} dataKey={y[0]} nameKey={x} innerRadius={60} outerRadius={110} paddingAngle={1}>
            {rows.map((r, i) => (
              <Cell key={r[x]} fill={seriesColor("", c, i)} />
            ))}
          </Pie>
          {tooltip}
          <Legend wrapperStyle={{ fontSize: 12 }} />
        </PieChart>
      </ResponsiveContainer>
    );
  }

  if (type === "line") {
    return (
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid stroke={c.line} vertical={false} />
          <XAxis dataKey={x} {...axis} />
          <YAxis {...axis} />
          {tooltip}
          {legend}
          {y.map((k, i) => (
            <Line key={k} dataKey={k} stroke={seriesColor(k, c, i)} strokeWidth={2} dot={{ r: 3 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    );
  }

  // Horizontal bars keep long theme / retailer names readable.
  const height = Math.max(220, rows.length * (y.length > 1 ? 44 : 32) + 60);
  const stacked = type === "stacked_bar";
  const narrow = typeof window !== "undefined" && window.innerWidth < 640;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={c.line} horizontal={false} />
        <XAxis type="number" {...axis} />
        <YAxis type="category" dataKey={x} width={narrow ? 110 : 230} {...axis} interval={0}
          tickFormatter={(v) => (String(v).length > (narrow ? 16 : 36) ? `${String(v).slice(0, narrow ? 15 : 35)}…` : v)} />
        {tooltip}
        {legend}
        {y.map((k, i) => (
          <Bar key={k} dataKey={k} fill={seriesColor(k, c, i)} stackId={stacked ? "s" : undefined}
            radius={stacked ? 0 : [0, 3, 3, 0]} maxBarSize={22} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
