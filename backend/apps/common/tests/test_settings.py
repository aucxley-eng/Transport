"""Guards on the settings the test suite runs under.

These are not tests of behaviour; they are tests of the harness. Each one protects
a way the suite could do real damage to a machine it was not given.
"""

import os

from django.conf import settings
from django.test import SimpleTestCase
from unittest.case import SkipTest


class TestDatabaseIsAlwaysLocalTestCase(SimpleTestCase):
    """The suite must never create or drop a database on a configured server.

    `config.settings.test` inherits from development, which inherits the DB_ENGINE
    environment variable. A developer with production credentials in `.env` would
    otherwise run the suite against the hosted database, where Django creates
    `test_postgres` and leaves it behind if the run is interrupted. Pinning the
    engine in the test settings is what prevents that, and this is what notices if
    the pin is ever dropped.

    The assertions describe `config.settings.test`. They are skipped under
    `config.settings.test_postgres`, which is the one opt-in way to run the suite
    on a server -- without the skip, deliberately running on PostgreSQL would read
    as a failure and invite removing the pin that makes the default run safe.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if getattr(settings, "TESTING_ON_POSTGRESQL", False):
            raise SkipTest(
                "SQLite-only guard; the run is opted into PostgreSQL explicitly."
            )

    def test_the_suite_runs_on_sqlite_regardless_of_the_environment(self):
        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3"
        )

    def test_no_server_is_borrowed_from_the_environment(self):
        options = settings.DATABASES["default"]
        for key in ("HOST", "PORT", "USER", "PASSWORD"):
            self.assertEqual(options[key], "", f"{key} leaked from the environment")
        self.assertEqual(options["CONN_MAX_AGE"], 0)

    def test_the_suite_uses_an_in_memory_database(self):
        # Django overrides NAME for SQLite tests, so nothing is written to disk and
        # no stray test-db.sqlite3 is left behind by an interrupted run.
        self.assertIn("mode=memory", settings.DATABASES["default"]["NAME"])


class TestProviderNeverLeavesTheStubTestCase(SimpleTestCase):
    """The suite must not be able to select the live M-PESA adapter.

    `manage.py` loads `.env` before the settings module is imported, so a
    developer who has pasted real Daraja credentials would otherwise have every
    test that initiates a payment place a real STK push against a real
    subscriber, and every test that assumes the local stub fail. Clearing the
    credentials in the test settings is what prevents it; this notices if the
    clearing is ever dropped.
    """

    def test_no_live_mpesa_credentials_reach_the_suite(self):
        leaked = [
            name
            for name in (
                "MPESA_ENVIRONMENT",
                "MPESA_CONSUMER_KEY",
                "MPESA_CONSUMER_SECRET",
                "MPESA_SHORTCODE",
                "MPESA_PASSKEY",
                "MPESA_CALLBACK_URL",
            )
            if os.environ.get(name)
        ]
        self.assertEqual(
            leaked, [], f"the suite is running with live payment credentials: {leaked}"
        )

    def test_the_selected_adapter_is_the_local_stub(self):
        from apps.payments.provider import (
            MpesaProvider,
            daraja_credentials,
            get_provider,
        )

        self.assertIsNone(daraja_credentials())
        provider = get_provider("mpesa")
        self.assertIsInstance(provider, MpesaProvider)


class PostgresOptInGuardTestCase(SimpleTestCase):
    """`config.settings.test_postgres` must refuse an unconfigured host.

    The guard raises before Django builds a connection, so this exercises the
    real file rather than a copy of its logic: a guard written without its
    exception imported raises NameError on load and reads as a crash in the
    settings package instead of a sentence telling you what to do.
    """

    def test_an_unconfigured_host_is_refused_by_name(self):
        from pathlib import Path
        from unittest import mock

        import os

        from django.core.exceptions import ImproperlyConfigured

        from config.settings import test as test_settings

        path = Path(test_settings.__file__).with_name("test_postgres.py")
        source = path.read_text(encoding="utf-8")
        namespace = {"__file__": str(path), "__name__": "config.settings.test_postgres"}

        with mock.patch.dict(os.environ, {"DB_HOST": ""}):
            with self.assertRaises(ImproperlyConfigured) as raised:
                exec(compile(source, str(path), "exec"), namespace)  # noqa: S102

        message = str(raised.exception)
        self.assertIn("DB_HOST", message)
        self.assertIn("config.settings.test_postgres", message)

    def test_a_placeholder_host_is_refused_too(self):
        from pathlib import Path
        from unittest import mock

        import os

        from django.core.exceptions import ImproperlyConfigured

        from config.settings import test as test_settings

        path = Path(test_settings.__file__).with_name("test_postgres.py")
        source = path.read_text(encoding="utf-8")
        namespace = {"__file__": str(path), "__name__": "config.settings.test_postgres"}

        with mock.patch.dict(os.environ, {"DB_HOST": "REPLACE-WITH-YOUR-HOST"}):
            with self.assertRaises(ImproperlyConfigured):
                exec(compile(source, str(path), "exec"), namespace)  # noqa: S102
