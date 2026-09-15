import React, { useCallback, useEffect, useState } from "react";
import { fetchPlots, fetchTickets, fetchVersions } from "./api";
import PlotMap from "./components/PlotMap";
import RemeasurementTable from "./components/RemeasurementTable";
import EstimatePanel from "./components/EstimatePanel";
import TicketList from "./components/TicketList";
import BatchPanel from "./components/BatchPanel";
import ComparePanel from "./components/ComparePanel";

const TABS = [
  ["remeasure", "个体复测"],
  ["estimate", "总体估计"],
  ["compare", "版本比较"],
  ["batches", "修订批次"],
  ["tickets", "待核实"],
];

export default function App() {
  const [plots, setPlots] = useState(null);
  const [versions, setVersions] = useState(null);
  const [tickets, setTickets] = useState(null);
  const [selectedPlot, setSelectedPlot] = useState(null);
  const [selectedVersion, setSelectedVersion] = useState(null);
  const [tab, setTab] = useState("remeasure");
  const [error, setError] = useState(null);

  const refresh = useCallback(() => {
    return Promise.all([fetchPlots(), fetchVersions(), fetchTickets()])
      .then(([p, v, t]) => {
        setPlots(p);
        setVersions(v);
        setTickets(t);
        if (p.length && !selectedPlot) setSelectedPlot(p[0].plot_id);
        // 默认选中最新已确认版（基线）
        setSelectedVersion((cur) => {
          if (cur && v.some((x) => x.id === cur)) return cur;
          const confirmed = v.filter((x) => x.confirmed);
          return (confirmed[0] || v[0])?.id ?? null;
        });
      })
      .catch((err) => setError(String(err)));
  }, [selectedPlot]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <div className="banner error">加载失败：{error}</div>;
  if (!plots || !versions || !tickets)
    return <div className="banner">正在加载调查数据…</div>;

  const version = versions.find((v) => v.id === selectedVersion);
  const openTickets = tickets.filter((t) => t.status === "open").length;

  return (
    <div className="app">
      <header>
        <h1>固定样地两期复测分析</h1>
        <div className="version-banner">
          <label>当前调查版：</label>
          <select
            value={selectedVersion || ""}
            onChange={(e) => setSelectedVersion(Number(e.target.value))}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                #{v.id} {v.name}
                {v.confirmed ? "（已确认）" : "（草稿）"}
              </option>
            ))}
          </select>
          {version && (
            <>
              <span className={`badge ${version.confirmed ? "confirmed" : "warn"}`}>
                {version.confirmed ? "已确认" : "草稿"}
              </span>
              <span className="mono">
                方程集哈希 {version.equation_set_hash?.slice(0, 12) || "未确认"}…
              </span>
              {version.base_version && (
                <span className="mono">修订自 #{version.base_version}</span>
              )}
            </>
          )}
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
            <RemeasurementTable plotId={selectedPlot} versionId={selectedVersion} />
          )}
          {tab === "estimate" && <EstimatePanel versionId={selectedVersion} />}
          {tab === "compare" && <ComparePanel versions={versions} />}
          {tab === "batches" && (
            <BatchPanel
              versions={versions}
              tickets={tickets}
              onChanged={refresh}
            />
          )}
          {tab === "tickets" && <TicketList tickets={tickets} />}
        </section>
      </main>
      <footer>
        全部数据为虚构演示数据；估计按抽样设计加权（Horvitz–Thompson），非简单平均×面积。
        已确认调查版、方程集快照与已发布估计始终可查询且不可覆盖；修订只发生在新的草稿版上。
      </footer>
    </div>
  );
}
