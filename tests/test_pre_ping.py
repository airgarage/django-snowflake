"""Tests for the opt-in pre_ping support on the Snowflake connection pool."""
import pytest
import sqlalchemy.pool as sa_pool

from django_snowflake.base import _SnowflakePingDialect
from django_snowflake.pool import POOL_CONTAINER


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql):
        if self.conn.dead:
            raise OSError("server closed connection")
        return self

    def close(self):
        pass


class FakeConn:
    _counter = 0

    def __init__(self):
        FakeConn._counter += 1
        self.id = FakeConn._counter
        self.dead = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        pass

    def close(self):
        pass


def _creator():
    return FakeConn()


def test_ping_dialect_returns_true_for_live_connection():
    assert _SnowflakePingDialect()._do_ping_w_event(FakeConn()) is True


def test_ping_dialect_returns_false_for_dead_connection():
    conn = FakeConn()
    conn.dead = True
    assert _SnowflakePingDialect()._do_ping_w_event(conn) is False


def test_pre_ping_transparently_reconnects_dead_connection():
    pool = sa_pool.QueuePool(
        _creator, pool_size=2, max_overflow=0, pre_ping=True,
        dialect=_SnowflakePingDialect(),
    )
    conn = pool.connect()
    dead = conn.driver_connection
    conn.close()  # return to pool
    dead.dead = True  # killed while idle

    fresh = pool.connect().driver_connection
    assert fresh.id != dead.id
    assert fresh.dead is False


def test_without_pre_ping_dead_connection_is_handed_back():
    pool = sa_pool.QueuePool(_creator, pool_size=2, max_overflow=0)
    conn = pool.connect()
    dead = conn.driver_connection
    conn.close()
    dead.dead = True

    got = pool.connect().driver_connection
    assert got.id == dead.id and got.dead is True


def test_bare_pool_with_pre_ping_but_no_dialect_raises():
    """Documents the trap: enabling pre_ping on a bare pool crashes on checkout
    because the default stub dialect cannot ping."""
    pool = sa_pool.QueuePool(_creator, pool_size=1, max_overflow=0, pre_ping=True)
    pool.connect().close()  # make the connection non-fresh
    with pytest.raises(NotImplementedError):
        pool.connect()


@pytest.mark.parametrize(
    "pool_config, expected",
    [
        ({"IS_ENABLED": True, "PRE_PING": True}, True),
        ({"IS_ENABLED": True, "PRE_PING": False}, False),
        ({"IS_ENABLED": True}, False),  # default is off
    ],
)
def test_get_connection_params_reads_pre_ping(make_wrapper, pool_config, expected):
    wrapper = make_wrapper(pool=pool_config)
    assert wrapper.get_connection_params()["pre_ping"] is expected


def test_get_connection_params_omits_pool_keys_when_disabled(make_wrapper):
    wrapper = make_wrapper(pool={"IS_ENABLED": False})
    assert "pre_ping" not in wrapper.get_connection_params()


def test_create_pool_attaches_ping_dialect_only_when_enabled(make_wrapper):
    POOL_CONTAINER.clear()
    on = make_wrapper(alias="sf_on", pool={"IS_ENABLED": True, "PRE_PING": True})
    on.create_pool_if_not_exists(on.get_connection_params())
    pool_on = POOL_CONTAINER.get("sf_on")
    assert pool_on._pre_ping is True
    assert isinstance(pool_on._dialect, _SnowflakePingDialect)

    off = make_wrapper(alias="sf_off", pool={"IS_ENABLED": True, "PRE_PING": False})
    off.create_pool_if_not_exists(off.get_connection_params())
    pool_off = POOL_CONTAINER.get("sf_off")
    assert pool_off._pre_ping is False
    assert not isinstance(pool_off._dialect, _SnowflakePingDialect)
