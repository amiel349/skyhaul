import pytest
from mcp_support.harness import Harness, new_harness


@pytest.fixture
def harness() -> Harness:
    return new_harness()
