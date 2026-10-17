"""Run the suite against real PostgreSQL.

SQLite silently ignores ``select_for_update()`` and skips CheckConstraints, so
the SQLite run cannot prove the row-locking invariants ADR0001 depends on --
seat-hold expiry, first-accept-wins assignment, the outbox drain. This module is
the only way to run the same suite where those guarantees are real.

    .venv/bin/python manage.py test --settings=config.settings.test_postgres

Never the default: it creates and drops a database on a real server, so it has
to be asked for by name. ``manage.py test`` still uses config.settings.test,
which is pinned to SQLite.

Requires a project with pooling or a network able to reach the server; the
suite is refused outright if the host looks unconfigured, so a placeholder
cannot turn into a confusing DNS error halfway through.
"""

from config.settings.test import *  # noqa: F401,F403

TESTING_ON_POSTGRESQL = True

HOST = os.environ.get("DB_HOST", "").strip()  # noqa: F405
if not HOST or HOST.startswith("REPLACE-"):
    raise ImproperlyConfigured(  # noqa: F405
        "config.settings.test_postgres needs a real DB_HOST in .env. "
        "Run with config.settings.test for the local SQLite suite."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "postgres"),  # noqa: F405
        "USER": os.environ.get("DB_USER", ""),  # noqa: F405
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),  # noqa: F405
        "HOST": HOST,
        "PORT": os.environ.get("DB_PORT", "5432"),  # noqa: F405
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),  # noqa: F405
            "sslmode": os.environ.get("DB_SSLMODE", "require"),  # noqa: F405
        },
        # Named explicitly so a crashed run leaves something identifiable.
        "TEST": {"NAME": "test_supabase"},
    }
}