from typing import Optional, List
import atexit
import logging
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class DjangoSnowflakeConfig(AppConfig):
    name = "django_snowflake"
    verbose_name = "Django Snowflake"

    _warmup_thread: Optional[threading.Thread] = None
    _warmup_lock: threading.Lock = threading.Lock()
    _has_warmup_started: bool = False
    _stop_event = threading.Event()

    def _get_databases(self) -> List[dict[str, str | int]]:
        """
        Get a list of databases that have their engine set to "django_snowflake" and pooling is enabled.
        """
        from django.conf import settings

        databases = []
        for alias, db_config in settings.DATABASES.items():
            if "django_snowflake" not in db_config.get("ENGINE", ""):
                continue

            pool_config = db_config.get("POOL", {})
            if pool_config.get("IS_ENABLED", False):
                databases.append(
                    {
                        "alias": alias,
                        "pool_size": pool_config.get("POOL_SIZE", 5),
                    }
                )

        return databases

    def _warm_connection_pools(self):
        """
        Warm up connection pools for Snowflake databases that have pooling enabled.
        This runs in a background thread to avoid blocking Django startup.
        """
        from django.db import connections
        from .pool import POOL_CONTAINER

        databases_to_warm = []
        try:
            databases_to_warm = self._get_databases()
        except Exception as e:
            logger.error(f"Error reading database configuration for pool warming: {e}")
            return

        if not databases_to_warm:
            logger.debug("No Snowflake databases with pooling enabled found.")
            return

        for db_info in databases_to_warm:
            alias = str(db_info["alias"])
            pool_size = int(db_info["pool_size"])

            try:
                logger.info(
                    f"Warming up connection pool for database '{alias}' with {pool_size} connections."
                )

                connection_wrapper = connections[alias]
                conn_params = connection_wrapper.get_connection_params()

                if not connection_wrapper.should_use_pool(conn_params):
                    logger.info(f"Pooling not enabled for '{alias}', skipping warm-up.")
                    return

                connection_wrapper.create_pool_if_not_exists(conn_params)
                POOL_CONTAINER.warm_pool(alias, pool_size, exit_event=self._stop_event)

                logger.info(
                    f"Successfully warmed up pool for '{alias}' with {pool_size} connections."
                )
            except Exception as e:
                logger.error(
                    f"Failed to warm up connection pool for '{alias}': {e}",
                    exc_info=True,
                )

    def _cleanup_warmup_thread(self):
        """
        Wait for warmup thread to complete on shutdown (with timeout).
        """
        if self._warmup_thread and self._warmup_thread.is_alive():
            logger.debug("Waiting for pool warming thread to complete...")
            self._stop_event.set()
            self._warmup_thread.join(timeout=5.0)

    def ready(self):
        """
        Called when Django starts up. This is where we warm up connection pools
        for any databases using the Snowflake backend that have pooling enabled.
        """
        logger.debug("DjangoSnowflakeConfig.ready() called - initiating pool warming")

        with self._warmup_lock:
            if self._has_warmup_started:
                logger.debug("Pool warming already initiated. Returning.")
                return
            self._has_warmup_started = True

            logger.debug(
                "Starting Snowflake connection pool warming in background thread..."
            )

            self._warmup_thread = threading.Thread(
                target=self._warm_connection_pools,
                name="django_snowflake_pool_warmer",
                daemon=True,
            )
            self._warmup_thread.start()
            atexit.register(self._cleanup_warmup_thread)
