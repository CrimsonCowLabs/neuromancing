from __future__ import annotations

from fastapi import FastAPI

from .routers import accounts, orders, positions, strategies

app = FastAPI(title="Neuromancing trade-api", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "trade-api"}


app.include_router(accounts.router)
app.include_router(orders.router)
app.include_router(positions.router)
app.include_router(positions.exits_router)
app.include_router(strategies.router)
