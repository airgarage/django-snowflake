"""
Pytest configuration for django-snowflake unit tests.

These tests exercise the pooling, pre_ping, and heartbeat logic in isolation
with fakes; they do not connect to a real Snowflake account. Django settings are
configured here before any ``django_snowflake`` import, because importing the
package pulls in ``django.db``.
"""
import copy
import threading
import time

import pytest


def pytest_configure(config):
    from django.conf import settings

    if settings.configured:
        return

    settings.configure(
        DEBUG=False,
        USE_TZ=False,
        SECRET_KEY="django-snowflake-tests",
        INSTALLED_APPS=["django_snowflake"],
        DATABASES={
            # Django requires a 'default' alias; tests never query it.
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
            "snowflake": {
                "ENGINE": "django_snowflake",
                "NAME": "TEST_DB",
                "USER": "test_user",
                "ACCOUNT": "test_account",
                "WAREHOUSE": "TEST_WH",
                "SCHEMA": "TEST_SCHEMA",
                # An authenticator means PASSWORD is not required.
                "OPTIONS": {"authenticator": "SNOWFLAKE_JWT"},
                "POOL": {
                    "IS_ENABLED": True,
                    "POOL_SIZE": 2,
                    "MAX_OVERFLOW": 3,
                    "PRE_PING": True,
                },
            },
        },
    )

    import django

    django.setup()


@pytest.fixture
def base_settings_dict():
    """A complete (defaults-applied) settings_dict for the 'snowflake' alias."""
    from django.db import connections

    return copy.deepcopy(connections["snowflake"].settings_dict)


@pytest.fixture
def make_wrapper(base_settings_dict):
    """Build a DatabaseWrapper with optional POOL overrides, without connecting."""
    from django_snowflake.base import DatabaseWrapper

    def _make(alias="snowflake", pool=None):
        settings_dict = copy.deepcopy(base_settings_dict)
        if pool is not None:
            settings_dict["POOL"] = pool
        return DatabaseWrapper(settings_dict, alias)

    return _make


@pytest.fixture(autouse=True)
def _reset_pool_and_heartbeat():
    """Keep the process-global pool container and heartbeat singleton isolated
    between tests, and make sure no heartbeat threads leak across tests."""
    yield

    from django_snowflake.heartbeat import HEARTBEAT
    from django_snowflake.pool import POOL_CONTAINER

    HEARTBEAT.shutdown()
    for thread in list(HEARTBEAT._threads.values()):
        if isinstance(thread, threading.Thread):
            thread.join(timeout=2)
    HEARTBEAT._threads.clear()
    HEARTBEAT._last_real_query.clear()
    HEARTBEAT._stop = threading.Event()
    POOL_CONTAINER.clear()


@pytest.fixture
def wait_until():
    """Poll ``predicate`` until true or timeout; returns whether it became true."""
    def _wait(predicate, timeout=3.0, interval=0.02):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    return _wait


@pytest.fixture
def fake_pool():
    """A stand-in for a SQLAlchemy QueuePool that records heartbeat pings and can
    simulate a dead connection."""
    class FakeCursor:
        def __init__(self, pool):
            self.pool = pool

        def execute(self, sql):
            if self.pool.dead:
                raise OSError("dead socket")
            self.pool.pings += 1
            self.pool.last_sql = sql
            self.pool.ping_event.set()

        def close(self):
            pass

    class FakeFairy:
        def __init__(self, pool):
            self.pool = pool
            self.invalidated = False
            self.closed = False

        def cursor(self):
            return FakeCursor(self.pool)

        def invalidate(self):
            self.invalidated = True

        def close(self):
            self.closed = True

    class FakePool:
        def __init__(self):
            self.dead = False
            self.pings = 0
            self.last_sql = None
            self.last_fairy = None
            self.ping_event = threading.Event()

        def connect(self):
            fairy = FakeFairy(self)
            self.last_fairy = fairy
            return fairy

    return FakePool()
