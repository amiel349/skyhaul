"""HS256 access tokens (ADR 0011). The token identifies the user; the database authorizes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
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


class ExpiredTokenError(InvalidTokenError):
    """The token was valid but its ``exp`` has passed."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    expires_at: datetime


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
    return verify_claims(token, secret).user_id


def verify_claims(token: str, secret: str) -> TokenClaims:
    """The user id and expiry of a valid token. ``ExpiredTokenError`` if it has expired."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": REQUIRED_CLAIMS},
        )
        expires_at = datetime.fromtimestamp(int(claims["exp"]), UTC)
        return TokenClaims(uuid.UUID(str(claims["sub"])), expires_at)
    except jwt.ExpiredSignatureError:
        raise ExpiredTokenError from None
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise InvalidTokenError from None
