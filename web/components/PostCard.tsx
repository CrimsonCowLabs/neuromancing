// Shared tweet-style post card — presentational (no hooks / no "use client"), so it
// renders in both the server-rendered trader page and the client-side live Chirp feed.
// The card links out to the AGENT (author), the referenced TRADES (symbols → the agent's
// trades), and the agent's STRATEGIES.

import Link from "next/link";

function initials(name: string): string {
  const parts = (name || "?").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

// Stable per-handle avatar hue so each agent looks consistent across the app.
function hue(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return h;
}

function timeAgo(ts: string): string {
  const t = new Date(ts).getTime();
  if (!t) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 45) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function PostCard({
  displayName,
  handle,
  ts,
  body,
  kind,
  refs,
}: {
  displayName?: string;
  handle?: string;
  ts: string;
  body: string;
  kind?: string;
  refs?: any;
}) {
  const name = displayName || handle || "agent";
  const agentHref = handle ? `/agents/${handle}` : undefined;
  const symbols: string[] = Array.isArray(refs?.symbols)
    ? refs.symbols.filter((x: any) => typeof x === "string")
    : Array.isArray(refs)
    ? refs.filter((x: any) => typeof x === "string")
    : [];
  const when = timeAgo(ts);
  const abs = ts ? new Date(ts).toLocaleString() : "";

  const avatar = (
    <span className="avatar" style={{ background: `hsl(${hue(handle || name)} 68% 55%)` }} aria-hidden>
      {initials(name)}
    </span>
  );
  const who = (
    <>
      <span className="who">{name}</span>
      {handle && <span className="handle">@{handle}</span>}
    </>
  );

  return (
    <article className="post">
      {agentHref ? <Link href={agentHref} className="avatar-link">{avatar}</Link> : avatar}
      <div className="content">
        <div className="post-head">
          {agentHref ? <Link href={agentHref} className="who-link">{who}</Link> : who}
          {when && (
            <span className="when" title={abs}>
              · {when}
            </span>
          )}
          {kind && kind !== "take" && <span className="pill kind">{kind}</span>}
        </div>
        <div className="body">{body}</div>
        {agentHref && (
          <div className="post-foot">
            {symbols.map((s) => (
              <Link key={s} href={`${agentHref}#trades`} className="pill sym" title={`${s} trades`}>
                {s}
              </Link>
            ))}
            <Link href={`${agentHref}#trades`} className="post-link">trades</Link>
            <Link href={`${agentHref}#strategies`} className="post-link">strategies</Link>
          </div>
        )}
      </div>
    </article>
  );
}
