import React, { useEffect, useState } from "react";
import {
  applyBatch,
  createBatch,
  fetchBatches,
  patchConclusion,
  retryBatch,
} from "../api";

const CONCLUSION_TYPES = [
  ["confirm_renumbered", "同株改号"],
  ["confirm_missing", "确认漏测"],
  ["confirm_dead", "确认死亡"],
  ["keep_excluded", "保留排除"],
  ["correct_observation", "更正观测记录"],
];

const STATUS_CHIP = { draft: "muted", applying: "missing", applied: "ok", failed: "conflict" };

const newRequestId = () =>
  `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

/** 修订批次：列表、创建（幂等）、应用、失败重试、结论修正与审计。 */
export default function BatchPanel({ versions, tickets, onChanged }) {
  const [batches, setBatches] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  // 创建表单状态
  const confirmedVersions = versions.filter((v) => v.confirmed);
  const [baseVersionId, setBaseVersionId] = useState(confirmedVersions[0]?.id);
  const [createdBy, setCreatedBy] = useState("核实员甲");
  const [requestId, setRequestId] = useState(newRequestId);
  const [lines, setLines] = useState([]);
  const [selectedTicket, setSelectedTicket] = useState("");
  const [conclusionType, setConclusionType] = useState("confirm_renumbered");
  const [correctionText, setCorrectionText] = useState(
    '{\n  "survey": "S2020",\n  "plot": "P02",\n  "tree_no": "T006",\n  "corrections": {"dbh_value": 41.2, "dbh_unit": "cm"}\n}'
  );
  // 结论修正编辑状态：{conclusionId: 文本}
  const [editing, setEditing] = useState({});

  const openTickets = tickets.filter((t) => t.status === "open");

  const reload = () =>
    fetchBatches()
      .then(setBatches)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    reload();
  }, []);

  const run = (promise, okMessage) => {
    setError(null);
    setNotice(null);
    return promise
      .then((result) => {
        setNotice(okMessage);
        return reload().then(() => onChanged?.()).then(() => result);
      })
      .catch((e) => setError(String(e.message || e)));
  };

  const addTicketLine = () => {
    const ticket = tickets.find((t) => t.id === Number(selectedTicket));
    if (!ticket) return;
    setLines([
      ...lines,
      {
        ticket_id: ticket.id,
        conclusion_type: conclusionType,
        operator: createdBy,
        request_id: `${requestId}-c${lines.length + 1}`,
        after_value:
          conclusionType === "confirm_renumbered"
            ? {
                tree_no_t1: ticket.tree_no_t1,
                tree_no_t2: ticket.tree_no_t2,
                note: "现场核实结论",
              }
            : { note: "现场核实结论" },
        _label: `工单#${ticket.id} ${ticket.plot_id} ${ticket.category_label}`,
      },
    ]);
  };

  const addCorrectionLine = () => {
    try {
      const after = JSON.parse(correctionText);
      setLines([
        ...lines,
        {
          ticket_id: null,
          conclusion_type: "correct_observation",
          operator: createdBy,
          request_id: `${requestId}-c${lines.length + 1}`,
          after_value: after,
          _label: `更正 ${after.survey}/${after.plot}/${after.tree_no}`,
        },
      ]);
    } catch {
      setError("更正内容不是合法 JSON");
    }
  };

  const submitBatch = () => {
    const payload = {
      request_id: requestId,
      base_version_id: Number(baseVersionId),
      created_by: createdBy,
      conclusions: lines.map(({ _label, ...rest }) => rest),
    };
    run(createBatch(payload), "批次已创建（重复提交将幂等返回同一批次）").then(
      (batch) => {
        if (batch) {
          setLines([]);
          setRequestId(newRequestId());
          setExpanded(batch.id);
        }
      }
    );
  };

  const saveConclusion = (conclusion) => {
    let after;
    try {
      after = JSON.parse(editing[conclusion.id]);
    } catch {
      setError("修正内容不是合法 JSON");
      return;
    }
    run(
      patchConclusion(conclusion.id, after),
      `结论 #${conclusion.id} 已修正`
    ).then(() => setEditing((s) => ({ ...s, [conclusion.id]: undefined })));
  };

  if (!batches) return <div className="banner">加载批次…</div>;

  return (
    <div>
      <h3>修订批次（{batches.length}）</h3>
      {error && <div className="banner error">{error}</div>}
      {notice && <div className="banner ok-banner">{notice}</div>}

      <table className="data-table">
        <thead>
          <tr>
            <th>批次</th>
            <th>请求标识</th>
            <th>基线版</th>
            <th>状态</th>
            <th>创建人</th>
            <th>结果版</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {batches.map((b) => (
            <React.Fragment key={b.id}>
              <tr className={b.status === "failed" ? "row-verify" : ""}>
                <td className="mono">#{b.id}</td>
                <td className="mono">{b.request_id.slice(0, 18)}…</td>
                <td className="mono">#{b.base_version}</td>
                <td>
                  <span className={`chip ${STATUS_CHIP[b.status]}`}>
                    {b.status_label}
                  </span>
                </td>
                <td>{b.created_by}</td>
                <td className="mono">{b.result_version ? `#${b.result_version}` : "—"}</td>
                <td>
                  <button onClick={() => setExpanded(expanded === b.id ? null : b.id)}>
                    {expanded === b.id ? "收起" : "详情"}
                  </button>{" "}
                  {(b.status === "draft" || b.status === "applying") && (
                    <button onClick={() => run(applyBatch(b.id), `批次 #${b.id} 已应用`)}>
                      应用
                    </button>
                  )}
                  {b.status === "failed" && (
                    <button onClick={() => run(retryBatch(b.id), `批次 #${b.id} 重试成功`)}>
                      重试
                    </button>
                  )}
                </td>
              </tr>
              {expanded === b.id && (
                <tr>
                  <td colSpan={7}>
                    {b.error && <div className="banner error">失败原因：{b.error}</div>}
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>结论</th>
                          <th>类型</th>
                          <th>工单</th>
                          <th>操作者</th>
                          <th>请求标识</th>
                          <th>前值 → 后值</th>
                          <th>应用时间</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {b.conclusions.map((c) => (
                          <tr key={c.id}>
                            <td className="mono">#{c.id}</td>
                            <td>{c.conclusion_type_label}</td>
                            <td className="mono">{c.ticket ? `#${c.ticket}` : "—"}</td>
                            <td>{c.operator}</td>
                            <td className="mono">{c.request_id}</td>
                            <td className="note">
                              {editing[c.id] !== undefined ? (
                                <textarea
                                  rows={5}
                                  value={editing[c.id]}
                                  onChange={(e) =>
                                    setEditing((s) => ({
                                      ...s,
                                      [c.id]: e.target.value,
                                    }))
                                  }
                                />
                              ) : (
                                <details>
                                  <summary>查看前后值</summary>
                                  <pre className="json-block">
                                    {JSON.stringify(
                                      { before: c.before_value, after: c.after_value },
                                      null,
                                      1
                                    )}
                                  </pre>
                                </details>
                              )}
                            </td>
                            <td className="mono">
                              {c.applied_at ? c.applied_at.slice(0, 19) : "—"}
                            </td>
                            <td>
                              {(b.status === "draft" || b.status === "failed") &&
                                (editing[c.id] !== undefined ? (
                                  <button onClick={() => saveConclusion(c)}>保存</button>
                                ) : (
                                  <button
                                    onClick={() =>
                                      setEditing((s) => ({
                                        ...s,
                                        [c.id]: JSON.stringify(c.after_value, null, 2),
                                      }))
                                    }
                                  >
                                    修正
                                  </button>
                                ))}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
          {batches.length === 0 && (
            <tr>
              <td colSpan={7} className="hint">
                尚无修订批次。在下方选择工单与结论类型创建。
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <h4>新建修订批次</h4>
      <div className="form-grid">
        <label>基线版（已确认）</label>
        <select
          value={baseVersionId || ""}
          onChange={(e) => setBaseVersionId(e.target.value)}
        >
          {confirmedVersions.map((v) => (
            <option key={v.id} value={v.id}>
              #{v.id} {v.name}
            </option>
          ))}
        </select>
        <label>创建人</label>
        <input value={createdBy} onChange={(e) => setCreatedBy(e.target.value)} />
        <label>请求标识（幂等键）</label>
        <span className="mono">
          {requestId}{" "}
          <button onClick={() => setRequestId(newRequestId())}>重新生成</button>
        </span>
      </div>

      <div className="form-grid">
        <label>选择工单</label>
        <select value={selectedTicket} onChange={(e) => setSelectedTicket(e.target.value)}>
          <option value="">—</option>
          {openTickets.map((t) => (
            <option key={t.id} value={t.id}>
              #{t.id} {t.plot_id} {t.category_label}（{t.tree_no_t1 || "—"}/
              {t.tree_no_t2 || "—"}）
            </option>
          ))}
        </select>
        <label>结论类型</label>
        <select
          value={conclusionType}
          onChange={(e) => setConclusionType(e.target.value)}
        >
          {CONCLUSION_TYPES.filter(([k]) => k !== "correct_observation").map(
            ([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            )
          )}
        </select>
        <span>
          <button onClick={addTicketLine} disabled={!selectedTicket}>
            + 添加工单结论
          </button>
        </span>
      </div>

      <details>
        <summary>添加更正观测记录结论（坐标 / 单位 / 死亡状态）</summary>
        <textarea
          rows={6}
          value={correctionText}
          onChange={(e) => setCorrectionText(e.target.value)}
        />
        <button onClick={addCorrectionLine}>+ 添加更正结论</button>
      </details>

      {lines.length > 0 && (
        <div>
          <h4>待提交结论（{lines.length}）</h4>
          <ul className="excluded">
            {lines.map((l, i) => (
              <li key={i}>
                [{l.conclusion_type}] {l._label}{" "}
                <button onClick={() => setLines(lines.filter((_, j) => j !== i))}>
                  移除
                </button>
              </li>
            ))}
          </ul>
          <button className="primary" onClick={submitBatch}>
            提交批次（{lines.length} 条结论）
          </button>
        </div>
      )}
    </div>
  );
}
