// Thin BFF helper. The browser NEVER calls game-api directly; Next.js server
// code proxies with a read-scoped token. No privileged/order token is ever
// present in this tier (see spec: money-blind Node tier).
//
// Server-only: do not import from client components.

const GAME_API_URL = process.env.GAME_API_URL ?? "http://game-api:8000";
const READ_TOKEN = process.env.GAME_API_PUBLIC_TOKEN ?? "";

export async function gameApiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${GAME_API_URL}${path}`, {
    headers: { authorization: `Bearer ${READ_TOKEN}` },
    // Cold data is ISR-cached at the page level; this fetch stays uncached.
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`game-api ${path} -> ${res.status}`);
  }
  return (await res.json()) as T;
}
