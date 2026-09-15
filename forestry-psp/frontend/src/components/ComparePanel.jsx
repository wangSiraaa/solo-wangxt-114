import React, { useEffect, useState } from "react";
import { fetchCompare } from "../api";

const COMPONENT_LABELS = {
  survivor_growth: "保留木生长",
  ingrowth: "进界",
  mortality: "死亡",
  net_change: "净变化",
};
const RESPONSE_LABELS = {
  basal_area_m2: "断面积 (m²)",
  biomass_kg: "生物量 (kg)",
  volume_m3: "蓄积 (m³)",
};

const fmt = (x, d = 4) =>
  x === null || x === undefined ? "—" : Number(x).toFixed(d);

/** 版本比较：基线版 vs 修订版，三类响应量 × 四个分量的差异。 */
export default function ComparePanel({ versions }) {
  const confirmed = versions.filter((v) => v.confirmed);
  const revisions = versions.filter((v) => !v.confirmed || v.base_version);
  const [baseId, setBaseId] = useState(confirmed[0]?.id);
  const [revisionId, setRevisionId] = useState(revisions[0]?.id);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!baseId || !revisionId) return;
    setData(null);
    setError(null);
    fetchCompare(baseId, revisionId).then(setData).catch((e) => setError(String(e)));
  }, [baseId, revisionId]);

  if (versions.length < 2)
    return (
      <div className="banner">
        尚无可比较的修订版。请先在"修订批次"页创建并应用批次。
      </div>
    );

  return (
    <div>
      <h3>版本比较（基线 vs 修订）</h3>
      <div className="form-grid">
        <label>基线版</label>
        <select value={baseId || ""} onChange={(e) => setBaseId(Number(e.target.value))}>
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              #{v.id} {v.name}
              {v.confirmed ? "（已确认）" : "（草稿）"}
            </option>
          ))}
        </select>
        <label>修订版</label>
        <select
          value={revisionId || ""}
          onChange={(e) => setRevisionId(Number(e.target.value))}
        >
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              #{v.id} {v.name}
              {v.confirmed ? "（已确认）" : "（草稿）"}
            </option>
          ))}
        </select>
      </div>
      {error && <div className="banner error">{error}</div>}
      {!data && !error && <div className="banner">计算差异…</div>}
      {data && (
        <>
          {Object.entries(COMPONENT_LABELS).map(([comp, label]) => (
            <div key={comp}>
              <h4>{label}</h4>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>响应量</th>
                    <th>基线值</th>
                    <th>修订值</th>
                    <th>差值</th>
                    <th>相对变化</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(RESPONSE_LABELS).map(([resp, rlabel]) => {
                    const d = data.diff[comp][resp];
                    return (
                      <tr key={resp}>
                        <td>{rlabel}</td>
                        <td className="num">{fmt(d.base, 3)}</td>
                        <td className="num">{fmt(d.revision, 3)}</td>
                        <td className={`num ${d.abs > 0 ? "pos" : d.abs < 0 ? "neg" : ""}`}>
                          {d.abs > 0 ? "+" : ""}
                          {fmt(d.abs, 4)}
                        </td>
                        <td className="num">
                          {d.rel === null ? "—" : `${(d.rel * 100).toFixed(2)}%`}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
          {data.batch && (
            <>
              <h4>审计记录（批次 #{data.batch.id}）</h4>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>结论</th>
                    <th>类型</th>
                    <th>工单</th>
                    <th>操作者</th>
                    <th>请求标识</th>
                    <th>应用时间</th>
                  </tr>
                </thead>
                <tbody>
                  {data.batch.conclusions.map((c) => (
                    <tr key={c.id}>
                      <td className="mono">#{c.id}</td>
                      <td>{c.conclusion_type}</td>
                      <td className="mono">{c.ticket_id ? `#${c.ticket_id}` : "—"}</td>
                      <td>{c.operator}</td>
                      <td className="mono">{c.request_id}</td>
                      <td className="mono">{c.applied_at?.slice(0, 19) || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </div>
  );
}
