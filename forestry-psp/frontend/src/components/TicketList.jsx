import React from "react";

/** 待核实工单及其处理结果（结论、操作者、批次状态）。 */
export default function TicketList({ tickets }) {
  if (!tickets.length) return <p className="hint">没有工单。</p>;
  const open = tickets.filter((t) => t.status === "open").length;
  return (
    <div>
      <h3>核实工单（{open} 项待核实 / 共 {tickets.length} 项）</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>工单</th>
            <th>样地</th>
            <th>类型</th>
            <th>编号（初测/复测）</th>
            <th>说明</th>
            <th>状态</th>
            <th>处理结果</th>
          </tr>
        </thead>
        <tbody>
          {tickets.map((t) => (
            <tr key={t.id} className={t.status === "open" ? "row-verify" : ""}>
              <td className="mono">#{t.id}</td>
              <td className="mono">{t.plot_id}</td>
              <td>{t.category_label}</td>
              <td className="mono">
                {t.tree_no_t1 || "—"} / {t.tree_no_t2 || "—"}
              </td>
              <td className="note">{t.detail}</td>
              <td>
                <span className={`chip ${t.status === "open" ? "conflict" : "ok"}`}>
                  {t.status === "open" ? "待核实" : "已核实"}
                </span>
              </td>
              <td className="note">
                {t.conclusions.length === 0 && "—"}
                {t.conclusions.map((c) => (
                  <div key={c.id}>
                    <span className="chip renumbered">{c.conclusion_type_label}</span>{" "}
                    {c.operator} · 批次#{c.batch_id}（{c.batch_status}）
                    <div className="mono">{c.request_id}</div>
                  </div>
                ))}
                {t.resolution_note && <div>{t.resolution_note}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">
        核实结论通过"修订批次"提交并应用后生效；核实前，相关个体不参与生长、死亡、进界任何分量。
      </p>
    </div>
  );
}
