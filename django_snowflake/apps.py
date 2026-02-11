import atexit
import logging
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)

_warmup_lock = threading.Lock()
_warmup_started = False
_warmup_thread = None


def _warm_connection_pools_background():
    """
    Warm up connection pools for Snowflake databases that have pooling enabled.
    This runs in a background thread to avoid blocking Django startup.
    """
    from django.conf import settings
    from django.db import connections

    from .pool import POOL_CONTAINER

    databases_to_warm = []
    try:
        for alias, db_config in settings.DATABASES.items():
            engine = db_config.get("ENGINE", "")
            if "django_snowflake" in engine:
                pool_config = db_config.get("POOL", {})
                if pool_config.get("IS_ENABLED", False):
                    databases_to_warm.append(
                        {
                            "alias": alias,
                            "pool_size": pool_config.get("POOL_SIZE", 5),
                        }
                    )
    except Exception as e:
        logger.error(f"Error reading database configuration for pool warming: {e}")
        return

    if not databases_to_warm:
        logger.debug("No Snowflake databases with pooling enabled found.")
        return

    for db_info in databases_to_warm:
        alias = db_info["alias"]
        pool_size = db_info["pool_size"]

        try:
            logger.info(
                f"Warming up connection pool for database '{alias}' with {pool_size} connections..."
            )

            connection_wrapper = connections[alias]
            conn_params = connection_wrapper.get_connection_params()

            if connection_wrapper.should_use_pool(conn_params):
                connection_wrapper.create_pool_if_not_exists(conn_params)

                POOL_CONTAINER.warm_pool(alias, pool_size)

                logger.info(
                    f"Successfully warmed up pool for '{alias}' with {pool_size} connections."
                )
            else:
                logger.debug(f"Pooling not enabled for '{alias}', skipping warm-up.")

        except Exception as e:
            logger.error(
                f"Failed to warm up connection pool for '{alias}': {e}",
                exc_info=True,
            )


def _start_pool_warming():
    """
    Start pool warming in a background thread.
    Uses a lock to ensure only one thread starts the warming process.
    """
    global _warmup_started, _warmup_thread

    with _warmup_lock:
        if _warmup_started:
            return
        _warmup_started = True

        logger.info(
            "Starting Snowflake connection pool warming in background thread..."
        )

        # Start warming in a daemon thread so it doesn't block shutdown
        _warmup_thread = threading.Thread(
            target=_warm_connection_pools_background,
            name="django_snowflake_pool_warmer",
            daemon=True,
        )
        _warmup_thread.start()


def _cleanup_warmup_thread():
    """Wait for warmup thread to complete on shutdown (with timeout)."""
    global _warmup_thread
    if _warmup_thread and _warmup_thread.is_alive():
        logger.debug("Waiting for pool warming thread to complete...")
        _warmup_thread.join(timeout=5.0)


atexit.register(_cleanup_warmup_thread)


class DjangoSnowflakeConfig(AppConfig):
    name = "django_snowflake"
    verbose_name = "Django Snowflake"

    def ready(self):
        """
        Called when Django starts up. This is where we warm up connection pools
        for any databases using the Snowflake backend that have pooling enabled.
        """
        logger.info("DjangoSnowflakeConfig.ready() called - initiating pool warming")
        _start_pool_warming()
