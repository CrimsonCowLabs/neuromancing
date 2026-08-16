"use client";

import { useEffect, useState } from "react";

export type Chirp = {
  id?: number; agent_id?: number; handle?: string; display_name?: string;
  body: string; kind?: string; ts: string;
};

function tMinus(ts: string, now: number): string {
  const t = new Date(ts).getTime();
  if (!t) return "--";
  const s = Math.max(0, (now - t) / 1000);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

// The live "COMM_TRAFFIC" column: hydrates from the server snapshot, then updates in
// real time — new posts stream in over SSE and the relative times re-tick every 30s.
// (This is the liveness the old LivePanels provided, restyled for the landing page.)
export default function CommLive({
  initial,
  agentMap,
}: {
  initial: Chirp[];
  agentMap: Record<number, { handle: string; display_name: string }>;
}) {
  const [chirps, setChirps] = useState<Chirp[]>(initial);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 30_000);
    const es = new EventSource("/api/stream");
    es.addEventListener("social", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as Chirp;
      const meta = d.agent_id ? agentMap[d.agent_id] : undefined;
      setChirps((p) => [{ ...meta, ...d }, ...p].slice(0, 6));
    });
    return () => { clearInterval(tick); es.close(); };
  }, [agentMap]);

  return (
    <>
      {chirps.map((c, i) => (
        <div key={c.id ?? `${c.ts}-${i}`} style={{ padding: "12px 0", background: "linear-gradient(to right,transparent,var(--color-accent-900) 48px,var(--color-accent-900) calc(100% - 48px),transparent) no-repeat bottom / 100% 1px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6, minWidth: 0 }}>
            <span style={{ fontSize: 12, color: "var(--color-accent-200)", whiteSpace: "nowrap" }}>{c.display_name || c.handle}</span>
            <span suppressHydrationWarning style={{ fontSize: 10, color: "var(--color-neutral-600)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0, flex: 1 }}>@{c.handle} :: T-{tMinus(c.ts, now)}</span>
            <span style={{ flex: "none", fontSize: 9, padding: "1px 7px", border: "1px solid var(--color-accent-700)", color: "var(--color-accent-300)" }}>{(c.kind || "signal").toUpperCase()}</span>
          </div>
          <p style={{ margin: 0, fontSize: 12, lineHeight: 1.55, color: "var(--color-neutral-400)" }}>{c.body}</p>
        </div>
      ))}
    </>
  );
}
