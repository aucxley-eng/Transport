"""Test settings.

Identical to development except that password hashing is cheap. The suite
creates organizations, staff, drivers and senders in every test, and PBKDF2
dominated the runtime (roughly four seconds per test). Hashing is not what these
tests exercise, so the test run uses a fast hasher.

The database is pinned to SQLite on purpose. Everything else is inherited from
development, including the DB_ENGINE environment variable, so without this a
developer with production credentials in ``.env`` would run the suite against the
hosted database: Django would create ``test_postgres`` on it, and a run
interrupted before teardown would leave that database behind. A test run has no
business creating databases on a machine it was not given.
"""

from config.settings.development import *  # noqa: F401,F403

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # Django replaces NAME with an in-memory database for SQLite tests, so
        # this only has to name something SQLite can open.
        "NAME": BASE_DIR / "test-db.sqlite3",  # noqa: F405
    }
}
