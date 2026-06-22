import sqlalchemy
import logging
import os
import threading
from typing import Any, List

logger = logging.getLogger(__name__)


class ConnectionPool(dict):
    lock = threading.Lock()

    def has(self, pool_name: str) -> bool:
        with self.lock:
            return pool_name in self

    def set(self, pool_name: str, pool: Any) -> None:
        # Use a single lock instead of using .has() to prevent Time-of-Check-Time-of-Use race conditions
        with self.lock:
            if pool_name in self:
                return
            self[pool_name] = pool
            logger.debug(f"Pool {pool_name} added to the pool container.")

    def get(self, pool_name: str) -> Any:
        try:
            with self.lock:
                return self[pool_name]
        except KeyError:
            raise ValueError(f"Pool {pool_name} does not exist.")

    def dispose(self):
        for _, pool in self.items():
            pool.dispose()

    def warm_pool(
        self, pool_name: str, num_connections: int, exit_event: threading.Event
    ) -> None:
        """
        Warm up a connection pool by populating with active connections.
        """
        if not self.has(pool_name):
            raise ValueError(f"Pool {pool_name} does not exist.")

        pool = self.get(pool_name)
        connections: List[sqlalchemy.engine.Connection] = []

        try:
            for i in range(num_connections):
                if exit_event.is_set():
                    logger.debug(f"Exit event detected for {pool_name}")
                    return
                conn: sqlalchemy.engine.Connection = pool.connect()
                connections.append(conn)
                logger.debug(
                    f"Created connection {i + 1}/{num_connections} for pool {pool_name}"
                )

            logger.debug(
                f"Successfully created {num_connections} connections for pool {pool_name}"
            )
        except Exception as e:
            logger.error(f"Failed to warm up pool {pool_name}: {e}")
        finally:
            for conn in connections:
                try:
                    if exit_event.is_set():
                        conn.invalidate()
                        continue

                    conn.close()
                except Exception as e:
                    logger.error(f"Failed to close connection: {e}")
            logger.debug(
                f"Released {len(connections)} connections back to pool {pool_name}"
            )


POOL_CONTAINER = ConnectionPool()


def _after_fork_in_child() -> None:
    """
    Reset pool state in a forked child (e.g. a Gunicorn worker).

    Connections established before fork() share OS sockets with the parent and
    cannot be used safely in the child. We drop the inherited pools (without
    gracefully closing them, which would log out the shared session and disrupt
    the parent) so each worker lazily builds its own connections, and we reset
    the inherited lock in case the parent held it at fork time. The heartbeat
    manager's threads are reset too, since threads do not survive fork().

    With lazy, per-worker startup the parent normally holds no Snowflake
    connections at all, so this is defensive insurance rather than the common path.
    """
    POOL_CONTAINER.lock = threading.Lock()
    POOL_CONTAINER.clear()
    try:
        from .heartbeat import HEARTBEAT

        HEARTBEAT.reset_after_fork()
    except Exception as e:  # pragma: no cover - best effort
        logger.debug(f"Heartbeat reset after fork skipped: {e}")


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)
