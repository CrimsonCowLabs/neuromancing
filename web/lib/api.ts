// Server-only fetch helpers to game-api. The browser never calls game-api
// directly (it's internal); pages fetch here (ISR-cached), realtime goes through
// the /api/stream SSE proxy. Read-scoped token only — no privileged token here.

const BASE = process.env.GAME_API_URL ?? "http://game-api:8000";
const READ_TOKEN = process.env.GAME_API_PUBLIC_TOKEN ?? "";

export async function api<T>(path: string, revalidate = 5): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: READ_TOKEN ? { authorization: `Bearer ${READ_TOKEN}` } : {},
    next: { revalidate },
  });
  if (!res.ok) throw new Error(`game-api ${path} -> ${res.status}`);
  return (await res.json()) as T;
}

export const gameApiBase = BASE;

export type LeaderRow = {
  agent_id: number; rank: number; handle: string; display_name: string;
  equity: number; starting_equity: number; return_pct: number;
};
