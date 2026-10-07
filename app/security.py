"""API key authentication.

Enabled only when ``API_KEY`` is set in the environment. Comparison is
constant-time; when no key is configured the dependency is a no-op so local
setups stay simple.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    configured = request.app.state.settings.api_key
    if not configured:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")
