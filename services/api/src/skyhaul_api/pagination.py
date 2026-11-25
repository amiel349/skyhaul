"""Keyset pagination: newest (or oldest) first by ``(ts, id)``, ``?limit=``, opaque ``?cursor=``.

The cursor is base64url of ``[ts, id]`` of the last row of the previous page. Responses are
``{items, next_cursor}``; ``next_cursor`` is ``None`` on the last page.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import InstrumentedAttribute, Session

from skyhaul_api.errors import ApiError

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None


def encode_cursor(ts: datetime, row_id: object) -> str:
    raw = json.dumps([ts.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """``(ts, id)`` from a cursor. A malformed cursor is a 400 that does not echo it."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        ts, row_id = json.loads(raw)
        return datetime.fromisoformat(ts), str(row_id)
    except (binascii.Error, ValueError, TypeError):
        raise ApiError(400, "bad_cursor", "The cursor is not valid.") from None


def paginate[T](
    session: Session,
    statement: Select[T],
    *,
    ts_column: InstrumentedAttribute[datetime],
    id_column: InstrumentedAttribute[Any],
    id_type: Callable[[str], Any],
    limit: int,
    cursor: str | None,
    oldest_first: bool = False,
) -> Page[T]:
    """One page of ``statement``'s rows, newest first (or oldest first). ``id_type`` parses
    the cursor's id (``uuid.UUID``, ``int`` or ``str``)."""
    if cursor is not None:
        ts, raw_id = decode_cursor(cursor)
        try:
            row_id = id_type(raw_id)
        except ValueError:
            raise ApiError(400, "bad_cursor", "The cursor is not valid.") from None
        if oldest_first:
            after = or_(ts_column > ts, and_(ts_column == ts, id_column > row_id))
        else:
            after = or_(ts_column < ts, and_(ts_column == ts, id_column < row_id))
        statement = statement.where(after)
    if oldest_first:
        statement = statement.order_by(ts_column.asc(), id_column.asc())
    else:
        statement = statement.order_by(ts_column.desc(), id_column.desc())
    statement = statement.limit(limit + 1)
    rows = list(session.scalars(statement))
    items = rows[:limit]
    if len(rows) <= limit:
        return Page(items, None)
    last = items[-1]
    return Page(items, encode_cursor(getattr(last, ts_column.key), getattr(last, id_column.key)))
