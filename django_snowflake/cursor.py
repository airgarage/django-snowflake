import logging
import time

from django_snowflake.heartbeat import HEARTBEAT

logger = logging.getLogger(__name__)


class TimingCursorWrapper:
    def __init__(self, cursor, alias):
        self.cursor = cursor
        self.alias = alias

    def execute(self, sql, params=None):
        HEARTBEAT.record_query(self.alias)
        start = time.monotonic()
        try:
            if params is None:
                return self.cursor.execute(sql)
            return self.cursor.execute(sql, params)
        finally:
            elapsed = (time.monotonic() - start) * 1000
            logger.debug(
                f"[{self.alias}] query executed in {elapsed:.2f}ms: {sql[:200]}"
            )

    def executemany(self, sql, param_list):
        HEARTBEAT.record_query(self.alias)
        start = time.monotonic()
        try:
            return self.cursor.executemany(sql, param_list)
        finally:
            elapsed = (time.monotonic() - start) * 1000
            logger.debug(
                f"[{self.alias}] executemany ({len(param_list)} rows) in {elapsed:.2f}ms: {sql[:200]}"
            )

    def __getattr__(self, attr):
        return getattr(self.cursor, attr)

    def __iter__(self):
        return iter(self.cursor)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cursor.close()
