import React, { useEffect, useState } from "react";
import { fetchRemeasurements } from "../api";

const CHIP_CLASS = {
  survivor: "ok",
  survivor_zero: "zero",
  dead: "dead",
  missing: "missing",
  ingrowth: "ingrowth",
  renumbered: "renumbered",
  conflict: "conflict",
  possible_missed: "conflict",
  subthreshold: "muted",
  dead_without_t1: "conflict",
};

const fmt = (x) => (x === null || x === undefined ? "—" : Number(x).toFixed(1));

/** 单块样地的两期复测匹配明细。 */
export default function RemeasurementTable({ plotId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    fetchRemeasurements(plotId).then(setData).catch((e) => setError(String(e)));
  }, [plotId]);

  if (error) return <div className="banner error">{error}</div>;
  if (!data) return <div className="banner">加载复测明细…</div>;

  return (
    <div>
      <h3>样地 {plotId} · 个体复测（{data.outcomes.length} 条）</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>类别</th>
            <th>初测编号</th>
            <th>复测编号</th>
            <th>树种</th>
            <th>胸径₁ (cm)</th>
            <th>胸径₂ (cm)</th>
            <th>ΔD</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {data.outcomes.map((o, i) => {
            const d1 = o.t1?.dbh_cm;
            const d2 = o.t2?.dbh_cm;
            const delta = d1 != null && d2 != null ? (d2 - d1).toFixed(1) : "—";
            return (
              <tr key={i} className={o.needs_verification ? "row-verify" : ""}>
                <td>
                  <span className={`chip ${CHIP_CLASS[o.category] || ""}`}>
                    {o.category_label}
                  </span>
                </td>
                <td className="mono">{o.t1?.tree_no || "—"}</td>
                <td className="mono">{o.t2?.tree_no || "—"}</td>
                <td>{o.t1?.species || o.t2?.species}</td>
                <td className="num">{fmt(d1)}</td>
                <td className="num">{fmt(d2)}</td>
                <td className="num">{delta}</td>
                <td className="note">{o.note}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="hint">
        真实零生长 = 两期均实测且 ΔD≤0.05 cm；缺测 ≠ 死亡；位置矛盾与疑似漏测在人工核实前不参与任何分量。
      </p>
    </div>
  );
}
