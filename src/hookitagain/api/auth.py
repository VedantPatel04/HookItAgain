import uuid
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from hookitagain.settings import get_settings


@lru_cache
def _jwks_client() -> PyJWKClient:
    base = str(get_settings().supabase_url).rstrip("/")
    return PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json")


def _payload_from_token(token: str) -> dict:
    settings = get_settings()
    try:
        key = _jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except jwt.PyJWKClientError:
        pass
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401) from None

    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401) from None


def require_user_id(
    authorization: str | None = Header(default=None),
) -> uuid.UUID:
    """Return the JWT subject as a user id, or raise 401 if the Bearer token is missing or invalid."""
    if authorization is None:
        raise HTTPException(status_code=401)
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token:
        raise HTTPException(status_code=401)

    payload = _payload_from_token(token)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401)
    try:
        return uuid.UUID(str(sub))
    except ValueError:
        raise HTTPException(status_code=401) from None
