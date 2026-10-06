import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import Moderator, utcnow

TOKEN_LIFETIME = timedelta(minutes=60)
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

_hasher = PasswordHasher()
# Checked against when the username doesn't exist, so a wrong username takes
# as long to reject as a wrong password and can't be told apart by timing.
_DUMMY_HASH = _hasher.hash("there-is-no-such-moderator")

bearer = HTTPBearer(auto_error=False, description="Paste the access_token from POST /api/auth/login.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def authenticate(db: Session, username: str, password: str) -> Moderator | None:
    moderator = db.scalar(select(Moderator).where(Moderator.username == username))
    if moderator is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(moderator.password_hash, password) or not moderator.is_active:
        return None
    return moderator


def create_token(moderator: Moderator) -> str:
    now = utcnow()
    payload = {"sub": str(moderator.id), "iat": now, "exp": now + TOKEN_LIFETIME}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


def _unauthorized(code: str, message: str) -> ApiError:
    return ApiError(401, code, message, headers={"WWW-Authenticate": "Bearer"})


def current_moderator(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Moderator:
    if credentials is None:
        raise _unauthorized(
            "NOT_AUTHENTICATED",
            "Log in at /api/auth/login and send the token as 'Authorization: Bearer <token>'.",
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
        moderator_id = int(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("TOKEN_EXPIRED", "Your login has expired. Log in again.") from None
    except (jwt.InvalidTokenError, ValueError):
        raise _unauthorized("INVALID_TOKEN", "That token isn't valid. Log in again.") from None

    moderator = db.get(Moderator, moderator_id)
    if moderator is None or not moderator.is_active:
        raise _unauthorized("INVALID_TOKEN", "That token isn't valid. Log in again.")
    return moderator


# The rate limiter needs something that tells clients apart, and the IP address is
# the only thing available. This is the one place the app reads it. It's hashed
# with a random key that only exists in memory and changes on every restart, and
# the limiter's counters live in memory too. The IP is never logged or stored.
_RATE_LIMIT_KEY = secrets.token_bytes(32)


def client_fingerprint(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return hmac.new(_RATE_LIMIT_KEY, ip.encode(), hashlib.sha256).hexdigest()


limiter = Limiter(key_func=client_fingerprint, storage_uri="memory://")
# slowapi logs a warning with the client's key every time a limit is hit. Our own
# request log already records the 429, without anything that tells clients apart.
logging.getLogger("slowapi").setLevel(logging.ERROR)
