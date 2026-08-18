from __future__ import annotations

from fastapi import FastAPI

from .realtime import sse
from .routers import agents, feed, leaderboard, options

app = FastAPI(title="Neuromancing game-api", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "game-api"}


app.include_router(leaderboard.router)
app.include_router(agents.router)
app.include_router(feed.router)
app.include_router(sse.router)
app.include_router(options.router)
