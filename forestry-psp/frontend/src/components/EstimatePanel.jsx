import React, { useState } from "react";

const fmt = (x, d = 4) =>
  x === null || x === undefined ? "—" : Number(x).toFixed(d);

/** 总体估计：分量表 + 来源与不确定性假设。 */
export default function EstimatePanel({ estimates }) {
  const responses = estimates.responses;
  const components = estimates.components;
  const [response, setResponse] = useState(responses[0].id);
  const [component, setComponent] = useState(components[0].id);

  const prov = estimates.provenance[component];

  return (
    <div>
      <h3>总体估计（设计加权，Horvitz–Thompson）</h3>
      <div className="selector-row">
        <label>响应量：</label>
        {responses.map((r) => (
          <button
            key={r.id}
            className={response === r.id ? "active" : ""}
            onClick={() => setResponse(r.id)}
          >
            {r.label}
          </button>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>分量</th>
            <th>总量</th>
            <th>标准误</th>
            <th>95% 置信区间</th>
            <th>每公顷</th>
            <th>年均每公顷</th>
          </tr>
        </thead>
        <tbody>
          {components.map((c) => {
            const e = estimates.estimates[c.id][response];
            return (
              <tr
                key={c.id}
                className={component === c.id ? "row-active" : ""}
                onClick={() => setComponent(c.id)}
              >
                <td>{c.label}</td>
                <td className="num">{fmt(e.total, 3)}</td>
                <td className="num">{fmt(e.se, 3)}</td>
                <td className="num mono">
                  [{fmt(e.ci95[0], 3)}, {fmt(e.ci95[1], 3)}]
                </td>
                <td className="num">{fmt(e.per_ha, 5)}</td>
                <td className="num">{fmt(e.annual_per_ha, 5)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {estimates.warnings.length > 0 && (
        <div className="banner warn">{estimates.warnings.join("；")}</div>
      )}

      <h4>分量来源与不确定性假设：{components.find((c) => c.id === component)?.label}</h4>
      <p className="source">{prov.source}</p>
      <ul className="assumptions">
        {prov.assumptions.map((a, i) => (
          <li key={i}>{a}</li>
        ))}
      </ul>
      {prov.excluded_records.length > 0 && (
        <details>
          <summary>被剔除记录（{prov.excluded_records.length} 条，不参与本分量）</summary>
          <ul className="excluded">
            {prov.excluded_records.map((e, i) => (
              <li key={i}>
                <span className="mono">
                  {e.plot_id} {e.tree_no_t1 || "—"}→{e.tree_no_t2 || "—"}
                </span>{" "}
                [{e.category}] {e.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      <h4>单位换算与拒收记录</h4>
      {estimates.unit_conversions.length === 0 &&
      estimates.rejected_records.length === 0 ? (
        <p className="hint">本期无单位换算或拒收记录。</p>
      ) : (
        <ul className="excluded">
          {estimates.unit_conversions.map((c, i) => (
            <li key={`c${i}`}>
              换算：<span className="mono">{c.context}</span> {c.quantity}{" "}
              {c.value_in} {c.from_unit} → {fmt(c.value_out, 2)} {c.to_unit}
            </li>
          ))}
          {estimates.rejected_records.map((r, i) => (
            <li key={`r${i}`}>
              拒收：<span className="mono">{r.observation}</span> — {r.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
