"""Tests for fork-safety of the pool container and heartbeat manager."""
import os

import pytest

from django_snowflake.heartbeat import HEARTBEAT
from django_snowflake.pool import POOL_CONTAINER, _after_fork_in_child


def test_after_fork_clears_pool_and_resets_lock():
    POOL_CONTAINER["snowflake"] = object()
    old_lock = POOL_CONTAINER.lock

    _after_fork_in_child()

    assert len(POOL_CONTAINER) == 0
    assert POOL_CONTAINER.lock is not old_lock


def test_after_fork_resets_heartbeat_state():
    HEARTBEAT._threads["snowflake"] = "stale-thread-handle"
    old_stop = HEARTBEAT._stop

    _after_fork_in_child()

    assert HEARTBEAT._threads == {}
    assert HEARTBEAT._stop is not old_stop
    assert HEARTBEAT._pid == os.getpid()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork()")
def test_register_at_fork_clears_inherited_pool_in_child():
    """The os.register_at_fork handler registered at import must clear the pool
    in a real forked child so workers never reuse inherited connections."""
    POOL_CONTAINER["snowflake"] = object()

    pid = os.fork()
    if pid == 0:
        # Child: the after-fork handler should already have cleared the pool.
        os._exit(0 if len(POOL_CONTAINER) == 0 else 1)

    _, status = os.waitpid(pid, 0)
    POOL_CONTAINER.clear()
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
