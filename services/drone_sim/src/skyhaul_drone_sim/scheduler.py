"""The scheduler thread: every tick, ask the board what is due and put it in the outbox.

The loop waits on the stop event (no sleep), stays on its own 100 ms grid (no drift), and
never catches up after a stall (no burst). A failing tick is logged and the next one runs.
"""

import logging
import threading
from collections.abc import Callable

from skyhaul_drone_sim.board import DueResult, JobsBoard
from skyhaul_drone_sim.clock import Clock
from skyhaul_drone_sim.outbox import Outbox, put_until_stopped

log = logging.getLogger(__name__)

type Wait = Callable[[float], bool]
"""`wait(timeout_s)` returns True when the scheduler should stop (`Event.wait`)."""


class Scheduler:
    def __init__(
        self,
        *,
        board: JobsBoard,
        outbox: Outbox,
        clock: Clock,
        stop: threading.Event,
        tick_s: float,
        wait: Wait | None = None,
    ) -> None:
        self._board = board
        self._outbox = outbox
        self._clock = clock
        self._stop = stop
        self._tick_s = tick_s
        self._wait = wait if wait is not None else stop.wait

    def run(self) -> None:
        """Tick until the stop event is set."""
        next_t = self._clock.monotonic()
        last = next_t
        while not self._wait(max(0.0, next_t - self._clock.monotonic())):
            now = self._clock.monotonic()
            self.tick(now, now - last)
            last = now
            next_t = max(next_t + self._tick_s, now)  # no catch-up burst

    def tick(self, now: float, dt: float) -> None:
        """One tick. Never raises, so one bad tick cannot stop the thread."""
        try:
            result = self._board.due(now, dt)  # takes and releases the board lock
        except Exception:
            log.exception("scheduler tick failed")
            return
        _log_errors(result)  # outside the lock
        for out in result.messages:
            if not put_until_stopped(self._outbox, out, self._stop):
                log.warning("stopping with a full outbox; telemetry from this tick dropped")
                return


def _log_errors(result: DueResult) -> None:
    for job_error in result.errors:
        error = job_error.error
        drone_id, job_type = job_error.key
        log.error(
            "job failed",
            exc_info=(type(error), error, error.__traceback__),
            extra={"drone_id": drone_id, "type": job_type.value},
        )
