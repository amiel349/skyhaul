"""A minimal HTTP client for the simulator's control API, for the concurrency and integration
tests (the automation framework's client and fixture come later)."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Reply:
    status: int
    body: Any


@dataclass
class ControlClient:
    base_url: str
    token: str = field(repr=False)

    def call(self, method: str, path: str, body: Any = None, *, token: str | None = None) -> Reply:
        """One request. `token` overrides the client's (an empty string sends none)."""
        token = self.token if token is None else token
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(  # noqa: S310 - local test URL
            self.base_url + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                raw = response.read()
                return Reply(response.status, json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return Reply(exc.code, json.loads(raw) if raw else None)

    def spawn(self, namespace: str, count: int, **extra: Any) -> list[str]:
        reply = self.call("POST", "/drones", {"namespace": namespace, "count": count, **extra})
        assert reply.status == 201, reply
        return [drone["drone_id"] for drone in reply.body["drones"]]

    def reset(self, namespace: str) -> dict[str, Any]:
        reply = self.call("POST", "/reset", {"namespace": namespace})
        assert reply.status == 200, reply
        result: dict[str, Any] = reply.body
        return result

    def stats(self) -> dict[str, Any]:
        reply = self.call("GET", "/stats")
        assert reply.status == 200, reply
        result: dict[str, Any] = reply.body
        return result

    def wait_healthy(self, timeout_s: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                if self.call("GET", "/healthz", token="").status == 200:
                    return
            except (urllib.error.URLError, ConnectionError):
                pass
            assert time.monotonic() < deadline, "the control API never became healthy"
            time.sleep(0.05)
