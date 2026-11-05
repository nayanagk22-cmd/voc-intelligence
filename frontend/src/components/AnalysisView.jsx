import { fmtDate } from "../format.js";
import ChartBlock from "./ChartBlock.jsx";
import SourceBar from "./SourceBar.jsx";
import DataTable from "./DataTable.jsx";
import Quotes from "./Quotes.jsx";
import Takeaways from "./Takeaways.jsx";
import Details from "./Details.jsx";

// Renders the 8-part UI output contract for one analytical answer.
export default function AnalysisView({ result }) {
  const { period, slice_stats: s, kpi } = result;
  const tone = s.net_sentiment_score < 0 ? "neg" : s.net_sentiment_score > 0 ? "pos" : "neu";
  // KPI colour follows what the metric measures, not the overall slice mood.
  const kpiText = `${kpi?.label || ""} ${kpi?.context || ""}`.toLowerCase();
  const kpiTone = /negative|complain|churn|risk|drain|issue/.test(kpiText) ? "neg" : /positive|praise/.test(kpiText) ? "pos" : "neu";
  return (
    <div className="analysis">
      {/* 1. Analysis period banner */}
      <div className="period" role="note">
        <span className="period-range">
          {fmtDate(period.start)} – {fmtDate(period.end)}
        </span>
        <span>{s.feedback_units.toLocaleString()} feedback units</span>
        <span>{s.mentions.toLocaleString()} topic mentions</span>
        <span className={`nss ${tone}`}>
          Net sentiment {s.net_sentiment_score > 0 ? "+" : ""}
          {s.net_sentiment_score}
        </span>
      </div>

      {/* 2. Executive summary + 3. KPI callout */}
      <section className="briefing">
        <div className="briefing-text">
          {result.headline && <h2 className="headline">{result.headline}</h2>}
          <p className="summary">{result.executive_summary}</p>
        </div>
        {kpi?.value && (
          <aside className={`kpi ${kpiTone}`} aria-label="Standout metric">
            <span className="kpi-value">{kpi.value}</span>
            <span className="kpi-label">{kpi.label}</span>
            {kpi.context && <span className="kpi-context">{kpi.context}</span>}
          </aside>
        )}
      </section>

      {/* 5. Chart + 7. Source distribution */}
      <section className="row-2">
        <div className="panel">
          <h3>{result.chart?.title || "Breakdown"}</h3>
          <ChartBlock chart={result.chart} rows={result.table.rows} />
        </div>
        <div className="panel">
          <h3>Where this feedback comes from</h3>
          <SourceBar dist={result.source_distribution} />
        </div>
      </section>

      {/* 4. Summary table */}
      <section className="panel">
        <h3>Summary table</h3>
        <DataTable table={result.table} />
      </section>

      {/* 6. Verbatims */}
      <section>
        <h3 className="section-title">In customers' words</h3>
        <Quotes quotes={result.verbatims} />
      </section>

      {/* 8. Takeaways */}
      <section>
        <h3 className="section-title">What to do next</h3>
        <Takeaways items={result.takeaways} />
      </section>

      <Details result={result} />
    </div>
  );
}
