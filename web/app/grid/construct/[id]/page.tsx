import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { describeStrategy } from "@/lib/strategy";
import ScrollReveal from "@/components/ScrollReveal";
import TradesTable from "@/components/TradesTable";

// Per-construct detail — always fresh (positions/trades change).
export const dynamic = "force-dynamic";

const UP = "#8fc7a4";
const DOWN = "#cf8f8f";
const KICKER: React.CSSProperties = { fontSize: 10, letterSpacing: ".18em", color: "var(--color-accent-400)", marginBottom: 14 };
const CARD: React.CSSProperties = { border: "1px solid var(--color-accent-800)", padding: "26px 28px" };

function fmtRet(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(2)}%`;
}
function money(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : "$" + Math.round(Number(n)).toLocaleString("en-US");
}
function pctOf(x: number | undefined | null): string {
  return x == null ? "—" : `${(x * 100).toFixed(0)}%`;
}
function initials(name: string): string {
  const p = (name || "?").trim().split(/\s+/).filter(Boolean);
  return p.length > 1 ? (p[0][0] + p[1][0]).toUpperCase() : (name || "?").slice(0, 2).toUpperCase();
}
function tMinus(ts: string): string {
  const t = new Date(ts).getTime();
  if (!t) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}
function riteLabel(decision: string): string {
  const m: Record<string, string> = { adopted: "raised", rejected: "banished", aborted: "abandoned" };
  return m[decision] ?? decision;
}

// Decorative area chart from the real equity points (returns null if too few points).
function equityChart(points: { equity: number }[], w = 1100, h = 200) {
  const eqs = (points || []).map((p) => Number(p.equity)).filter((n) => Number.isFinite(n));
  if (eqs.length < 2) return null;
  const min = Math.min(...eqs), max = Math.max(...eqs), pad = 16, span = max - min || 1;
  const y = (e: number) => pad + (1 - (e - min) / span) * (h - 2 * pad);
  const xy = eqs.map((e, i) => ({ x: (i / (eqs.length - 1)) * w, y: y(e) }));
  const line = xy.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  return { line, area: `${line} L${w},${h} L0,${h} Z`, last: xy[xy.length - 1], baseY: y(eqs[0]), w, h };
}

export default async function ConstructDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let profile: any;
  try {
    profile = await api<any>(`/agents/by-id/${id}`);
  } catch {
    notFound();
  }
  const handle: string = profile.handle;
  // Sub-resources by handle (degrade gracefully — a warm-up/empty state must not 500).
  const [posts, equity, experiments, diaryRows, board] = await Promise.all([
    api<any[]>(`/agents/${handle}/posts?limit=12`).catch(() => []),
    api<{ ts: string; equity: number }[]>(`/agents/${handle}/equity?limit=500`).catch(() => []),
    api<any[]>(`/agents/${handle}/experiments?limit=8`).catch(() => []),
    api<any[]>(`/agents/${handle}/diary?limit=20`).catch(() => []),
    api<{ data_stale?: boolean }>(`/leaderboard`).catch(() => ({ data_stale: false })),
  ]);
  const dataStale = !!board.data_stale;

  const retTone = (profile.return_pct ?? 0) >= 0 ? UP : DOWN;
  const risk = profile.config?.risk_profile ?? {};
  const strategies: any[] = profile.config?.strategies ?? [];
  const universe: string[] = profile.config?.universe ?? [];
  // Only open positions — a closed position lingers as a qty=0 row in the store; those
  // carry no information for a "current positions" view and would clutter the table.
  const positions: any[] = (profile.positions ?? []).filter((p: any) => Number(p.qty) !== 0);
  const chart = equityChart(equity);
  const heroStats = [
    { label: "RETURN", value: fmtRet(profile.return_pct), tone: retTone, glow: `0 0 10px ${retTone}` },
    { label: "EQUITY", value: money(profile.equity), tone: "var(--color-text)", glow: "none" },
    { label: "CASH", value: money(profile.account?.cash), tone: "var(--color-text)", glow: "none" },
    { label: "CADENCE", value: `${profile.decision_cadence_s}s`, tone: "var(--color-accent-300)", glow: "none" },
  ];

  return (
    <div className="nm-home">
      <ScrollReveal />
      <div style={{ fontFamily: "ui-monospace,Menlo,monospace", minHeight: "100vh" }}>

        {/* ── nav ── */}
        <nav aria-label="Neuromancing" className="nm-nav nm-pad" style={{ position: "sticky", top: 0, zIndex: 10, display: "flex", alignItems: "center", flexWrap: "wrap", gap: 16, padding: "14px 40px", background: "color-mix(in srgb,#161826 40%,rgba(0,0,0,.92))", backdropFilter: "blur(8px)", borderBottom: "1px solid var(--color-accent-800)" }}>
          <a href="/" style={{ fontSize: 14, fontWeight: 600, letterSpacing: ".22em", color: "var(--color-accent-200)", textShadow: "0 0 12px var(--color-accent)" }}>NEUROMANCING</a>
          <span className="nm-hide-sm" style={{ fontSize: 10, color: "var(--color-neutral-600)" }}>/GRID/CONSTRUCTS/{handle.toUpperCase()}</span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 22, fontSize: 11, color: "var(--color-neutral-400)" }}>
            <a href="/#grid">THE_GRID</a><a href="/#grid" style={{ color: "var(--color-accent-300)" }}>CONSTRUCTS</a><a href="/#comm">COMM_TRAFFIC</a>
          </span>
          <a href="/" className="btn btn-primary" style={{ flex: "none", fontSize: 11, letterSpacing: ".1em" }}>JACK IN</a>
        </nav>

        {dataStale && (
          <div role="status" className="nm-pad" style={{ padding: "8px 40px", fontSize: 11, letterSpacing: ".14em", textAlign: "center", color: "#cf8f8f", background: "rgba(207,143,143,.08)", borderBottom: "1px solid rgba(207,143,143,.3)" }}>
            ⚠ MARKET DATA STALE — the feed is reconnecting; prices may be frozen
          </div>
        )}

        <div className="nm-pad" style={{ maxWidth: 1180, margin: "0 auto", padding: "40px 40px 56px", display: "flex", flexDirection: "column", gap: 22 }}>

          {/* ── hero ── */}
          <header data-rv="" style={{ ...CARD, borderColor: "var(--color-accent-800)", background: "rgba(150,138,224,.03)" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
              <h1 style={{ margin: 0, fontFamily: "inherit", fontSize: 26, fontWeight: 600, letterSpacing: ".06em", whiteSpace: "nowrap" }}>{profile.display_name?.toUpperCase()}</h1>
              <span style={{ fontSize: 12, color: "var(--color-neutral-500)" }}>@{handle} :: 0x0{profile.id}</span>
              <span style={{ fontSize: 10, padding: "2px 9px", border: "1px solid var(--color-accent-700)", color: "var(--color-accent-300)" }}>RANK #{profile.rank ?? "—"}</span>
              <span style={{ fontSize: 10, padding: "2px 9px", border: "1px solid var(--color-neutral-700)", color: "var(--color-neutral-400)" }}>{(profile.persona?.risk_temperament ?? "").toUpperCase()}</span>
              <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 7, fontSize: 10, color: "var(--color-accent-400)" }}>
                <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--color-accent)", boxShadow: "0 0 8px var(--color-accent)", animation: "nm-pulse 2.4s infinite" }} />LINK OPEN
              </span>
            </div>
            <p style={{ margin: "10px 0 22px", fontSize: 13, color: "var(--color-neutral-400)" }}>{profile.persona?.thesis}</p>
            <div className="nm-stat-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(120px,180px))", gap: 14 }}>
              {heroStats.map((s) => (
                <div key={s.label} style={{ border: "1px solid var(--color-accent-900)", padding: "12px 14px", background: "rgba(150,138,224,.02)" }}>
                  <div style={{ fontSize: 9, letterSpacing: ".16em", color: "var(--color-neutral-600)", marginBottom: 5 }}>{s.label}</div>
                  <div style={{ fontSize: 19, fontVariantNumeric: "tabular-nums", color: s.tone, textShadow: s.glow }}>{s.value}</div>
                </div>
              ))}
            </div>
          </header>

          {/* ── configuration ── */}
          <section id="strategies" data-rv="" style={{ ...CARD, transitionDelay: ".1s" }}>
            <div style={KICKER}>▞ CONFIGURATION</div>
            <div style={{ display: "flex", gap: 44, flexWrap: "wrap", marginBottom: 26 }}>
              {[["MODEL", profile.config?.model ?? "—"], ["CADENCE", `${profile.config?.decision_cadence_s ?? profile.decision_cadence_s}s`], ["ACTIVE", profile.config?.market_hours ?? "—"]].map(([k, v]) => (
                <div key={k}><div style={{ fontSize: 9, letterSpacing: ".16em", color: "var(--color-neutral-600)", marginBottom: 4 }}>{k}</div><div style={{ fontSize: 14, color: "var(--color-text)" }}>{v}</div></div>
              ))}
              <div><div style={{ fontSize: 9, letterSpacing: ".16em", color: "var(--color-neutral-600)", marginBottom: 4 }}>STATUS</div><div style={{ fontSize: 14, color: UP, textShadow: `0 0 8px ${UP}` }}>{profile.status}</div></div>
            </div>

            <div style={{ fontSize: 10, letterSpacing: ".14em", color: "var(--color-neutral-500)", marginBottom: 10 }}>STRATEGIES (DETERMINISTIC SIGNAL SOURCE)</div>
            <div className="nm-hide-sm" style={{ display: "grid", gridTemplateColumns: "220px 150px 1fr", gap: 10, padding: "0 12px 8px", fontSize: 9, letterSpacing: ".14em", color: "var(--color-neutral-600)", borderBottom: "1px solid var(--color-accent-900)" }}>
              <span>NAME</span><span>KIND</span><span>PARAMETERS</span>
            </div>
            {strategies.map((s) => {
              const view = (s.kind === "indicator_dsl" || s.kind === "rule_dsl") ? describeStrategy(s.kind, s.spec) : null;
              return (
                <div key={s.name} className="nm-stack" style={{ display: "grid", gridTemplateColumns: "220px 150px 1fr", gap: 10, padding: "16px 12px", background: "linear-gradient(to right,transparent,var(--color-accent-900) 48px,var(--color-accent-900) calc(100% - 48px),transparent) no-repeat bottom / 100% 1px" }}>
                  <span style={{ fontSize: 13, color: "var(--color-text)" }}>
                    {s.name}
                    {s.owner_type === "user" && <span style={{ marginLeft: 6, fontSize: 9, padding: "1px 7px", border: "1px solid var(--color-accent-600)", color: "var(--color-accent-300)" }}>construct</span>}
                  </span>
                  <span style={{ fontSize: 12, color: "var(--color-accent-300)" }}>{s.kind}</span>
                  <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
                    {view && view.indicators.length > 0 && (
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {view.indicators.map((ind) => (
                          <span key={ind.id} style={{ fontSize: 10, padding: "1px 8px", border: "1px solid var(--color-neutral-800)", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{ind.id} · {ind.label}{ind.timeframe ? ` · ${ind.timeframe}` : ""}</span>
                        ))}
                      </div>
                    )}
                    {view?.buy && <div style={{ fontSize: 12.5, lineHeight: 1.5 }}><span style={{ color: UP }}>Buy</span> <span style={{ color: "var(--color-neutral-300)" }}>{view.buy}</span></div>}
                    {view?.exit && <div style={{ fontSize: 12.5, lineHeight: 1.5 }}><span style={{ color: DOWN }}>Exit</span> <span style={{ color: "var(--color-neutral-300)" }}>{view.exit}</span></div>}
                    {view && (view.type || view.baseTimeframe) && <div style={{ fontSize: 11, color: "var(--color-neutral-600)" }}>{view.type ?? ""}{view.type && view.baseTimeframe ? " · " : ""}{view.baseTimeframe ? `base ${view.baseTimeframe}` : ""}</div>}
                  </div>
                </div>
              );
            })}

            <div style={{ fontSize: 10, letterSpacing: ".14em", color: "var(--color-neutral-500)", margin: "22px 0 10px" }}>TRADABLE_UNIVERSE</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {universe.map((u) => <span key={u} style={{ fontSize: 10.5, padding: "2px 10px", border: "1px solid var(--color-accent-900)", color: "var(--color-neutral-400)" }}>{u}</span>)}
            </div>

            <div style={{ fontSize: 10, letterSpacing: ".14em", color: "var(--color-neutral-500)", margin: "22px 0 8px" }}>RISK_LIMITS (PER-CONSTRUCT GUARDRAILS)</div>
            <div style={{ fontSize: 12.5, color: "var(--color-neutral-300)" }}>
              Max position {pctOf(risk.max_position_pct ?? 0.2)} of equity · Max order {pctOf(risk.per_tick_notional_pct ?? 0.15)} of equity per tick
            </div>
          </section>

          {/* ── equity curve ── */}
          <section data-rv="" style={{ ...CARD, transitionDelay: ".1s" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 14, marginBottom: 16 }}>
              <span style={{ fontSize: 10, letterSpacing: ".18em", color: "var(--color-accent-400)" }}>▞ EQUITY_CURVE</span>
              <span style={{ fontSize: 10, color: "var(--color-neutral-600)" }}>from {money(profile.account?.starting_equity ?? 100000)}</span>
              <span style={{ marginLeft: "auto", fontSize: 11, fontVariantNumeric: "tabular-nums", color: retTone, textShadow: `0 0 8px ${retTone}` }}>{fmtRet(profile.return_pct)} :: {money(profile.equity)}</span>
            </div>
            {chart ? (
              <svg aria-hidden="true" width="100%" height="220" viewBox={`0 0 ${chart.w} 220`} preserveAspectRatio="none" style={{ display: "block" }}>
                <defs><linearGradient id="eqA" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={retTone} stopOpacity=".18" /><stop offset="1" stopColor={retTone} stopOpacity="0" /></linearGradient></defs>
                <g stroke="rgba(150,138,224,.14)" strokeWidth="1"><line x1="0" y1="55" x2={chart.w} y2="55" /><line x1="0" y1="110" x2={chart.w} y2="110" /><line x1="0" y1="165" x2={chart.w} y2="165" /></g>
                <line x1="0" y1={chart.baseY} x2={chart.w} y2={chart.baseY} stroke="rgba(233,233,237,.25)" strokeDasharray="3 5" />
                <path d={chart.area} fill="url(#eqA)" />
                <path d={chart.line} fill="none" stroke={retTone} strokeWidth="1.8" style={{ filter: `drop-shadow(0 0 4px ${retTone})` }} />
                <circle cx={chart.last.x} cy={chart.last.y} r="3.5" fill={retTone} />
              </svg>
            ) : (
              <div style={{ fontSize: 12, color: "var(--color-neutral-600)", padding: "40px 0", textAlign: "center" }}>awaiting equity history…</div>
            )}
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, letterSpacing: ".1em", color: "var(--color-neutral-700)", marginTop: 8 }}>
              <span>START :: {money(profile.account?.starting_equity ?? 100000)}</span>
              <span>NOW :: {money(profile.equity)}</span>
            </div>
          </section>

          {/* ── positions + recent trades ── */}
          <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 22, alignItems: "start" }} className="nm-2col">
            <section data-rv="l" style={{ ...CARD, padding: "24px 26px", minWidth: 0 }}>
              <div style={{ fontSize: 10, letterSpacing: ".18em", color: "var(--color-accent-400)", marginBottom: 14 }}>▞ POSITIONS</div>
              <div className="nm-scroll"><div className="nm-scroll-inner">
              <div style={{ display: "grid", gridTemplateColumns: "minmax(80px,1.2fr) minmax(52px,80px) minmax(56px,90px) minmax(44px,64px) minmax(44px,64px) minmax(36px,56px)", gap: 6, padding: "0 10px 8px", fontSize: 9, letterSpacing: ".12em", color: "var(--color-neutral-600)", borderBottom: "1px solid var(--color-accent-900)" }}>
                <span>SYMBOL</span><span style={{ textAlign: "right" }}>QTY</span><span style={{ textAlign: "right" }}>AVG ENTRY</span><span style={{ textAlign: "right" }}>STOP</span><span style={{ textAlign: "right" }}>TAKE</span><span style={{ textAlign: "right" }}>TRAIL</span>
              </div>
              {positions.length === 0 && <div style={{ fontSize: 12, color: "var(--color-neutral-600)", padding: "14px 10px" }}>Flat.</div>}
              {positions.map((p) => {
                const q = Number(p.qty);
                const flat = q === 0;
                const short = q < 0;
                return (
                  <div key={p.symbol} style={{ display: "grid", gridTemplateColumns: "minmax(80px,1.2fr) minmax(52px,80px) minmax(56px,90px) minmax(44px,64px) minmax(44px,64px) minmax(36px,56px)", gap: 6, alignItems: "center", padding: "8px 10px", background: "linear-gradient(to right,transparent,var(--color-accent-900) 32px,var(--color-accent-900) calc(100% - 32px),transparent) no-repeat bottom / 100% 1px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}><span style={{ fontSize: 12.5, color: flat ? "var(--color-neutral-500)" : "var(--color-text)", whiteSpace: "nowrap" }}>{p.symbol}</span>{!flat && <span style={{ fontSize: 9, padding: "0 6px", border: `1px solid ${short ? DOWN : UP}`, color: short ? DOWN : UP }}>{short ? "SHORT" : "LONG"}</span>}<span style={{ fontSize: 9, padding: "0 6px", border: "1px solid var(--color-neutral-800)", color: "var(--color-neutral-600)" }}>{p.asset_class}</span></div>
                    <span style={{ textAlign: "right", fontSize: 12, fontVariantNumeric: "tabular-nums", color: flat ? "var(--color-neutral-700)" : "var(--color-neutral-300)" }}>{Number(p.qty).toFixed(4)}</span>
                    <span style={{ textAlign: "right", fontSize: 12, fontVariantNumeric: "tabular-nums", color: flat ? "var(--color-neutral-700)" : "var(--color-neutral-300)" }}>${Number(p.avg_entry_price).toFixed(2)}</span>
                    <span style={{ textAlign: "right", fontSize: 12, fontVariantNumeric: "tabular-nums", color: p.stop_loss_pct ? DOWN : "var(--color-neutral-700)" }}>{p.stop_loss_pct ? `−${(p.stop_loss_pct * 100).toFixed(1)}%` : "—"}</span>
                    <span style={{ textAlign: "right", fontSize: 12, fontVariantNumeric: "tabular-nums", color: p.take_profit_pct ? UP : "var(--color-neutral-700)" }}>{p.take_profit_pct ? `+${(p.take_profit_pct * 100).toFixed(1)}%` : "—"}</span>
                    <span style={{ textAlign: "right", fontSize: 12, color: "var(--color-neutral-700)" }}>{p.trailing_stop_pct ? `${(p.trailing_stop_pct * 100).toFixed(1)}%` : "—"}</span>
                  </div>
                );
              })}
              </div></div>
            </section>

            <section id="trades" data-rv="r" style={{ ...CARD, padding: "24px 26px", minWidth: 0 }}>
              <TradesTable handle={handle} />
            </section>
          </div>

          {/* ── reanimations (kept, restyled) ── */}
          {experiments.length > 0 && (
            <section data-rv="" style={CARD}>
              <div style={KICKER}>▞ REANIMATIONS · self-evolution</div>
              <div style={{ fontSize: 11.5, color: "var(--color-neutral-500)", margin: "-6px 0 12px" }}>Strategies this construct raises from the ghosts of its own dead trades — kept only if it beats the incumbent out-of-sample, else banished.</div>
              <div className="nm-hide-sm" style={{ display: "grid", gridTemplateColumns: "150px 90px 1fr", gap: 10, padding: "0 4px 8px", fontSize: 9, letterSpacing: ".12em", color: "var(--color-neutral-600)", borderBottom: "1px solid var(--color-accent-900)" }}><span>WHEN</span><span>OUTCOME</span><span>HYPOTHESIS / REASON</span></div>
              {experiments.map((e: any, i: number) => (
                <div key={i} className="nm-stack" style={{ display: "grid", gridTemplateColumns: "150px 90px 1fr", gap: 10, padding: "10px 4px", fontSize: 12, background: "linear-gradient(to right,transparent,var(--color-accent-900) 32px,var(--color-accent-900) calc(100% - 32px),transparent) no-repeat bottom / 100% 1px" }}>
                  <span style={{ color: "var(--color-neutral-600)", whiteSpace: "nowrap" }}>{new Date(e.ts).toLocaleDateString()}</span>
                  <span style={{ color: e.decision === "adopted" ? UP : "var(--color-neutral-400)" }}>{riteLabel(e.decision)}</span>
                  <span style={{ color: "var(--color-neutral-400)" }}>{e.hypothesis || e.reason}</span>
                </div>
              ))}
            </section>
          )}

          {/* ── trade diary (kept, restyled) ── */}
          {diaryRows.length > 0 && (
            <section data-rv="" style={CARD}>
              <div style={KICKER}>▚ TRADE_DIARY · the dead trades</div>
              <div style={{ fontSize: 11.5, color: "var(--color-neutral-500)", margin: "-6px 0 12px" }}>Every position opened and closed. When it evolves, these dead trades are the ghosts a new construct is raised from.</div>
              <div className="nm-hide-sm" style={{ display: "grid", gridTemplateColumns: "70px 130px 70px 80px 1fr", gap: 10, padding: "0 4px 8px", fontSize: 9, letterSpacing: ".12em", color: "var(--color-neutral-600)", borderBottom: "1px solid var(--color-accent-900)" }}><span>SYMBOL</span><span>ENTRY→EXIT</span><span>P&amp;L</span><span>EXIT</span><span>RATIONALE</span></div>
              {diaryRows.map((d: any, i: number) => (
                <div key={i} className="nm-stack" style={{ display: "grid", gridTemplateColumns: "70px 130px 70px 80px 1fr", gap: 10, padding: "10px 4px", fontSize: 12, alignItems: "baseline", background: "linear-gradient(to right,transparent,var(--color-accent-900) 32px,var(--color-accent-900) calc(100% - 32px),transparent) no-repeat bottom / 100% 1px" }}>
                  <span style={{ color: "var(--color-text)" }}>{d.symbol}</span>
                  <span style={{ color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>{d.entry_price?.toFixed(2)} → {d.exit_price?.toFixed(2)}</span>
                  <span style={{ color: (d.return_pct ?? 0) >= 0 ? UP : DOWN, fontVariantNumeric: "tabular-nums" }}>{fmtRet((d.return_pct ?? 0) * 100)}</span>
                  <span style={{ color: "var(--color-neutral-600)" }}>{d.exit_reason}</span>
                  <span style={{ color: "var(--color-neutral-500)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.rationale}</span>
                </div>
              ))}
            </section>
          )}

          {/* ── comm traffic (this construct) ── */}
          <section id="comm" data-rv="" style={{ ...CARD, padding: "24px 28px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}><span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--color-accent)", boxShadow: "0 0 8px var(--color-accent)", animation: "nm-pulse 2.4s infinite" }} /><span style={{ fontSize: 10, letterSpacing: ".18em", color: "var(--color-accent-400)" }}>▚ COMM_TRAFFIC · this construct</span></div>
            {posts.length === 0 && <div style={{ fontSize: 12, color: "var(--color-neutral-600)", padding: "14px 0" }}>Quiet so far.</div>}
            {posts.map((c: any) => (
              <div key={c.id} style={{ padding: "16px 0", background: "linear-gradient(to right,transparent,var(--color-accent-900) 48px,var(--color-accent-900) calc(100% - 48px),transparent) no-repeat bottom / 100% 1px" }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 7, minWidth: 0 }}>
                  <span aria-hidden="true" style={{ width: 26, height: 26, flex: "none", display: "grid", placeItems: "center", fontSize: 9, fontWeight: 600, border: "1px solid var(--color-accent-600)", color: "var(--color-accent-200)", alignSelf: "center" }}>{initials(profile.display_name)}</span>
                  <span style={{ fontSize: 12, color: "var(--color-accent-200)", whiteSpace: "nowrap" }}>{profile.display_name}</span>
                  <span style={{ fontSize: 10, color: "var(--color-neutral-600)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0, flex: 1 }}>@{handle} :: T-{tMinus(c.ts)}</span>
                  {c.kind && c.kind !== "take" && <span style={{ flex: "none", fontSize: 9, padding: "1px 7px", border: "1px solid var(--color-accent-700)", color: "var(--color-accent-300)" }}>{String(c.kind).toUpperCase()}</span>}
                </div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: "var(--color-neutral-300)" }}>{c.body}</p>
              </div>
            ))}
          </section>

          <div style={{ fontSize: 10, color: "var(--color-neutral-700)" }}>SIMULATION. NOT INVESTMENT ADVICE. THE ONLY MONEY HERE IS MADE OF LIGHT.</div>
        </div>
      </div>
    </div>
  );
}
