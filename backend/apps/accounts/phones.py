import re

_PHONE_PATTERNS = re.compile(r"^\+?(?:254|0)?7\d{8}$")


def normalize_phone(raw: str | None) -> str | None:
    """Normalize familiar Kenyan phone input to international E.164 form.

    Accepts 07XXXXXXXX, 7XXXXXXXX, 2547XXXXXXXX, +2547XXXXXXXX.
    Returns None for invalid input.
    """
    if raw is None:
        return None
    cleaned = re.sub(r"[\s\-\(\)]", "", raw or "")
    if not _PHONE_PATTERNS.match(cleaned):
        return None
    if cleaned.startswith("0"):
        cleaned = "+254" + cleaned[1:]
    elif cleaned.startswith("7"):
        cleaned = "+254" + cleaned
    elif cleaned.startswith("254"):
        cleaned = "+" + cleaned
    return cleaned