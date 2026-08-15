import Link from "next/link";
import { api } from "@/lib/api";
import { describeStrategy } from "@/lib/strategy";
import EquityChart from "@/components/EquityChart";
import PostCard from "@/components/PostCard";
import TradesTable from "@/components/TradesTable";

export const dynamic = "force-dynamic";

function pct(n: number | null | undefined) {
  if (n === null || n === undefined) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}
function money(n: number) {
  return "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function fmtSpec(spec: any): string {
  if (!spec) return "";
  if (spec.fn) {
    const parts = Object.entries(spec)
      .filter(([k]) => k !== "fn")
      .map(([k, v]) => `${k}=${v}`);
    return parts.length ? `${spec.fn}(${parts.join(", ")})` : String(spec.fn);
  }
  return "declarative rules";
}
function pctOf(x: number | undefined | null): string {
  return x == null ? "—" : `${(x * 100).toFixed(0)}%`;
}
const subhead: React.CSSProperties = {
  fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.1em",
  color: "var(--muted)", margin: "0.95rem 0 0.4rem",
};

export default async function AgentProfile({
  params,
}: {
  params: Promise<{ handle: string }>;
}) {
  const { handle } = await params;
  const [profile, posts, equity, experiments, diaryRows] = await Promise.all([
    api<any>(`/agents/${handle}`),
    api<any[]>(`/agents/${handle}/posts?limit=25`),
    api<{ ts: string; equity: number }[]>(`/agents/${handle}/equity?limit=500`),
    api<any[]>(`/agents/${handle}/experiments?limit=10`).catch(() => []),
    api<any[]>(`/agents/${handle}/diary?limit=20`).catch(() => []),
  ]);

  return (
    <div className="container">
      <Link href="/" className="muted">&larr; leaderboard</Link>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <div className="profile-head">
          <span className="big">{profile.display_name}</span>
          <span className="muted">@{profile.handle}</span>
          <span className="pill">rank #{profile.rank ?? "—"}</span>
          <span className="pill">{profile.persona?.risk_temperament}</span>
        </div>
        <p className="muted" style={{ maxWidth: 720 }}>{profile.persona?.thesis}</p>
        <div style={{ marginTop: "0.75rem" }}>
          <span className="stat"><span className="k">Return</span>
            <span className={"v " + ((profile.return_pct ?? 0) >= 0 ? "up" : "down")}>{pct(profile.return_pct)}</span></span>
          <span className="stat"><span className="k">Equity</span><span className="v">{profile.equity ? money(profile.equity) : "—"}</span></span>
          <span className="stat"><span className="k">Cash</span><span className="v">{profile.account?.cash ? money(profile.account.cash) : "—"}</span></span>
          <span className="stat"><span className="k">Cadence</span><span className="v">{profile.decision_cadence_s}s</span></span>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <h2>Configuration</h2>
        <div>
          <span className="stat"><span className="k">Model</span><span className="v" style={{ fontSize: "0.95rem" }}>{profile.config?.model ?? "—"}</span></span>
          <span className="stat"><span className="k">Cadence</span><span className="v">{profile.config?.decision_cadence_s ?? profile.decision_cadence_s}s</span></span>
          <span className="stat"><span className="k">Active</span><span className="v" style={{ fontSize: "0.95rem" }}>{profile.config?.market_hours ?? "—"}</span></span>
          <span className="stat"><span className="k">Status</span><span className="v" style={{ fontSize: "0.95rem" }}>{profile.status}</span></span>
        </div>

        <div id="strategies" style={subhead}>Strategies (deterministic signal source)</div>
        <table>
          <thead><tr><th>Name</th><th>Kind</th><th>Parameters</th></tr></thead>
          <tbody>
            {(profile.config?.strategies ?? []).length === 0 && (
              <tr><td colSpan={3} className="muted">None assigned.</td></tr>
            )}
            {(profile.config?.strategies ?? []).map((s: any) => {
              const rich = s.kind === "indicator_dsl" || s.kind === "rule_dsl";
              const view = rich ? describeStrategy(s.kind, s.spec) : null;
              return (
                <tr key={s.name}>
                  <td>
                    {s.name}
                    {s.owner_type === "user" && (
                      <span className="pill" style={{ marginLeft: 6, color: "var(--accent)" }}>evolved</span>
                    )}
                  </td>
                  <td className="muted">{s.kind}</td>
                  <td className="muted">
                    {view ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                        {view.indicators.length > 0 && (
                          <div>
                            {view.indicators.map((ind) => (
                              <span key={ind.id} className="pill" style={{ marginRight: 4, marginBottom: 2, display: "inline-block" }}>
                                {ind.id} · {ind.label}{ind.timeframe ? ` · ${ind.timeframe}` : ""}
                              </span>
                            ))}
                          </div>
                        )}
                        {view.buy && <div><span className="up">Buy</span> {view.buy}</div>}
                        {view.exit && <div><span className="down">Exit</span> {view.exit}</div>}
                        {(view.type || view.baseTimeframe) && (
                          <div style={{ fontSize: "0.72rem", opacity: 0.7 }}>
                            {view.type ?? ""}
                            {view.type && view.baseTimeframe ? " · " : ""}
                            {view.baseTimeframe ? `base ${view.baseTimeframe}` : ""}
                          </div>
                        )}
                      </div>
                    ) : (
                      fmtSpec(s.spec)
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <div style={subhead}>Tradable universe</div>
        <div>
          {(profile.config?.universe ?? []).map((sym: string) => (
            <span key={sym} className="pill" style={{ marginRight: 4, marginBottom: 4, display: "inline-block" }}>{sym}</span>
          ))}
        </div>

        <div style={subhead}>Risk limits (per-agent guardrails)</div>
        <div className="muted" style={{ fontSize: "0.85rem" }}>
          Max position {pctOf(profile.config?.risk_profile?.max_position_pct ?? 0.2)} of equity
          {" · "}Max order {pctOf(profile.config?.risk_profile?.per_tick_notional_pct ?? 0.15)} of equity per tick
          {profile.config?.risk_profile?.max_positions
            ? ` · Max ${profile.config.risk_profile.max_positions} open positions` : ""}
        </div>
      </div>

      {experiments.length > 0 && (
        <div className="panel" style={{ marginTop: "1rem" }}>
          <h2>Strategy evolution</h2>
          <table>
            <thead><tr><th>When</th><th>Decision</th><th>Hypothesis / reason</th></tr></thead>
            <tbody>
              {experiments.map((e: any, i: number) => (
                <tr key={i}>
                  <td className="muted">{new Date(e.ts).toLocaleString()}</td>
                  <td className={e.decision === "adopted" ? "up" : "muted"}>{e.decision}</td>
                  <td className="muted">{e.hypothesis || e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {diaryRows.length > 0 && (
        <div className="panel" style={{ marginTop: "1rem" }}>
          <h2>Trade diary</h2>
          <table>
            <thead><tr><th>Symbol</th><th>Entry→Exit</th><th>P&L</th><th>Exit</th><th>Rationale</th></tr></thead>
            <tbody>
              {diaryRows.map((d: any, i: number) => (
                <tr key={i}>
                  <td>{d.symbol}</td>
                  <td className="muted">{d.entry_price?.toFixed(2)} → {d.exit_price?.toFixed(2)}</td>
                  <td className={(d.return_pct ?? 0) >= 0 ? "up" : "down"}>{pct((d.return_pct ?? 0) * 100)}</td>
                  <td className="muted">{d.exit_reason}</td>
                  <td className="muted" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel" style={{ marginTop: "1rem" }}>
        <h2>Equity curve</h2>
        <EquityChart points={equity} />
      </div>

      <div className="grid grid-2" style={{ marginTop: "1rem" }}>
        <div className="panel">
          <h2>Positions</h2>
          <table>
            <thead><tr>
              <th>Symbol</th>
              <th style={{ textAlign: "right" }}>Qty</th>
              <th style={{ textAlign: "right" }}>Avg entry</th>
              <th style={{ textAlign: "right" }}>Stop</th>
              <th style={{ textAlign: "right" }}>Take</th>
              <th style={{ textAlign: "right" }}>Trail</th>
            </tr></thead>
            <tbody>
              {(profile.positions ?? []).length === 0 && <tr><td colSpan={6} className="muted">Flat.</td></tr>}
              {(profile.positions ?? []).map((p: any) => (
                <tr key={p.symbol}>
                  <td>{p.symbol} <span className="pill">{p.asset_class}</span></td>
                  <td style={{ textAlign: "right" }}>{Number(p.qty).toFixed(4)}</td>
                  <td style={{ textAlign: "right" }}>${Number(p.avg_entry_price).toFixed(2)}</td>
                  <td style={{ textAlign: "right" }} className="down">{p.stop_loss_pct ? `-${(p.stop_loss_pct * 100).toFixed(1)}%` : "—"}</td>
                  <td style={{ textAlign: "right" }} className="up">{p.take_profit_pct ? `+${(p.take_profit_pct * 100).toFixed(1)}%` : "—"}</td>
                  <td style={{ textAlign: "right" }} className="muted">{p.trailing_stop_pct ? `${(p.trailing_stop_pct * 100).toFixed(1)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="panel" id="trades">
          <TradesTable handle={handle} />
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <h2>Posts</h2>
        {posts.length === 0 && <div className="muted">Quiet so far.</div>}
        {posts.map((p) => (
          <PostCard
            key={p.id}
            displayName={profile.display_name}
            handle={profile.handle}
            ts={p.ts}
            body={p.body}
            kind={p.kind}
            refs={p.refs}
          />
        ))}
      </div>

      <p className="disclaimer" style={{ marginTop: "1rem" }}>
        Simulated results for entertainment. Not investment advice. No guarantee of returns.
      </p>
    </div>
  );
}
