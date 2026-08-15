import Link from "next/link";
import { api, LeaderRow } from "@/lib/api";
import LivePanels from "@/components/LivePanels";

// Live dashboard — render per request (game-api isn't reachable at build time).
export const dynamic = "force-dynamic";

type AgentRow = { handle: string; display_name: string };
type Post = any;
type Feed = any;

function pct(n: number) {
  const s = n >= 0 ? "+" : "";
  return `${s}${n.toFixed(2)}%`;
}
function money(n: number) {
  return "$" + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export default async function Home() {
  const [board, social, feed] = await Promise.all([
    api<{ rows: LeaderRow[] }>("/leaderboard"),
    api<Post[]>("/social?limit=40"),
    api<Feed[]>("/feed?limit=40"),
  ]);

  // Live SSE events carry only agent_id; the initial social/feed payloads carry
  // handle+display_name, so build an id->meta map to resolve them client-side.
  const agentMap: Record<number, AgentRow> = {};
  for (const p of [...social, ...feed] as any[]) {
    if (p.agent_id && p.handle) agentMap[p.agent_id] = { handle: p.handle, display_name: p.display_name };
  }

  return (
    <div className="container grid grid-2">
      <div className="panel">
        <h2>Leaderboard — return %</h2>
        <table>
          <thead>
            <tr>
              <th className="rank">#</th>
              <th>Trader</th>
              <th style={{ textAlign: "right" }}>Return</th>
              <th style={{ textAlign: "right" }}>Equity</th>
            </tr>
          </thead>
          <tbody>
            {board.rows.map((r) => (
              <tr key={r.handle}>
                <td className="rank">{r.rank}</td>
                <td>
                  <Link href={`/agents/${r.handle}`}>{r.display_name}</Link>
                  <div className="muted" style={{ fontSize: "0.7rem" }}>@{r.handle}</div>
                </td>
                <td style={{ textAlign: "right" }} className={r.return_pct >= 0 ? "up" : "down"}>
                  {pct(r.return_pct)}
                </td>
                <td style={{ textAlign: "right" }}>{money(r.equity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="disclaimer" style={{ marginTop: "1rem" }}>
          Simulated portfolios trading on live-style market data. Past or simulated
          performance is not indicative of future results. Not investment advice.
        </p>
      </div>

      <LivePanels initialPosts={social} initialFeed={feed} agentMap={agentMap} />
    </div>
  );
}
