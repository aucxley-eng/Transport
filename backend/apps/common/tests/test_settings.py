"""Guards on the settings the test suite runs under.

These are not tests of behaviour; they are tests of the harness. Each one protects
a way the suite could do real damage to a machine it was not given.
"""

from django.conf import settings
from django.test import SimpleTestCase


class TestDatabaseIsAlwaysLocalTestCase(SimpleTestCase):
    """The suite must never create or drop a database on a configured server.

    `config.settings.test` inherits from development, which inherits the DB_ENGINE
    environment variable. A developer with production credentials in `.env` would
    otherwise run the suite against the hosted database, where Django creates
    `test_postgres` and leaves it behind if the run is interrupted. Pinning the
    engine in the test settings is what prevents that, and this is what notices if
    the pin is ever dropped.
    """

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
