import unittest
from unittest.mock import patch

from backend.configure_mail import read_password


class MailConfigTests(unittest.TestCase):
    def test_password_is_read_without_changing_it(self):
        with patch("backend.configure_mail.getpass", return_value="example-only-password"):
            self.assertEqual(read_password(), "example-only-password")

    def test_invalid_password_is_not_echoed(self):
        for value in ("", "случайный текст", "password\ntext"):
            with self.subTest(value_type="empty" if not value else "invalid"):
                with patch("backend.configure_mail.getpass", return_value=value):
                    with self.assertRaises(SystemExit) as error:
                        read_password()
                if value:
                    self.assertNotIn(value, str(error.exception))
