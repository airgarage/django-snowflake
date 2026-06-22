"""Tests for the traffic-aware Snowflake warehouse heartbeat."""
import os
import time

import pytest
from django.conf import settings

from django_snowflake.heartbeat import (
    HEARTBEAT, HEARTBEAT_SQL, _as_bool, _get_setting,
)
from django_snowflake.pool import POOL_CONTAINER


@pytest.fixture(autouse=True)
def _no_warm(monkeypatch):
    """Isolate heartbeat behavior from the one-shot pool warm-up thread."""
    monkeypatch.setattr(HEARTBEAT, "_warm", lambda *a, **k: None)


def _configure(monkeypatch, interval=1, idle=900, enabled=True):
    monkeypatch.setattr(settings, "SNOWFLAKE_HEARTBEAT_INTERVAL", interval, raising=False)
    monkeypatch.setattr(settings, "SNOWFLAKE_HEARTBEAT_IDLE_THRESHOLD", idle, raising=False)
    monkeypatch.setattr(settings, "SNOWFLAKE_HEARTBEAT_ENABLED", enabled, raising=False)


# --- pure config helpers -------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [(True, True), (False, False), ("true", True), ("1", True),
     ("yes", True), ("on", True), ("false", False), ("0", False), ("", False)],
)
def test_as_bool(value, expected):
    assert _as_bool(value) is expected


def test_get_setting_precedence(monkeypatch):
    # Django setting wins over env var.
    monkeypatch.setattr(settings, "SNOWFLAKE_HEARTBEAT_INTERVAL", 30, raising=False)
    monkeypatch.setenv("SNOWFLAKE_HEARTBEAT_INTERVAL", "99")
    assert _get_setting("SNOWFLAKE_HEARTBEAT_INTERVAL", 45, int) == 30

    # Env var used when no Django setting.
    monkeypatch.delattr(settings, "SNOWFLAKE_HEARTBEAT_INTERVAL", raising=False)
    assert _get_setting("SNOWFLAKE_HEARTBEAT_INTERVAL", 45, int) == 99

    # Default when neither is set.
    monkeypatch.delenv("SNOWFLAKE_HEARTBEAT_INTERVAL", raising=False)
    assert _get_setting("SNOWFLAKE_HEARTBEAT_INTERVAL", 45, int) == 45


def test_get_setting_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(settings, "SNOWFLAKE_HEARTBEAT_INTERVAL", "not-an-int", raising=False)
    assert _get_setting("SNOWFLAKE_HEARTBEAT_INTERVAL", 45, int) == 45


def test_heartbeat_sql_is_tagged_for_apm_filtering():
    assert "django_snowflake:heartbeat" in HEARTBEAT_SQL


# --- lifecycle -----------------------------------------------------------

def test_record_query_updates_timestamp():
    HEARTBEAT.record_query("snowflake")
    assert "snowflake" in HEARTBEAT._last_real_query


def test_disabled_flag_prevents_start(monkeypatch):
    _configure(monkeypatch, enabled=False)
    HEARTBEAT.ensure_started("snowflake")
    assert "snowflake" not in HEARTBEAT._threads


def test_ensure_started_is_idempotent(monkeypatch):
    _configure(monkeypatch, interval=60)  # large interval: won't actually ping
    HEARTBEAT.ensure_started("snowflake")
    first = HEARTBEAT._threads["snowflake"]
    HEARTBEAT.ensure_started("snowflake")
    assert HEARTBEAT._threads["snowflake"] is first
    assert len([t for t in HEARTBEAT._threads if t == "snowflake"]) == 1


def test_pid_change_resets_state_then_starts(monkeypatch):
    _configure(monkeypatch, interval=60)
    HEARTBEAT._pid = -1  # simulate inherited state from a different process
    HEARTBEAT.ensure_started("snowflake")
    assert HEARTBEAT._pid == os.getpid()
    assert "snowflake" in HEARTBEAT._threads


# --- pinging behavior ----------------------------------------------------

def test_pings_while_active_then_stops_when_idle(monkeypatch, fake_pool, wait_until):
    _configure(monkeypatch, interval=1, idle=2)
    POOL_CONTAINER["snowflake"] = fake_pool
    HEARTBEAT.record_query("snowflake")
    HEARTBEAT.ensure_started("snowflake")

    assert wait_until(lambda: fake_pool.pings >= 1, timeout=3), "expected a ping while active"
    assert "heartbeat" in fake_pool.last_sql

    # Past the idle threshold with no new real queries: pinging must stop.
    fake_pool.pings = 0
    time.sleep(2.5)
    assert fake_pool.pings == 0, "heartbeat should stop after the idle threshold"

    # New real traffic resumes the heartbeat without restarting the thread.
    HEARTBEAT.record_query("snowflake")
    assert wait_until(lambda: fake_pool.pings >= 1, timeout=3), "expected pings to resume"


def test_ping_failure_invalidates_connection_and_thread_survives(
    monkeypatch, fake_pool, wait_until
):
    _configure(monkeypatch, interval=1, idle=900)
    fake_pool.dead = True
    POOL_CONTAINER["snowflake"] = fake_pool
    HEARTBEAT.record_query("snowflake")
    HEARTBEAT.ensure_started("snowflake")

    assert wait_until(
        lambda: fake_pool.last_fairy is not None and fake_pool.last_fairy.invalidated,
        timeout=3,
    ), "dead connection should be invalidated"
    assert fake_pool.pings == 0
    assert HEARTBEAT._threads["snowflake"].is_alive(), "thread must survive a failed ping"


def test_idle_alias_does_not_ping(monkeypatch, fake_pool):
    """A heartbeat started with no recent real query (older than idle) should not
    ping, so a long-idle warehouse is allowed to suspend."""
    _configure(monkeypatch, interval=1, idle=1)
    POOL_CONTAINER["snowflake"] = fake_pool
    # Mark the last real query far in the past.
    HEARTBEAT._last_real_query["snowflake"] = 0.0
    HEARTBEAT.ensure_started("snowflake")

    time.sleep(1.5)
    assert fake_pool.pings == 0
