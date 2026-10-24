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
    authorization: str | None = Header(default=None), #input: fastapi injects raw auth header via Header()
) -> uuid.UUID: 
    """Return the JWT subject as a user id, or raise 401 if the Bearer token is missing or invalid."""
    if authorization is None: #check if header is present
        raise HTTPException(status_code=401)
    scheme, _, token = authorization.partition(" ") #split into scheme(Bearer, separatr, JWT token)
    if scheme != "Bearer" or not token: #check if token present and "bearer" scheme is used
        raise HTTPException(status_code=401)

    payload = _payload_from_token(token)#parse AND validate token into dictionary
    sub = payload.get("sub")#read JWT subject from payload dict
    if not sub:
        raise HTTPException(status_code=401)
    try:
        # JWT sub is a string. parse to UUID so the rest of the app uses a typed id not a str
        return uuid.UUID(str(sub))
    except ValueError:
        raise HTTPException(status_code=401) from None #same error codes for missing vs. malformed vs. unverifiable
