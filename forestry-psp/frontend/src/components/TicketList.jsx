import React from "react";

/** 待人工核实事项（位置矛盾、疑似漏测等）。 */
export default function TicketList({ tickets }) {
  if (!tickets.length) return <p className="hint">没有待核实事项。</p>;
  return (
    <div>
      <h3>待核实事项（{tickets.filter((t) => t.status === "open").length} 项未决）</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>样地</th>
            <th>类型</th>
            <th>初测编号</th>
            <th>复测编号</th>
            <th>说明</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {tickets.map((t) => (
            <tr key={t.id} className={t.status === "open" ? "row-verify" : ""}>
              <td className="mono">{t.plot_id}</td>
              <td>{t.category_label}</td>
              <td className="mono">{t.tree_no_t1 || "—"}</td>
              <td className="mono">{t.tree_no_t2 || "—"}</td>
              <td className="note">{t.detail}</td>
              <td>
                <span className={`chip ${t.status === "open" ? "conflict" : "ok"}`}>
                  {t.status === "open" ? "待核实" : "已核实"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">
        核实前，相关个体不参与生长、死亡、进界任何分量；核实结论需回写调查记录后重新出数。
      </p>
    </div>
  );
}
