import sqlalchemy
import logging
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
