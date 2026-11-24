"""The simulator's control API (services/drone_sim/openapi.yaml) over httpx, with the token."""

from __future__ import annotations

from typing import Any, Final

import httpx

from skyhaul_mcp_sim.config import Config
from skyhaul_mcp_sim.errors import ToolFailure

TIMEOUT_S: Final = 10.0


class ControlApi:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._config = config
        self._transport = transport

    async def call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        """The response's JSON (None for 204). Raises ToolFailure for anything else."""
        token = self._config.token()
        async with httpx.AsyncClient(
            base_url=self._config.control_base_url(),
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_S,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            try:
                response = await client.request(method, path, json=body, params=params)
            except httpx.TransportError:
                raise ToolFailure(
                    "control_api_unreachable",
                    "the simulator control API at SIM_CONTROL_URL did not answer; "
                    "is the simulator running (make sim)?",
                ) from None
        return _result(response)


def _result(response: httpx.Response) -> Any:
    status = response.status_code
    if status == 204:
        return None
    if status == 401:
        raise ToolFailure(
            "unauthorized",
            "the control API refused the token: SIM_CONTROL_TOKEN must match the simulator's",
            status=401,
        )
    try:
        data = response.json()
    except ValueError:
        data = None
    if 200 <= status < 300 and data is not None:
        return data
    error = data if isinstance(data, dict) else {}
    raise ToolFailure(
        str(error.get("error") or "control_api_error"),
        str(error.get("detail") or f"the control API answered {status}"),
        status=status,
    )
