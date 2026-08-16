"use client";

import { useEffect, useState } from "react";

type Trade = { id: number; symbol: string; side: string; status: string };

const PAGE_SIZES = [10, 25, 50];
const UP = "#8fc7a4";
const DOWN = "#cf8f8f";
const COLS = "1fr 70px 70px";

// The construct's RECENT_TRADES — client-paginated via the handle-keyed BFF proxy,
// styled to the nocturne theme.
export default function TradesTable({ handle }: { handle: string }) {
  const [pageSize, setPageSize] = useState(10);
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`/api/agents/${handle}/trades?limit=${pageSize}&offset=${offset}`)
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) { setRows(Array.isArray(d) ? d : []); setLoading(false); }
      })
      .catch(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [handle, pageSize, offset]);

  const page = Math.floor(offset / pageSize) + 1;
  const hasPrev = offset > 0;
  const hasNext = rows.length === pageSize;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14 }}>
        <span style={{ fontSize: 10, letterSpacing: ".18em", color: "var(--color-accent-400)" }}>▞ RECENT_TRADES</span>
        <label style={{ marginLeft: "auto", fontSize: 10, color: "var(--color-neutral-600)" }}>
          show{" "}
          <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setOffset(0); }}
            style={{ background: "transparent", color: "var(--color-neutral-300)", border: "1px solid var(--color-accent-800)", padding: "1px 4px", fontFamily: "inherit", fontSize: 10 }}>
            {PAGE_SIZES.map((n) => <option key={n} value={n} style={{ background: "#161826" }}>{n}</option>)}
          </select>{" "}/ page
        </label>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: COLS, gap: 8, padding: "0 10px 8px", fontSize: 9, letterSpacing: ".12em", color: "var(--color-neutral-600)", borderBottom: "1px solid var(--color-accent-900)" }}>
        <span>SYMBOL</span><span>SIDE</span><span>STATUS</span>
      </div>
      {rows.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--color-neutral-600)", padding: "14px 10px" }}>{loading ? "Loading…" : "No trades yet."}</div>
      )}
      {rows.map((t) => {
        const tone = t.side === "buy" ? UP : DOWN;
        return (
          <div key={t.id} style={{ display: "grid", gridTemplateColumns: COLS, gap: 8, alignItems: "center", padding: "8px 10px", background: "linear-gradient(to right,transparent,var(--color-accent-900) 32px,var(--color-accent-900) calc(100% - 32px),transparent) no-repeat bottom / 100% 1px" }}>
            <span style={{ fontSize: 12.5, color: "var(--color-text)" }}>{t.symbol}</span>
            <span style={{ fontSize: 12, color: tone, textShadow: `0 0 6px ${tone}` }}>{t.side}</span>
            <span style={{ fontSize: 12, color: "var(--color-neutral-500)" }}>{t.status}</span>
          </div>
        );
      })}

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14, fontSize: 11 }}>
        <button onClick={() => setOffset(Math.max(0, offset - pageSize))} disabled={!hasPrev}
          style={{ padding: "4px 12px", border: "1px solid var(--color-neutral-800)", background: "transparent", color: hasPrev ? "var(--color-accent-300)" : "var(--color-neutral-700)", cursor: hasPrev ? "pointer" : "default", fontFamily: "inherit" }}>← PREV</button>
        <span style={{ margin: "0 auto", color: "var(--color-neutral-600)" }}>page {page}</span>
        <button onClick={() => setOffset(offset + pageSize)} disabled={!hasNext}
          style={{ padding: "4px 12px", border: "1px solid var(--color-accent-700)", background: "transparent", color: hasNext ? "var(--color-accent-300)" : "var(--color-neutral-700)", cursor: hasNext ? "pointer" : "default", fontFamily: "inherit" }}>NEXT →</button>
      </div>
    </div>
  );
}
