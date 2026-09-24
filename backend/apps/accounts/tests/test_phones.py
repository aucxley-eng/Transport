"""Phone normalization tests (blueprint 8, identity).

Aligns with the Kenyan mobile model: national 0X YYYYYYYY with X in {1, 7},
and international +254 immediately followed by that same 1 or 7 — never a
redundant national 0 after the country code.
"""

from django.test import SimpleTestCase

from apps.accounts.phones import normalize_phone


class NormalizePhoneTestCase(SimpleTestCase):
    def test_safaricom_07(self):
        self.assertEqual(normalize_phone("0712345678"), "+254712345678")

    def test_airtel_and_telkom_01(self):
        # 01x Airtel, 013x/017x Telkom.
        self.assertEqual(normalize_phone("0199000000"), "+254199000000")  # Telkom 01
        self.assertEqual(normalize_phone("0176000001"), "+254176000001")  # Telkom 017
        self.assertEqual(normalize_phone("0102000002"), "+254102000002")  # Airtel 01

    def test_legacy_9_digit_7(self):
        self.assertEqual(normalize_phone("796606363"), "+254796606363")

    def test_international_254(self):
        self.assertEqual(normalize_phone("254796606363"), "+254796606363")
        self.assertEqual(normalize_phone("+254196606363"), "+254196606363")

    def test_separators_stripped(self):
        self.assertEqual(normalize_phone("0712 345-678"), "+254712345678")

    def test_invalid_forms_rejected(self):
        # 02x is not a Kenyan mobile 0X range (X must be 1 or 7).
        self.assertIsNone(normalize_phone("0212345678"))
        # 13-digit +2540... keeps a redundant national 0 after the code.
        self.assertIsNone(normalize_phone("+2540102345678"))
        self.assertIsNone(normalize_phone("2540102345678"))
        self.assertIsNone(normalize_phone("07123456"))  # too short
        self.assertIsNone(normalize_phone("0812345678"))  # 08 not a range
        self.assertIsNone(normalize_phone(""))
        self.assertIsNone(normalize_phone(None))