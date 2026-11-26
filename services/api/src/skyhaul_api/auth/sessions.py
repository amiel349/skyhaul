"""Refresh-token sessions (ADR 0018): start, rotate with reuse detection, revoke, prune.

A family is one login. Every refresh marks the presented row used and adds a row to the
family, under ``SELECT ... FOR UPDATE`` on the presented row, so two refreshes of one token
are serialized by Postgres. Only SHA-256 hashes of the token and the CSRF token are stored.
The functions never commit; the caller commits with its audit entry.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from skyhaul_fleet_core.models import RefreshToken, Role, User
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from skyhaul_api.config import Config

PRUNE_AFTER: Final = timedelta(days=1)


class SessionInvalid(Exception):  # named after the API error code
    """The refresh token is refused. ``reason`` goes to the audit log, never to the caller."""

    def __init__(self, reason: str, user_id: uuid.UUID | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.user_id = user_id


class SessionReused(SessionInvalid):
    """A rotated token came back after the grace window; the family is revoked."""

    def __init__(self, user_id: uuid.UUID, family_id: uuid.UUID) -> None:
        super().__init__("reuse", user_id)
        self.family_id = family_id


class CsrfFailed(Exception):
    """The X-CSRF-Token header is missing or does not match the presented token."""

    def __init__(self, user_id: uuid.UUID) -> None:
        super().__init__("csrf")
        self.user_id = user_id


class RefreshSuperseded(Exception):
    """The token was rotated within the grace window (another tab). Nothing changes."""


@dataclass(frozen=True)
class Issued:
    """A new refresh token and its CSRF token, for the cookies and the response body."""

    token: str
    csrf: str
    expires_at: datetime
    user: User


def new_secret() -> str:
    """32 random bytes as 43 base64url characters."""
    return secrets.token_urlsafe(32)


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def refresh_ttl(role: Role, config: Config) -> timedelta:
    """The absolute lifetime of a session family. One value for every role today."""
    del role  # a short-lived VISITOR role will return its own value here
    return timedelta(seconds=config.refresh_token_ttl_s)


def access_claims(user: User) -> dict[str, Any]:
    """The access-token claims of ``user``, for login and refresh alike."""
    return {"sub": str(user.id), "role": user.role.value}


def start(session: Session, user: User, now: datetime, config: Config) -> Issued:
    """A new family for ``user``. Also deletes the user's expired rows."""
    session.execute(
        delete(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.expires_at <= now)
    )
    expires_at = now + refresh_ttl(user.role, config)
    issued, _ = _add_row(session, user, uuid.uuid4(), expires_at, now)
    return issued


def rotate(session: Session, token: str, csrf: str, now: datetime, config: Config) -> Issued:
    """Mark the presented row used and issue the next one of its family."""
    row = _locked_row(session, token)
    if row is None:
        raise SessionInvalid("unknown")
    if row.revoked_at is not None:
        raise SessionInvalid("revoked", row.user_id)
    if row.expires_at <= now:
        raise SessionInvalid("expired", row.user_id)
    _check_csrf(row, csrf)
    if row.used_at is not None:
        if now - row.used_at < timedelta(seconds=config.refresh_grace_s):
            raise RefreshSuperseded
        _revoke(session, row.family_id, now)
        raise SessionReused(row.user_id, row.family_id)
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        _revoke(session, row.family_id, now)
        raise SessionInvalid("inactive", row.user_id)
    issued, new_id = _add_row(session, user, row.family_id, row.expires_at, now)
    row.used_at = now
    row.replaced_by = new_id
    return issued


def revoke_family(session: Session, token: str, csrf: str, now: datetime) -> uuid.UUID | None:
    """Logout: revoke the family of a live token and return its user. A dead or unknown token
    changes nothing and returns None; a live one with the wrong CSRF raises ``CsrfFailed``."""
    row = _locked_row(session, token)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    _check_csrf(row, csrf)
    _revoke(session, row.family_id, now)
    return row.user_id


def revoke_presented(session: Session, token: str, now: datetime) -> None:
    """Login with a cookie: end its family, so a planted cookie never survives a login."""
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest(token)))
    if row is not None:
        _revoke(session, row.family_id, now)


def prune(session: Session, now: datetime) -> int:
    """Delete rows expired more than a day ago (the day keeps reuse evidence briefly)."""
    result = session.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < now - PRUNE_AFTER)
    )
    return int(getattr(result, "rowcount", 0))


def _locked_row(session: Session, token: str) -> RefreshToken | None:
    statement = (
        select(RefreshToken)
        .where(RefreshToken.token_hash == digest(token))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return session.scalar(statement)


def _check_csrf(row: RefreshToken, csrf: str) -> None:
    if not csrf or not hmac.compare_digest(digest(csrf), row.csrf_hash):
        raise CsrfFailed(row.user_id)


def _revoke(session: Session, family_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def _add_row(
    session: Session, user: User, family_id: uuid.UUID, expires_at: datetime, now: datetime
) -> tuple[Issued, uuid.UUID]:
    token, csrf = new_secret(), new_secret()
    row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        family_id=family_id,
        token_hash=digest(token),
        csrf_hash=digest(csrf),
        issued_at=now,
        expires_at=expires_at,
    )
    session.add(row)
    return Issued(token, csrf, expires_at, user), row.id
