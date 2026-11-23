"""`python -m skyhaul_drone_sim` / `skyhaul-drone-sim`: run the simulator until SIGTERM or
SIGINT. Exit codes: 0 after a clean shutdown, 1 when RabbitMQ is unreachable, 2 when the
configuration is invalid."""

import logging
import signal
import sys
import threading
from types import FrameType

from skyhaul_drone_sim import logs
from skyhaul_drone_sim.config import ConfigError, SimConfig
from skyhaul_drone_sim.registry import Registry
from skyhaul_drone_sim.simulator import Simulator

log = logging.getLogger("skyhaul_drone_sim")

EXIT_BAD_CONFIG = 2


def main(registry: Registry | None = None) -> int:
    """Run on the main thread, which becomes the consumer. `registry` defaults to the real
    handlers; tests pass their own."""
    logs.configure()
    try:
        cfg = SimConfig.from_env()
    except ConfigError as exc:
        log.error("invalid configuration", extra={"error": str(exc)})
        return EXIT_BAD_CONFIG
    if registry is None:
        from skyhaul_drone_sim.handlers import registry as default_registry

        registry = default_registry
    sim = Simulator(cfg, registry=registry)

    def on_signal(signum: int, frame: FrameType | None) -> None:
        # Only sets the stop event: safe whatever the main thread was doing.
        sim.stop()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    threading.current_thread().name = "consumer"
    return sim.run()


def run() -> None:
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    run()
