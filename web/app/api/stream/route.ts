import { gameApiBase } from "@/lib/api";

// SSE proxy: the browser connects here; we pipe game-api's /sse/stream through.
// game-api is internal-only, so the browser never reaches it directly.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const token = process.env.GAME_API_PUBLIC_TOKEN ?? "";
  const upstream = await fetch(`${gameApiBase}/sse/stream`, {
    headers: token ? { authorization: `Bearer ${token}` } : {},
    cache: "no-store",
  });
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
