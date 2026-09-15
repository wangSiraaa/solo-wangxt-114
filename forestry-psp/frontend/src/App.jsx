import React, { useEffect, useState } from "react";
import { fetchEstimates, fetchPlots, fetchTickets } from "./api";
import PlotMap from "./components/PlotMap";
import RemeasurementTable from "./components/RemeasurementTable";
import EstimatePanel from "./components/EstimatePanel";
import TicketList from "./components/TicketList";

const TABS = [
  ["remeasure", "个体复测"],
  ["estimate", "总体估计"],
  ["tickets", "待核实"],
];

export default function App() {
  const [plots, setPlots] = useState(null);
  const [estimates, setEstimates] = useState(null);
  const [tickets, setTickets] = useState(null);
  const [selectedPlot, setSelectedPlot] = useState(null);
  const [tab, setTab] = useState("remeasure");
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([fetchPlots(), fetchEstimates(), fetchTickets()])
      .then(([p, e, t]) => {
        setPlots(p);
        setEstimates(e);
        setTickets(t);
        if (p.length) setSelectedPlot(p[0].plot_id);
      })
      .catch((err) => setError(String(err)));
  }, []);

  if (error) return <div className="banner error">加载失败：{error}</div>;
  if (!plots || !estimates || !tickets)
    return <div className="banner">正在加载调查数据…</div>;

  const version = estimates.survey_version;
  const openTickets = tickets.filter((t) => t.status === "open").length;

  return (
    <div className="app">
      <header>
        <h1>固定样地两期复测分析</h1>
        <div className="version-banner">
          <span className="badge confirmed">已确认</span>
          <span>{version.name}</span>
          <span className="mono">
            方程集 {version.equation_set_id} · 哈希{" "}
            {version.equation_set_hash.slice(0, 12)}…
          </span>
          <span>
            {version.survey_t1} → {version.survey_t2}（间隔{" "}
            {version.interval_years} 年 · 起测胸径 {version.dbh_threshold_cm}{" "}
            cm · 位置容差 {version.position_tolerance_m} m）
          </span>
        </div>
      </header>
      <main>
        <section className="map-panel">
          <h2>样地位置（{plots.length} 块，面积不等）</h2>
          <PlotMap
            plots={plots}
            selectedId={selectedPlot}
            onSelect={(id) => {
              setSelectedPlot(id);
              setTab("remeasure");
            }}
          />
          <p className="hint">
            点击样地查看个体复测明细。权重 = 1/入样概率，由抽样设计给定。
          </p>
        </section>
        <section className="side-panel">
          <nav className="tabs">
            {TABS.map(([key, label]) => (
              <button
                key={key}
                className={tab === key ? "active" : ""}
                onClick={() => setTab(key)}
              >
                {label}
                {key === "tickets" && openTickets > 0 && (
                  <span className="badge warn">{openTickets}</span>
                )}
              </button>
            ))}
          </nav>
          {tab === "remeasure" && selectedPlot && (
            <RemeasurementTable plotId={selectedPlot} />
          )}
          {tab === "estimate" && <EstimatePanel estimates={estimates} />}
          {tab === "tickets" && <TicketList tickets={tickets} />}
        </section>
      </main>
      <footer>
        数据来源：{estimates.provenance?.survivor_growth?.source}。
        全部数据为虚构演示数据；估计按抽样设计加权，非简单平均×面积。
      </footer>
    </div>
  );
}
