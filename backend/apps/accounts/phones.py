import re

# Kenyan mobile numbering: national (significant) form is 0X YYYYYYYY with
# X in {1, 7} — 07x/01x Safaricom, 01x/07x Airtel, 01x/013x/017x Telkom.
# International E.164 is +254 immediately followed by that same 1 or 7;
# the national 0 is dropped and never appears after the country code.
#   National:  0 (1|7) 8 more digits        → 07XXXXXXXX / 01XXXXXXXX
#   Legacy:    (1|7) 8 more digits          → 7XXXXXXXX / 1XXXXXXXX (9-digit)
#   Intl:      +254 (1|7) 8 more digits     → +2547XXXXXXXX / +2541XXXXXXXX
# Accepts 07.../01..., legacy 9-digit 7.../1..., 2547.../2541..., and
# +2547.../+2541.... Very deliberately rejects 13-digit +2540.../2540...
# variants (redundant national 0) and non-mobile ranges like 02x/08x.
_PHONE_PATTERNS = re.compile(r"^\+?(?:254|0)?[17]\d{8}$")


def normalize_phone(raw: str | None) -> str | None:
    """Normalize familiar Kenyan phone input to international E.164 form.

    Accepts 07XXXXXXXX/01XXXXXXXX (national), legacy 9-digit 7XXXXXXXX/
    1XXXXXXXX, 2547XXXXXXXX/2541XXXXXXXX, and +2547XXXXXXXX/+2541XXXXXXXX.
    Returns None for invalid input (including 02x, 08x, or any form that
    keeps a redundant national 0 after the country code).
    """
    if raw is None:
        return None
    cleaned = re.sub(r"[\s\-\(\)]", "", raw or "")
    if not _PHONE_PATTERNS.match(cleaned):
        return None
    if cleaned.startswith("0"):
        cleaned = "+254" + cleaned[1:]
    elif cleaned.startswith("254"):
        cleaned = "+" + cleaned
    elif cleaned[0] in "17":
        cleaned = "+254" + cleaned
    return cleaned