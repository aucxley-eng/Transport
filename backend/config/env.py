"""Environment-file loading shared by every entrypoint.

``manage.py``, ``config.wsgi`` and ``config.asgi`` all call :func:`load_env`, so
the same value reaches the process whichever way it was started. The root
``.env`` is read first and ``backend/.env`` second, so a machine-local override
wins; ``override`` stays false, so a real process environment always wins over
both files. This mirrors the order documented in ``manage.py`` and the README.
"""

from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent


def load_env():
    """Load the repository root and backend ``.env`` files, in that order."""
    load_dotenv(BACKEND_DIR.parent / ".env")
    load_dotenv(BACKEND_DIR / ".env")
