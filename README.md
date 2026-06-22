# Snowflake backend for Django

## AirGarage Specific Notes

### Testing

To test Django Snowflake locally, update `dev_requirements.txt` in the Django project you want to test in.
You need to tell pip to autoload the package so it picks up any new changes, you can do this by replacing `django-snowflake` in that `dev_requirements.txt` file to:

**Replace all of this**
```python
django-snowflake @ git+https://github.com/airgarage/django-snowflake.git@[hash]
```


**With this**
```python
-e [path_to_django_snowflake_project] # For me (Dylan) it's under /Users/dylan/projects/django-snowflake
```

## Install and usage

Use the version of django-snowflake that corresponds to your version of
Django. For example, to get the latest compatible release for Django 6.0.x:

`pip install django-snowflake==6.0.*`

The minor release number of Django doesn't correspond to the minor release
number of django-snowflake. Use the latest minor release of each.

Configure the Django `DATABASES` setting similar to this:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django_snowflake',
        'NAME': 'MY_DATABASE',
        'SCHEMA': 'MY_SCHEMA',
        'WAREHOUSE': 'MY_WAREHOUSE',
        'USER': 'my_user',
        'PASSWORD': 'my_password',
        'ACCOUNT': 'my_account',
        'POOL': {
            'IS_ENABLED': True,
            'MAX_OVERFLOW': 10,
            'POOL_SIZE': 5,
            'PRE_PING': True
        },
        # Include 'OPTIONS' if you need to specify any other
        # snowflake.connector.connect() parameters, documented at:
        # https://docs.snowflake.com/en/user-guide/python-connector-api.html#connect
        'OPTIONS': {
            # Examples:
            'role': 'MY_ROLE',
            # To use native Okta authenticators:
            # https://docs.snowflake.com/en/user-guide/admin-security-fed-auth-use#native-sso-okta-only
            'authenticator': 'https://example.okta.com',
            # To use private key authentication:
            'private_key_file': '<path>/rsa_key.p8',
            'private_key_file_pwd': 'my_passphrase',
        },
    },
}
```

Supported `POOL` keys:

- `IS_ENABLED` (default `False`): turn pooling on for this database.
- `POOL_SIZE` (default `5`): number of persistent connections kept in the pool.
- `MAX_OVERFLOW` (default `10`): extra connections allowed beyond `POOL_SIZE` under load.
- `POOL_RECYCLE` (default `3600`): recycle connections older than this many seconds
  to avoid age-based staleness.
- `PRE_PING` (default `False`): when `True`, run a lightweight `SELECT 1` on each
  checkout of a non-fresh connection and transparently reconnect if it has died.
  This costs one extra round-trip per checkout, so leave it off if `POOL_RECYCLE`
  already covers your staleness needs.

## Warehouse heartbeat

Snowflake warehouses suspend after `AUTO_SUSPEND` seconds of inactivity, and the
next query pays a resume penalty (often several hundred ms). Connection pooling
does not prevent this: `AUTO_SUSPEND` governs compute, not connections, so pooled
connections survive a suspend while the warehouse itself goes cold.

When pooling is enabled, this backend runs a traffic-aware heartbeat that issues a
lightweight `SELECT 1` on an interval to keep the warehouse warm during active
windows, and stops once traffic has been idle long enough that letting the
warehouse suspend is cheaper. It runs as a daemon thread per worker process,
started lazily on the first Snowflake connection (so it never starts in a Gunicorn
`preload_app` master) and is reset across `fork()` so each worker runs its own.

Configure it via Django settings or environment variables (settings take
precedence):

- `SNOWFLAKE_HEARTBEAT_ENABLED` (default `True`): turn the heartbeat on/off.
- `SNOWFLAKE_HEARTBEAT_INTERVAL` (default `45`): seconds between pings. Must be
  less than the warehouse `AUTO_SUSPEND`.
- `SNOWFLAKE_HEARTBEAT_IDLE_THRESHOLD` (default `900`): seconds with no real query
  after which the heartbeat stops and the warehouse is allowed to suspend. A new
  real query resumes the heartbeat automatically.

The heartbeat query is tagged with the comment `django_snowflake:heartbeat`. To
keep these `SELECT 1` spans out of Datadog APM, add a `ddtrace` trace filter in
your app that drops spans whose resource contains that tag.

## Persistent connections

To use persisent connections, set Django's [`CONN_MAX_AGE`](https://docs.djangoproject.com/en/stable/ref/databases/#persistent-connections)
and Snowflake Python Connector's [`client_session_keep_alive`](https://docs.snowflake.com/en/sql-reference/parameters#client-session-keep-alive):

```python
DATABASES = {
    'default': {
        # ...
        'CONN_MAX_AGE': None,
        'OPTIONS': {
            'client_session_keep_alive': True,
        },
    },
}
```

## Notes on Django fields

- Consistent with [Snowflake's convention](https://docs.snowflake.com/en/sql-reference/identifiers-syntax.html),
  this backend uppercases all database identifiers (table names, column names,
  etc.) unless they are quoted, e.g. `db_table='"table_name"'`.

- Snowflake supports defining foreign key and unique constraints, however, it
  doesn't enforce them. Thus, Django manages these constraints and `inspectdb`
  detects them, but Django won't raise `IntegrityError` if they're violated.

- Snowflake doesn't support indexes. Thus, Django ignores any indexes defined
  on models or fields.

- Snowflake doesn't support check constraints, so the various
  `PositiveIntegerField` model fields allow negative values (though validation
  at the form level still works).

## Notes on Django QuerySets

* Snowflake has
  [limited support for subqueries](https://docs.snowflake.com/en/user-guide/querying-subqueries.html#types-supported-by-snowflake).

* Valid values for `QuerySet.explain()`'s `format` parameter are `'json'`,
  `'tabular'`, and `'text'`. The default is `'tabular'`.

## Known issues and limitations

This list isn't exhaustive. If you run into a problem, consult
`django_snowflake/features.py` to see if a similar test is skipped. Please
[create an issue on GitHub](https://github.com/Snowflake-Labs/django-snowflake/issues/new)
if you encounter an issue worth documenting.

* Snowflake doesn't support `last_insert_id` to retrieve the ID of a newly
  created object. Instead, this backend issues the query
  `SELECT MAX(pk_name) FROM table_name` to retrieve the ID. This is subject
  to race conditions if objects are created concurrently. This makes this
  backend inappropriate for use in web app use cases where multiple clients
  could be creating objects at the same time. Further, you should not manually
  specify an ID (e.g. `MyModel(id=1)`) when creating an object.

* Due to snowflake-connector-python's [lack of VARIANT support](https://github.com/snowflakedb/snowflake-connector-python/issues/244),
  some `JSONField` queries with complex JSON parameters [don't work](https://github.com/Snowflake-Labs/django-snowflake/issues/58).

  For example, if `value` is a `JSONField`, this won't work:
  ```python
  >>> JSONModel.objects.filter(value__k={"l": "m"})
  ```
  A workaround is:
  ```python
  >>> from django.db.models.expressions import RawSQL
  >>> JSONModel.objects.filter(value__k=RawSQL("PARSE_JSON(%s)", ('{"l": "m"}',)))
  ```
  In addition, ``QuerySet.bulk_update()`` isn't supported for `JSONField`.

* Interval math where the interval is a column
  [is not supported](https://github.com/Snowflake-Labs/django-snowflake/issues/27).

* Interval math with a null interval
  [crashes](https://github.com/Snowflake-Labs/django-snowflake/issues/26).

## Troubleshooting

### Debug logging

To troubleshoot issues with connectivity to Snowflake, you can enable
[Snowflake Connector for Python's logging](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-example#logging)
using [Django's `LOGGING` setting](https://docs.djangoproject.com/en/stable/topics/logging/).

This is a minimal addition to Django's default `"loggers"` configuration that
enables the connector's `DEBUG` logging:

```python
LOGGING = {
    …
    "loggers": {
        …
        "django_snowflake": {
            "level": "DEBUG",
            "handlers": ["console"],
        },
    },
}
```
