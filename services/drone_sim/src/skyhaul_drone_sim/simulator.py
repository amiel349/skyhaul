"""Wiring of the three core threads and the shutdown sequence (design note section 6).

Threads: the consumer runs on the calling thread (the main thread in production), the
scheduler and the publisher on their own. One stop event ends all of them.

Shutdown, on `stop()` (signal handlers call it):
1. the consumer finishes its current message, closes its channel (the broker requeues the
   prefetched commands nobody acked) and returns;
2. the scheduler is joined (its `stop.wait` returns True);
3. every producer has stopped, so the sentinel goes into the outbox;
4. the publisher drains up to the sentinel, with confirms, for at most `drain_s`, then
   closes its connection. Anything left is logged as dropped.
The control API (step 4 of the design note) and the final snapshot arrive with #9.
"""

import logging
import queue
import threading
import time
from typing import Final

from skyhaul_contracts import Codec

from skyhaul_drone_sim.amqp import BrokerUnavailable, Connect, connector
from skyhaul_drone_sim.board import JobsBoard
from skyhaul_drone_sim.clock import Clock, SystemClock
from skyhaul_drone_sim.config import SimConfig
from skyhaul_drone_sim.consumer import Consumer
from skyhaul_drone_sim.outbox import SENTINEL, Outbox, new_outbox
from skyhaul_drone_sim.publisher import Publisher
from skyhaul_drone_sim.registry import Registry
from skyhaul_drone_sim.scheduler import Scheduler

log = logging.getLogger(__name__)

JOIN_GRACE_S: Final = 2.0
"""Extra time, past the drain bound, before a stuck thread is given up on."""

EXIT_OK: Final = 0
EXIT_BROKER_UNAVAILABLE: Final = 1


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
        for drone_id in cfg.drones:
            self.board.add_drone(drone_id)
        self.publisher = Publisher(
            codec=self.codec,
            outbox=self.outbox,
            stop=self.stop_event,
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

    def stop(self) -> None:
        """Ask every thread to stop. Safe from a signal handler or any thread."""
        self.stop_event.set()

    def run(self) -> int:
        """Run until stopped. Returns the process exit code."""
        log.info("simulator starting", extra={"drones": len(self.cfg.drones)})
        self.publisher.start()
        self.publisher.ready.wait()
        if self.publisher.failed:
            stopping = self.stop_event.is_set()  # a signal during startup is not an error
            self.stop()
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
        self.scheduler_thread.join(JOIN_GRACE_S)
        self._put_sentinel()
        self.publisher.join(self.cfg.drain_s + JOIN_GRACE_S)
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

    def _put_sentinel(self) -> None:
        while self.publisher.is_alive():
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
