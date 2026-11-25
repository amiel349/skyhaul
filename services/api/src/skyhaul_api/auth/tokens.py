"""HS256 access tokens (ADR 0011). The token identifies the user; the database authorizes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import jwt

ALGORITHM: Final = "HS256"
ISSUER: Final = "skyhaul-api"
AUDIENCE: Final = "skyhaul"
TOKEN_TTL: Final = timedelta(minutes=15)
REQUIRED_CLAIMS: Final = ["sub", "role", "iat", "exp", "jti", "iss", "aud"]


class InvalidTokenError(Exception):
    """Any reason a token is refused. Never carries the token."""


def issue_token(user_id: uuid.UUID, role: str, secret: str, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(user_id),
        "role": role,
        "iat": issued,
        "exp": issued + TOKEN_TTL,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def verify_token(token: str, secret: str) -> uuid.UUID:
    """The user id of a valid token. Only HS256 is accepted, so ``alg: none`` fails."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": REQUIRED_CLAIMS},
        )
        return uuid.UUID(str(claims["sub"]))
    except (jwt.InvalidTokenError, ValueError):
        raise InvalidTokenError from None
