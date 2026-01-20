from collections import UserDict
import threading
import logging
from typing import Any


logger = logging.getLogger(__name__)

class ConnectionPool(dict):
    lock = threading.Lock()

    def has(self, pool_name: str) -> bool:
        return pool_name in self

    def set(self, pool_name: str, pool: Any) -> None:
        with self.lock:
            if self.has(pool_name):
                return
            
            self[pool_name] = pool
            logger.info(f"Pool {pool_name} added to the pool container.")

    def get(self, pool_name: str) -> Any:
        try:
            with self.lock:
                return self[pool_name]
        except KeyError:
            raise ValueError(f"Pool {pool_name} does not exist.")

    def dispose(self):
        for _, pool in self.items():
            pool.dispose()
            
POOL_CONTAINER = ConnectionPool()
