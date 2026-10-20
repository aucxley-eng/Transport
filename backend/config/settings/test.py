"""Test settings.

Pin the database to SQLite, hash passwords cheaply, and never let a
developer's live M-PESA credentials reach the suite. The suite creates
organizations, staff, drivers and senders in every test, and PBKDF2 dominated
the runtime (roughly four seconds per test). Hashing is not what these tests
exercise, so the test run uses a fast hasher.

The database is pinned to SQLite on purpose. Everything else is inherited from
development, including the DB_ENGINE environment variable, so without this a
developer with production credentials in ``.env`` would run the suite against the
hosted database: Django would create ``test_postgres`` on it, and a run
interrupted before teardown would leave that database behind. A test run has no
business creating databases on a machine it was not given.

The M-PESA environment is cleared for the same reason. ``manage.py`` loads
``.env`` before the settings are imported, so ``daraja_credentials()`` would
find a complete set and ``get_provider("mpesa")`` would select the live Daraja
adapter in place of the stub. Tests that assume the stub would then fail, and
worse, any test that initiates a payment would place a real request against a
real subscriber. The credentials tests put back deliberately; nothing else
needs them.
"""

import os

from config.settings.development import *  # noqa: F401,F403

# See the module docstring: the suite must stay on the local stub.
for _mpesa_name in (
    "MPESA_ENVIRONMENT",
    "MPESA_CONSUMER_KEY",
    "MPESA_CONSUMER_SECRET",
    "MPESA_SHORTCODE",
    "MPESA_PASSKEY",
    "MPESA_CALLBACK_URL",
):
    os.environ.pop(_mpesa_name, None)

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
