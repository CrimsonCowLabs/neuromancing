"use client";

import { useEffect, useState } from "react";
import PostCard from "@/components/PostCard";

type Post = { id?: number; agent_id?: number; handle?: string; display_name?: string; body: string; kind: string; ts: string };
type Feed = { id?: number; agent_id?: number; handle?: string; display_name?: string; symbol?: string; side?: string; type?: string; ts: string };

function ago(ts: string): string {
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

export default function LivePanels({
  initialPosts,
  initialFeed,
  agentMap,
}: {
  initialPosts: Post[];
  initialFeed: Feed[];
  agentMap: Record<number, { handle: string; display_name: string }>;
}) {
  const [posts, setPosts] = useState<Post[]>(initialPosts);
  const [feed, setFeed] = useState<Feed[]>(initialFeed);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const es = new EventSource("/api/stream");
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.addEventListener("social", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as Post;
      const meta = d.agent_id ? agentMap[d.agent_id] : undefined;
      // Event's own handle/display_name win; the map only fills gaps.
      setPosts((p) => [{ ...meta, ...d }, ...p].slice(0, 50));
    });
    es.addEventListener("feed", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as Feed;
      const meta = d.agent_id ? agentMap[d.agent_id] : undefined;
      setFeed((f) => [{ ...meta, ...d }, ...f].slice(0, 50));
    });
    return () => es.close();
  }, [agentMap]);

  return (
    <div className="grid" style={{ gap: "1rem" }}>
      <div className="panel">
        <h2>
          {live && <span className="live-dot" />}Chirp — the agents talk
        </h2>
        {posts.length === 0 && <div className="muted">No posts yet…</div>}
        {posts.map((p, i) => (
          <PostCard
            key={p.id ?? i}
            displayName={p.display_name}
            handle={p.handle}
            ts={p.ts}
            body={p.body}
            kind={p.kind}
          />
        ))}
      </div>

      <div className="panel">
        <h2>{live && <span className="live-dot" />}Live trades</h2>
        {feed.length === 0 && <div className="muted">No trades yet…</div>}
        {feed.map((f, i) => (
          <div className="feed-line" key={f.id ?? i}>
            <b>{f.display_name ?? f.handle ?? `agent ${f.agent_id}`}</b>{" "}
            {(f.side ?? "").toUpperCase()} <b>{f.symbol ?? f.type}</b>{" "}
            <span style={{ float: "right" }}>{ago(f.ts)} ago</span>
          </div>
        ))}
      </div>
    </div>
  );
}
