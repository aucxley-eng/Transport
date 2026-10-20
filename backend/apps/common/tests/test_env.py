"""Entrypoints must reach the same environment files.

``manage.py``, ``config.wsgi`` and ``config.asgi`` all delegate to
``config.env.load_env``. When only ``manage.py`` loaded ``.env`` a deployment
started through ``gunicorn config.wsgi`` silently ignored the file, so these
tests pin the contract down.
"""

import inspect
import unittest
from unittest import mock

from config import asgi, env, wsgi


class LoadEnvTestCase(unittest.TestCase):
    def test_reads_root_then_backend_so_backend_wins(self):
        with mock.patch.object(env, "load_dotenv") as load:
            env.load_env()

        paths = [call.args[0] for call in load.call_args_list]
        self.assertEqual(
            paths,
            [env.BACKEND_DIR.parent / ".env", env.BACKEND_DIR / ".env"],
        )

    def test_files_never_override_the_process_environment(self):
        with mock.patch.object(env, "load_dotenv") as load:
            env.load_env()

        for call in load.call_args_list:
            self.assertFalse(
                call.kwargs.get("override", False),
                "override=True would let a stale .env clobber the real environment",
            )

    def test_wsgi_and_asgi_load_the_env(self):
        for module in (wsgi, asgi):
            lines = [line.strip() for line in inspect.getsource(module).splitlines()]
            self.assertIn(
                "load_env()",
                lines,
                f"{module.__name__} must call load_env() like manage.py does",
            )


if __name__ == "__main__":
    unittest.main()
