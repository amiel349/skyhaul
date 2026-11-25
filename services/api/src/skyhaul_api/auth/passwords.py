"""argon2id password hashes (argon2-cffi defaults: the RFC 9106 low-memory profile)."""

from __future__ import annotations

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


@cache
def _dummy_hash() -> str:
    return _hasher.hash("dummy password for unknown accounts")


def verify_dummy(password: str) -> None:
    """Spend the same work as a real verify, for an email that has no account."""
    verify_password(_dummy_hash(), password)
