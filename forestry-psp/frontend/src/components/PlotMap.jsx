import React from "react";

const STRATUM_COLORS = { S1: "#4caf50", S2: "#26a69a" };
const W = 640;
const H = 460;
const PAD = 56;

/** 样地位置图：边界坐标 (EPSG:32650) 直接做仿射投影到 SVG。 */
export default function PlotMap({ plots, selectedId, onSelect }) {
  const coords = plots.flatMap((p) => p.boundary.coordinates[0]);
  const xs = coords.map((c) => c[0]);
  const ys = coords.map((c) => c[1]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const scale = Math.min((W - 2 * PAD) / (maxX - minX), (H - 2 * PAD) / (maxY - minY));
  const tx = (x) => PAD + (x - minX) * scale;
  const ty = (y) => H - PAD - (y - minY) * scale;

  const strata = [...new Map(plots.map((p) => [p.stratum.code, p.stratum])).values()];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="plot-map" role="img">
        {plots.map((p) => {
          const ring = p.boundary.coordinates[0];
          const points = ring.map(([x, y]) => `${tx(x)},${ty(y)}`).join(" ");
          const cx = ring.reduce((s, c) => s + c[0], 0) / ring.length;
          const cy = ring.reduce((s, c) => s + c[1], 0) / ring.length;
          const selected = p.plot_id === selectedId;
          return (
            <g
              key={p.plot_id}
              className={`plot ${selected ? "selected" : ""}`}
              onClick={() => onSelect(p.plot_id)}
            >
              <polygon
                points={points}
                fill={STRATUM_COLORS[p.stratum.code] || "#888"}
                fillOpacity={selected ? 0.85 : 0.45}
                stroke={selected ? "#ffeb3b" : "#1b2a1f"}
                strokeWidth={selected ? 3 : 1.5}
              />
              <text x={tx(cx)} y={ty(cy)} textAnchor="middle" className="plot-label">
                {p.plot_id}
              </text>
              <text x={tx(cx)} y={ty(cy) + 14} textAnchor="middle" className="plot-sublabel">
                {p.area_ha} ha · w={p.weight.toFixed(0)}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="legend">
        {strata.map((s) => (
          <span key={s.code} className="legend-item">
            <i style={{ background: STRATUM_COLORS[s.code] || "#888" }} />
            {s.name}（{s.area_ha} ha）
          </span>
        ))}
      </div>
    </div>
  );
}
