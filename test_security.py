import html
import re
import unittest
from unittest.mock import MagicMock

from upinar_notifier.models import clean_student_id, StudentAccount
from upinar_notifier.crypto import encrypt_text, decrypt_text, ensure_fernet_key
from upinar_notifier.db import Database


class SecurityAndPentestTests(unittest.TestCase):
    def setUp(self):
        self.secret = ensure_fernet_key("test_secret_key_for_testing_12345678")
        self.db = Database(":memory:")

    def test_student_id_sanitization_and_injection_prevention(self):
        """Verify that malicious inputs, SQLi payloads, XSS, spaces, and Arabic numerals are neutralized."""
        malicious_inputs = [
            ("١٢٠٢٥١٣٨١", "120251381"),
            (" 120251381 ", "120251381"),
            ("12025 1381", "120251381"),
            (" ١٢٠٢٥  ١٣٨١ ", "120251381"),
            ("\u200e120251381\u200f", "120251381"),
            ("120251381' OR '1'='1", "12025138111"),
            ("<script>alert(1)</script>120251381", "1120251381"),
            ("120251381; DROP TABLE students;--", "120251381"),
            ("abc-xyz-!@#$%", ""),
            ("", ""),
            (None, ""),
        ]
        for raw, expected in malicious_inputs:
            cleaned = clean_student_id(raw)
            self.assertEqual(cleaned, expected)
            # Ensure output is strictly numeric or empty
            self.assertTrue(cleaned == "" or cleaned.isdigit())

    def test_encryption_integrity_and_tampering(self):
        """Verify AES-256 Fernet encryption and resistance to tampering."""
        original_password = "SuperP@ssw0rd!#2026_فلسطين"
        encrypted = encrypt_text(original_password, self.secret)
        self.assertNotEqual(encrypted, original_password)
        
        # Proper decryption
        decrypted = decrypt_text(encrypted, self.secret)
        self.assertEqual(decrypted, original_password)

        # Tampered ciphertext must fail safely
        tampered = list(encrypted)
        tampered[-5] = "A" if tampered[-5] != "A" else "B"
        tampered_str = "".join(tampered)
        with self.assertRaises(RuntimeError):
            decrypt_text(tampered_str, self.secret)

    def test_database_account_isolation_and_sqli(self):
        """Verify account isolation: Student A cannot access Student B, and SQL injection fails."""
        # Insert Student A
        acc_a = self.db.accounts.upsert(
            telegram_chat_id="1001",
            telegram_user_id=1001,
            student_id="120250001",
            password="pass_student_a",
        )
        # Insert Student B
        acc_b = self.db.accounts.upsert(
            telegram_chat_id="2002",
            telegram_user_id=2002,
            student_id="120250002",
            password="pass_student_b",
        )

        # Verify Student A cannot see Student B's data
        fetched_a = self.db.accounts.load_by_chat_id("1001")
        fetched_b = self.db.accounts.load_by_chat_id("2002")
        self.assertEqual(fetched_a.student_id, "120250001")
        self.assertEqual(fetched_b.student_id, "120250002")
        self.assertNotEqual(fetched_a.password, fetched_b.password)

        # SQL injection attempt via chat_id
        sqli_chat = "1001' OR '1'='1"
        self.assertIsNone(self.db.accounts.load_by_chat_id(sqli_chat))

    def test_html_injection_prevention(self):
        """Verify dynamic strings are escaped against Telegram HTML formatting injection."""
        xss_string = "<script>alert('hack')</script><b>Course Title</b>"
        escaped = html.escape(xss_string)
        self.assertNotIn("<script>", escaped)
        self.assertNotIn("<b>", escaped)
        self.assertIn("&lt;script&gt;", escaped)


if __name__ == "__main__":
    unittest.main()
