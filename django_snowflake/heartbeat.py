import atexit
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

HEARTBEAT_SQL = "SELECT 1 /* django_snowflake:heartbeat */"

DEFAULT_INTERVAL = 45  # seconds between pings; must be < warehouse AUTO_SUSPEND (60)
DEFAULT_IDLE_THRESHOLD = 900  # stop pinging after this many seconds with no real query


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _get_setting(name: str, default: Any, cast):
    """Read config from Django settings first, then env var, then default."""
    from django.conf import settings

    value = getattr(settings, name, None)
    if value is None:
        value = os.environ.get(name)
    if value is None:
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        logger.warning(f"Invalid value {value!r} for {name}; using default {default!r}")
        return default


class HeartbeatManager:
    """
    Per-process, traffic-aware Snowflake warehouse heartbeat.

    Runs one daemon thread per database alias. While real queries have occurred
    within the idle threshold, it issues a lightweight ``SELECT 1`` every
    ``interval`` seconds to keep the warehouse above its ``AUTO_SUSPEND`` timeout
    so warm queries stay warm. After the idle threshold elapses with no real
    queries, it stops pinging and lets the warehouse suspend, so genuinely idle
    periods (nights/weekends) cost nothing.

    The warehouse is a shared, global resource, so a single worker pinging keeps
    it warm for every worker's queries. Lifecycle is per process: state is reset
    after ``fork()`` (threads do not survive fork), so each Gunicorn worker that
    actually talks to Snowflake runs its own heartbeat. It is started lazily on
    the first connection rather than at import/ready() time, which keeps it from
    ever starting in a Gunicorn preload master.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._threads = {}
        self._last_real_query = {}
        self._stop = threading.Event()
        self._pid = os.getpid()
        self._atexit_registered = False

    def record_query(self, alias):
        """Record that a real (non-heartbeat) query ran against ``alias``."""
        self._last_real_query[alias] = time.monotonic()

    def reset_after_fork(self):
        """Drop inherited thread handles so the child starts its own threads."""
        self._lock = threading.Lock()
        self._threads = {}
        self._stop = threading.Event()
        self._pid = os.getpid()

    def ensure_started(self, alias):
        """Idempotently start the heartbeat (and a one-shot warm-up) for ``alias``
        in the current process."""
        if not _get_setting("SNOWFLAKE_HEARTBEAT_ENABLED", True, _as_bool):
            return

        if self._pid == os.getpid() and alias in self._threads:
            return

        with self._lock:
            if self._pid != os.getpid():
                self._threads = {}
                self._stop = threading.Event()
                self._pid = os.getpid()
            if alias in self._threads:
                return

            self._last_real_query.setdefault(alias, time.monotonic())
            thread = threading.Thread(
                target=self._run,
                args=(alias, self._stop),
                name=f"snowflake_heartbeat_{alias}",
                daemon=True,
            )
            self._threads[alias] = thread
            if not self._atexit_registered:
                atexit.register(self.shutdown)
                self._atexit_registered = True
            thread.start()
            logger.info(f"Started Snowflake heartbeat for '{alias}' (pid {self._pid})")

        # Warm the pool once per process, off the request thread.
        threading.Thread(
            target=self._warm,
            args=(alias, self._stop),
            name=f"snowflake_pool_warmer_{alias}",
            daemon=True,
        ).start()

    def _warm(self, alias, stop):
        from django.conf import settings

        from .pool import POOL_CONTAINER

        try:
            pool_size = int(
                settings.DATABASES[alias].get("POOL", {}).get("POOL_SIZE", 5)
            )
            POOL_CONTAINER.warm_pool(alias, pool_size, exit_event=stop)
        except Exception as e:
            logger.warning(f"Snowflake pool warm-up failed for '{alias}': {e}")

    def _run(self, alias, stop):
        from .pool import POOL_CONTAINER

        interval = max(
            1, _get_setting("SNOWFLAKE_HEARTBEAT_INTERVAL", DEFAULT_INTERVAL, int)
        )
        idle_threshold = _get_setting(
            "SNOWFLAKE_HEARTBEAT_IDLE_THRESHOLD", DEFAULT_IDLE_THRESHOLD, int
        )

        while not stop.wait(interval):
            last_real = self._last_real_query.get(alias, 0.0)
            if time.monotonic() - last_real >= idle_threshold:
                continue  # truly idle: let the warehouse suspend to save cost
            try:
                pool = POOL_CONTAINER.get(alias)
            except ValueError:
                continue  # pool not created in this process yet
            self._ping(pool, alias)

    def _ping(self, pool, alias):
        conn = None
        try:
            conn = pool.connect()
            cursor = conn.cursor()
            try:
                cursor.execute(HEARTBEAT_SQL)
            finally:
                cursor.close()
            logger.debug(f"Snowflake heartbeat ping ok for '{alias}'")
        except Exception as e:
            logger.warning(f"Snowflake heartbeat ping failed for '{alias}': {e}")
            if conn is not None:
                try:
                    conn.invalidate()
                except Exception:
                    pass
                conn = None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def shutdown(self):
        self._stop.set()


HEARTBEAT = HeartbeatManager()
