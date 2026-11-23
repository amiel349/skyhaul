import sys
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def frequent_thread_switches() -> Iterator[None]:
    """Make the interpreter switch threads far more often, so races show up if the lock is gone."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(previous)
