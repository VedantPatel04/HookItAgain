import uuid

import jwt
from fastapi import Header, HTTPException

from hookitagain.settings import get_settings


def require_user_id(
    authorization: str | None = Header(default=None),
) -> uuid.UUID:
    """checks the Authorization header, verifies the JWT, and returns the caller's user id, or raises 401 if any of that fails"""
    if authorization is None:
        raise HTTPException(status_code=401)
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token:
        raise HTTPException(status_code=401)

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401) from None

    sub = payload.get("sub") # "sub" is the user id
    if not sub:
        raise HTTPException(status_code=401)
    try:
        return uuid.UUID(str(sub))
    except ValueError:
        raise HTTPException(status_code=401) from None
