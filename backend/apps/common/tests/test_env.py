"""Entrypoints must reach the same environment files.

``manage.py``, ``config.wsgi`` and ``config.asgi`` all delegate to
``config.env.load_env``. When only ``manage.py`` loaded ``.env`` a deployment
started through ``gunicorn config.wsgi`` silently ignored the file, so these
tests pin the contract down.

``config.wsgi`` and ``config.asgi`` are imported *inside* the tests, under a
patched ``load_dotenv``. Importing them at module level would execute a real
``load_env()`` against the developer's ``.env``, which puts live payment
credentials back into the environment the test settings deliberately cleared.
"""

import importlib
import inspect
import unittest
from unittest import mock

from config import env


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
        for name in ("config.wsgi", "config.asgi"):
            with self.subTest(module=name), mock.patch.object(env, "load_dotenv"):
                module = importlib.reload(importlib.import_module(name))
                lines = [
                    line.strip() for line in inspect.getsource(module).splitlines()
                ]
                self.assertIn(
                    "load_env()",
                    lines,
                    f"{name} must call load_env() like manage.py does",
                )
