"use client";

import { useEffect, useState } from "react";

type Trade = {
  id: number;
  symbol: string;
  side: string;
  status: string;
  order_type?: string;
  submitted_at?: string;
};

// Default page size is 10; configurable via the selector.
const PAGE_SIZES = [10, 25, 50];

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
        if (!cancelled) {
          setRows(Array.isArray(d) ? d : []);
          setLoading(false);
        }
      })
      .catch(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [handle, pageSize, offset]);

  const page = Math.floor(offset / pageSize) + 1;
  const hasPrev = offset > 0;
  const hasNext = rows.length === pageSize; // full page => probably more

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "0.5rem" }}>
        <h2 style={{ margin: 0 }}>Recent trades</h2>
        <label className="muted" style={{ fontSize: "0.72rem" }}>
          show{" "}
          <select
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); setOffset(0); }}
            style={{ background: "var(--panel-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 6, padding: "1px 4px" }}
          >
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>{" "}per page
        </label>
      </div>

      <table>
        <thead>
          <tr><th>Symbol</th><th>Side</th><th>Status</th></tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={3} className="muted">{loading ? "Loading…" : "No trades yet."}</td></tr>
          )}
          {rows.map((t) => (
            <tr key={t.id}>
              <td>{t.symbol}</td>
              <td className={t.side === "buy" ? "up" : "down"}>{t.side}</td>
              <td className="muted">{t.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.6rem", fontSize: "0.8rem" }}>
        <button className="pgbtn" disabled={!hasPrev} onClick={() => setOffset(Math.max(0, offset - pageSize))}>&larr; Prev</button>
        <span className="muted">page {page}</span>
        <button className="pgbtn" disabled={!hasNext} onClick={() => setOffset(offset + pageSize)}>Next &rarr;</button>
      </div>
    </div>
  );
}
