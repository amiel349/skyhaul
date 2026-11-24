"""Wiring of the four long-lived threads and the shutdown sequence (design note section 6).

Threads: the consumer runs on the calling thread (the main thread in production), the
scheduler, the publisher and the control API (`control-http`) on their own. One stop event
ends the first three; the control server is shut down explicitly.

Shutdown, on `stop()` (signal handlers call it):
1. the consumer finishes its current message, closes its channel (the broker requeues the
   prefetched commands nobody acked) and returns;
2. the control server finishes its current request and stops; its thread is joined;
3. the scheduler is joined (its `stop.wait` returns True);
4. every producer has stopped, so the sentinel goes into the outbox;
5. the publisher drains up to the sentinel, with confirms, for at most `drain_s`, then
   closes its connection. Anything left is logged as dropped.
The final snapshot (step 8 of the design note) arrives with the snapshot issue.
"""

import logging
import queue
import threading
import time
from typing import Any, Final

from skyhaul_contracts import Codec, NackReason

from skyhaul_drone_sim.amqp import BrokerUnavailable, Connect, connector
from skyhaul_drone_sim.behaviors import add_drone, spawn_drone
from skyhaul_drone_sim.board import JobsBoard, ResetResult
from skyhaul_drone_sim.clock import Clock, SystemClock
from skyhaul_drone_sim.config import SimConfig
from skyhaul_drone_sim.consumer import Consumer
from skyhaul_drone_sim.control_api import ControlServer, create_app
from skyhaul_drone_sim.model import GeoPoint
from skyhaul_drone_sim.outbox import SENTINEL, Outbox, new_outbox
from skyhaul_drone_sim.publisher import Publisher
from skyhaul_drone_sim.registry import Registry
from skyhaul_drone_sim.scheduler import Scheduler

log = logging.getLogger(__name__)

RESET_FENCE_S: Final = 2.0
"""How long a reset waits for the message in flight in the publisher (see Publisher)."""

JOIN_GRACE_S: Final = 2.0
"""Extra time, past the drain bound, before a stuck thread is given up on."""

EXIT_OK: Final = 0
EXIT_BROKER_UNAVAILABLE: Final = 1
EXIT_CONTROL_UNAVAILABLE: Final = 3
"""The control API port could not be bound (2 is a bad configuration, in __main__)."""


