import { gameApiBase } from "@/lib/api";

// BFF proxy for paginated, rejected-filtered trades (game-api already excludes
// rejected orders on this endpoint). Browser -> here -> game-api (internal).
export const dynamic = "force-dynamic";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ handle: string }> },
) {
  const { handle } = await params;
  const url = new URL(req.url);
  const limit = url.searchParams.get("limit") ?? "10";
  const offset = url.searchParams.get("offset") ?? "0";
  const token = process.env.GAME_API_PUBLIC_TOKEN ?? "";
  const res = await fetch(
    `${gameApiBase}/agents/${handle}/trades?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
    { headers: token ? { authorization: `Bearer ${token}` } : {}, cache: "no-store" },
  );
  if (!res.ok) return Response.json([], { status: res.status });
  return Response.json(await res.json());
}
