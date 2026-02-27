import os
import sys

import django
from django.core.exceptions import ImproperlyConfigured
from django.utils.version import get_version_tuple


def check_django_compatability():
    """
    Verify that this version of django-snowflake is compatible with the
    installed version of Django. For example, any django-snowflake 3.2.x is
    compatible with Django 3.2.y.
    """
    from . import __version__

    if django.VERSION[:2] != get_version_tuple(__version__)[:2]:
        raise ImproperlyConfigured(
            "You must use the latest version of django-snowflake {A}.{B}.x "
            "with Django {A}.{B}.y (found django-snowflake {C}).".format(
                A=django.VERSION[0],
                B=django.VERSION[1],
                C=__version__,
            )
        )


def is_running_tests() -> bool:
    """
    Detect if we're running in a test environment.
    Returns True if tests are being run, False otherwise.
    """
    if "test" in sys.argv:
        return True

    if "pytest" in sys.argv[0] or any("pytest" in arg for arg in sys.argv):
        return True

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True

    settings_module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if "test" in settings_module.lower():
        return True

    return False


migration_commands = {"migrate", "makemigrations", "sqlmigrate", "showmigrations"}


def is_running_migrations() -> bool:
    """
    Detect if Django is running migrations (manage.py migrate or makemigrations).
    Returns True if migrations are being run, False otherwise.
    """
    return bool(migration_commands.intersection(sys.argv))
