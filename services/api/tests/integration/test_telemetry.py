"""Telemetry history and the Redis latest-state merge on the drone endpoints (#27)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
import redis
from flask.testing import FlaskClient
from skyhaul_fleet_core.models import Drone, Role, Telemetry, User
from sqlalchemy.orm import Session

from .conftest import Stack, bearer

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 20, 12, tzinfo=UTC)


def _drone_with_rows(db_session: Session, count: int) -> str:
    drone_id = f"tl-{uuid.uuid4().hex[:8]}"
    db_session.add(Drone(id=drone_id, name="T", home_lat=32.0, home_lon=34.8))
    for i in range(count):
        db_session.add(
            Telemetry(
                msg_id=uuid.uuid4(),
                drone_id=drone_id,
                type="POSITION" if i % 2 else "BATTERY",
                ts=T0 + timedelta(seconds=i),
                correlation_id=None,
                payload={"i": i},
            )
        )
    db_session.commit()
    return drone_id


def test_telemetry_history_pages_newest_first_for_every_role_and_404s_unknown_drones(
    api: FlaskClient,
    db_session: Session,
    make_user: Callable[[Role], User],
    token_for: Callable[[User], str],
) -> None:
    drone_id = _drone_with_rows(db_session, 60)
    url = f"/api/v1/drones/{drone_id}/telemetry"
    for role in (Role.VIEWER, Role.DISPATCHER, Role.ADMIN):
        assert api.get(url, headers=bearer(token_for(make_user(role)))).status_code == 200
    headers = bearer(token_for(make_user(Role.VIEWER)))

    first = api.get(url, headers=headers).get_json()
    second = api.get(f"{url}?cursor={first['next_cursor']}", headers=headers).get_json()
    seen = [item["payload"]["i"] for item in first["items"] + second["items"]]
    assert seen == list(range(59, -1, -1))  # default page 50, newest first, no gaps
    assert second["next_cursor"] is None
    assert set(first["items"][0]) == {
        "msg_id",
        "drone_id",
        "type",
        "ts",
        "received_at",
        "correlation_id",
        "payload",
    }

    since, until = (
        (T0 + timedelta(seconds=10)).isoformat(),
        (T0 + timedelta(seconds=20)).isoformat(),
    )
    window = api.get(
        url, headers=headers, query_string={"type": "POSITION", "since": since, "until": until}
    ).get_json()
    assert [item["payload"]["i"] for item in window["items"]] == [19, 17, 15, 13, 11]
    assert api.get(f"{url}?limit=200", headers=headers).status_code == 200
    assert api.get(f"{url}?limit=201", headers=headers).status_code == 400
    assert api.get(f"{url}?type=HEARTBEAT", headers=headers).status_code == 400
    missing = api.get("/api/v1/drones/no-such-drone/telemetry", headers=headers)
    assert missing.status_code == 404


def test_drone_list_and_detail_merge_the_redis_latest_state(
    api: FlaskClient,
    stack: Stack,
    db_session: Session,
    make_user: Callable[[Role], User],
    token_for: Callable[[User], str],
) -> None:
    with_state, without = _drone_with_rows(db_session, 0), _drone_with_rows(db_session, 0)
    fields = {
        "flight_state": "HOVERING",
        "armed": True,
        "battery_pct": 77.5,
        "lat": 32.1,
        "lon": 34.9,
        "altitude_m": 20,
        "heading_deg": 90,
        "ground_speed_mps": 0,
        "link": "UP",
        "ts": "2026-11-25T10:00:00.000Z",
    }
    client = redis.Redis.from_url(stack.redis_url)
    client.hset(f"drone:{with_state}", mapping={k: json.dumps(v) for k, v in fields.items()})
    headers = bearer(token_for(make_user(Role.VIEWER)))

    listed = api.get("/api/v1/drones?limit=200", headers=headers).get_json()["items"]
    states = {d["id"]: d["state"] for d in listed}
    detail = api.get(f"/api/v1/drones/{with_state}", headers=headers).get_json()

    assert states[without] is None
    assert states[with_state]["flight_state"] == "HOVERING"
    assert states[with_state]["position"]["lat"] == 32.1
    assert states[with_state]["est_remaining_s"] is None
    assert detail["state"] == states[with_state]
