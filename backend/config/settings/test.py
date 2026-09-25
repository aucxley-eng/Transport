"""Test settings.

Identical to development except that password hashing is cheap. The suite
creates organizations, staff, drivers and senders in every test, and PBKDF2
dominated the runtime (roughly four seconds per test). Hashing is not what these
tests exercise, so the test run uses a fast hasher.
"""

from config.settings.development import *  # noqa: F401,F403

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
