"""REST resilience for the alpaca-py historical clients.

alpaca-py's REST clients use a `requests.Session` and call `session.request(...)` with NO
timeout (alpaca/common/rest.py) — so a half-open socket blocks the worker thread FOREVER. An
`asyncio.wait_for` around `asyncio.to_thread(...)` can't fix this: cancelling the awaiter can't
kill the thread, so hung requests leak threads until the pool is exhausted. The real fix is to
make the underlying HTTP call itself time out — inject a default `timeout` into the client's
session so the request raises `requests.Timeout` and the thread completes cleanly.

`call_rest(fn, ..., timeout=T)` combines both: the injected session timeout is primary; a
longer `wait_for` is the backstop for a code path the injection doesn't cover (e.g. a wedged
connection setup) — set longer than the request timeout so the clean path fires first.
"""

from __future__ import annotations

import asyncio


def inject_timeout(client, timeout: float):
    """Patch an alpaca-py historical client's session so every request carries a default
    timeout. Idempotent. Returns the client for chaining."""
    session = getattr(client, "_session", None)
    if session is None or getattr(session, "_nm_timeout_injected", False):
        return client
    orig = session.request

    def request(method, url, **kw):
        kw.setdefault("timeout", timeout)
        return orig(method, url, **kw)

    session.request = request
    session._nm_timeout_injected = True
    return client


async def call_rest(fn, *args, timeout: float):
    """Run a blocking alpaca-py REST call off-thread with a backstop deadline. Assumes the
    client's session already has `inject_timeout` applied (the primary bound). The wait_for
    is 2x so the request's own timeout fires first (clean thread completion)."""
    return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout * 2)
