"""``skyhaul-ingestor`` / ``python -m skyhaul_ingestor``. SIGTERM and SIGINT stop it after
a last flush."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType

from skyhaul_fleet_core.settings import SettingsError

from skyhaul_ingestor import logs
from skyhaul_ingestor.config import load_config
from skyhaul_ingestor.consumer import run

EXIT_CONFIG = 3

log = logging.getLogger("skyhaul_ingestor")


def main() -> None:
    logs.configure()
    try:
        config = load_config()
    except SettingsError as exc:
        log.error("bad configuration: %s", exc)
        sys.exit(EXIT_CONFIG)
    stop = threading.Event()

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    sys.exit(run(config, stop))


if __name__ == "__main__":
    main()