class Simulator:
    def __init__(
        self,
        cfg: SimConfig,
        *,
        registry: Registry,
        clock: Clock | None = None,
        consumer_connect: Connect | None = None,
        publisher_connect: Connect | None = None,
    ) -> None:
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.clock = clock if clock is not None else SystemClock()
        self.codec = Codec()
        self.board = JobsBoard()
        self.outbox = new_outbox(cfg.outbox_max)
        self._spawn(cfg.drones)
        self.publisher = Publisher(
            codec=self.codec,
            outbox=self.outbox,
            stop=self.stop_event,
            is_current=self.board.is_current,
            connect=publisher_connect or connector(cfg, "publisher"),
            connect_timeout_s=cfg.connect_timeout_s,
            drain_s=cfg.drain_s,
        )
        self.scheduler = Scheduler(
            board=self.board,
            outbox=self.outbox,
            clock=self.clock,
            stop=self.stop_event,
            tick_s=cfg.tick_s,
        )
        self.scheduler_thread = threading.Thread(
            target=self.scheduler.run, name="scheduler", daemon=True
        )
        self.consumer = Consumer(
            codec=self.codec,
            board=self.board,
            registry=registry,
            outbox=self.outbox,
            clock=self.clock,
            stop=self.stop_event,
            connect=consumer_connect or connector(cfg, "consumer"),
            connect_timeout_s=cfg.connect_timeout_s,
            prefetch=cfg.prefetch,
            debug_tap=cfg.debug_tap,
        )
        self.app = create_app(
            self.board,
            cfg,
            clock=self.clock,
            health=self.healthy,
            stats=self.stats,
            reset=self.reset,
        )
        self.control: ControlServer | None = None

    def _spawn(self, drone_ids: tuple[str, ...]) -> None:
        """The optional `SIM_DRONES` seed, landed at the configured home. Seeded drones do
        not move a namespace's id counter, so spawning over them is a 409."""
        now = self.clock.monotonic()
        home = GeoPoint(self.cfg.home_lat, self.cfg.home_lon)
        with self.board.transaction() as world:
            for drone_id in drone_ids:
                drone, jobs = spawn_drone(
                    drone_id,
                    home=home,
                    now=now,
                    seed=self.cfg.seed,
                    geofence_radius_m=self.cfg.geofence_radius_m,
                )
                add_drone(world, drone, jobs)

    def healthy(self) -> bool:
        """The consumer, scheduler and publisher are running and both connections are
        open. Reads events and thread states only, so it never blocks."""
        return (
            self.consumer.connected.is_set()
            and self.consumer.is_alive()
            and self.scheduler_thread.is_alive()
            and self.publisher.is_alive()
            and self.publisher.connected.is_set()
        )

    def stats(self) -> dict[str, Any]:
        """The `/stats` counters, cumulative since start (a reset does not clear them)."""
        published = self.publisher.stats()
        codec = self.codec.stats()
        nacks = {reason.value: 0 for reason in NackReason} | published.nacks_by_reason
        return {
            "outbox_depth": self.outbox.qsize(),
            "published": published.published,
            "dropped_stale": published.dropped_stale,
            "encode_errors": codec.encode_errors,
            "unknown_drone_commands": self.board.unknown_drone_commands(),
            "nacks_by_reason": nacks,
            "codec_rejects": dict(codec.rejected_by_reason),
        }

    def reset(self, namespace: str) -> ResetResult:
        """Reset the namespace on the board (atomic, bumps its epoch), then wait for the
        message in flight in the publisher. When this returns, nothing produced for the
        namespace before the reset will be published (ADR 0009)."""
        result = self.board.reset(namespace)
        if self.publisher.is_alive() and not self.publisher.wait_for_in_flight(RESET_FENCE_S):
            log.warning("reset did not see the publisher finish its current message")
        return result

    def stop(self) -> None:
        """Ask every thread to stop. Safe from a signal handler or any thread."""
        self.stop_event.set()

    def run(self) -> int:
        """Run until stopped. Returns the process exit code."""
        log.info("simulator starting", extra={"drones": len(self.cfg.drones)})
        try:
            self.control = ControlServer(self.app, self.cfg.control_host, self.cfg.control_port)
        except OSError as exc:
            log.error(
                "control API could not listen",
                extra={"port": self.cfg.control_port, "error": exc.strerror},
            )
            return EXIT_CONTROL_UNAVAILABLE
        self.control.start()
        log.info("control API listening", extra={"port": self.control.port})
        self.publisher.start()
        self.publisher.ready.wait()
        if self.publisher.failed:
            stopping = self.stop_event.is_set()  # a signal during startup is not an error
            self.stop()
            self.control.shutdown()
            self.publisher.join()
            return EXIT_OK if stopping else EXIT_BROKER_UNAVAILABLE
        self.scheduler_thread.start()
        code = EXIT_OK
        try:
            self.consumer.run()
        except BrokerUnavailable as exc:
            if not self.stop_event.is_set():
                log.error("consumer could not connect", extra={"error": str(exc)})
                code = EXIT_BROKER_UNAVAILABLE
        finally:
            self._shutdown()
        return code

    def _shutdown(self) -> None:
        started = time.monotonic()
        self.stop()
        self.publisher.begin_drain()
        if self.control is not None:
            self.control.shutdown()
        self.scheduler_thread.join(JOIN_GRACE_S)
        give_up_at = time.monotonic() + self.cfg.drain_s + JOIN_GRACE_S
        self._put_sentinel(give_up_at)
        self.publisher.join(max(0.0, give_up_at - time.monotonic()))
        if self.publisher.is_alive():
            log.error("publisher did not stop in time")
        left = _count_left(self.outbox)
        if left:
            log.error("messages dropped at shutdown", extra={"dropped": left})
        log.info(
            "simulator stopped",
            extra={
                "published": self.publisher.published,
                "dropped": self.publisher.dropped + left,
                "shutdown_s": round(time.monotonic() - started, 3),
            },
        )

    def _put_sentinel(self, give_up_at: float) -> None:
        """Queue the sentinel, unless the publisher is gone or stuck past the bound."""
        while self.publisher.is_alive() and time.monotonic() < give_up_at:
            try:
                self.outbox.put(SENTINEL, timeout=0.2)
                return
            except queue.Full:
                continue


def _count_left(outbox: Outbox) -> int:
    left = 0
    while True:
        try:
            item = outbox.get_nowait()
        except queue.Empty:
            return left
        if item is not SENTINEL:
            left += 1
