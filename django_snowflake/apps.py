import logging

from django.apps import AppConfig

from django_snowflake.utils import is_running_migrations, is_running_tests

logger = logging.getLogger(__name__)


class DjangoSnowflakeConfig(AppConfig):
    name = "django_snowflake"
    verbose_name = "Django Snowflake"

    def ready(self):
        """
        Pool creation, warm-up, and the warehouse heartbeat all start lazily on
        the first Snowflake connection in each process (see
        ``base.DatabaseWrapper.get_new_connection`` and ``heartbeat.HEARTBEAT``).

        We deliberately do NOT start them here. Under Gunicorn ``preload_app``,
        ``ready()`` runs in the master before fork(); threads started there do not
        survive fork() and any connections opened there would share OS sockets
        with the workers. Lazy, per-process startup avoids initializing anything
        in the master and gives each worker that talks to Snowflake its own pool
        and heartbeat. Fork safety is enforced by ``pool._after_fork_in_child``.
        """
        if is_running_tests() or is_running_migrations():
            logger.debug(
                "Test/migration environment - Snowflake pool services disabled"
            )
            return

        logger.debug(
            "DjangoSnowflakeConfig.ready(): Snowflake pool services start lazily per worker"
        )
