export default function EquityChart({ points }: { points: { ts: string; equity: number }[] }) {
  if (!points || points.length < 2) return <div className="muted">Not enough data yet…</div>;
  const vals = points.map((p) => p.equity);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const W = 640, H = 140, pad = 6;
  const d = points
    .map((p, i) => {
      const x = pad + (i / (points.length - 1)) * (W - 2 * pad);
      const y = pad + (1 - (p.equity - min) / span) * (H - 2 * pad);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  const color = up ? "#4ade80" : "#f87171";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none"
         style={{ display: "block" }}>
      <path d={`${d} L${W - pad},${H} L${pad},${H} Z`} fill={color} opacity={0.08} />
      <path d={d} fill="none" stroke={color} strokeWidth={2} />
    </svg>
  );
}
