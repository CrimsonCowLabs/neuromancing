"""Auth for the internal service seam.

Order-mutating endpoints require the privileged service token. The thin Next.js
BFF never holds this token; only server-side game-api / temporal-worker callers do.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from .config import get_settings


def require_service_token(x_service_token: str = Header(default="")) -> None:
    expected = get_settings().trade_api_service_token
    if not hmac.compare_digest(x_service_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing service token",
        )
