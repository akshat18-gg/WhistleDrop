from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError
from app.schemas import LoginIn, TokenOut
from app.security import TOKEN_LIFETIME, authenticate, create_token

router = APIRouter(prefix="/api/auth", tags=["Moderator login"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    """Log in as a moderator. The token lasts 60 minutes."""
    moderator = authenticate(db, body.username, body.password)
    if moderator is None:
        # Same answer for an unknown username, a wrong password and a deactivated
        # account, so this can't be used to find out which usernames exist.
        raise ApiError(
            401,
            "INVALID_CREDENTIALS",
            "Wrong username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenOut(
        access_token=create_token(moderator),
        expires_in=int(TOKEN_LIFETIME.total_seconds()),
    )
