import unittest
from unittest.mock import MagicMock, patch, ANY
import urllib.parse
import urllib.request
import json
import sys
import os

# Import functions/classes from cmping module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import cmping


class TestCmping(unittest.TestCase):
    def test_is_ip_address(self):
        self.assertTrue(cmping.is_ip_address("127.0.0.1"))
        self.assertTrue(cmping.is_ip_address("8.8.8.8"))
        self.assertTrue(cmping.is_ip_address("2001:db8::1"))
        self.assertFalse(cmping.is_ip_address("chatmail.uk"))
        self.assertFalse(cmping.is_ip_address("example.com"))
        self.assertFalse(cmping.is_ip_address("https://google.com"))

    def test_generate_credentials(self):
        username, password = cmping.generate_credentials()
        self.assertEqual(len(username), 12)
        self.assertEqual(len(password), 20)
        self.assertTrue(all(c in (cmping.string.ascii_lowercase + cmping.string.digits) for c in username))
        self.assertTrue(all(c in (cmping.string.ascii_lowercase + cmping.string.digits) for c in password))

    def test_create_qr_url(self):
        # 1. Test HTTPS URL
        url = "https://relay.com/new"
        self.assertEqual(cmping.create_qr_url(url), "dcaccount:https://relay.com/new")

        # 2. Test Domain
        domain = "chatmail.uk"
        self.assertEqual(cmping.create_qr_url(domain), "dcaccount:chatmail.uk")

        # 3. Test IP Address
        ip = "127.0.0.1"
        qr = cmping.create_qr_url(ip)
        self.assertTrue(qr.startswith("dclogin:"))
        self.assertIn("@127.0.0.1/?", qr)
        self.assertIn("p=", qr)
        self.assertIn("v=1&ip=993&sp=465&ic=3&ss=default", qr)

    def test_format_duration(self):
        self.assertEqual(cmping.format_duration(1.5), "1.50s")
        self.assertEqual(cmping.format_duration(2.0), "2.00s")
        self.assertEqual(cmping.format_duration(0.5), "500.00ms")
        self.assertEqual(cmping.format_duration(0.001), "1.00ms")

    @patch("urllib.request.urlopen")
    def test_try_https_endpoint_success_post(self, mock_urlopen):
        # Mock successful POST response returning JSON
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"email": "test@domain.com", "password": "pass"}'
        mock_urlopen.return_value = mock_response

        email, password = cmping.try_https_endpoint("domain.com")
        self.assertEqual(email, "test@domain.com")
        self.assertEqual(password, "pass")

        # Verify urllib.request.urlopen was called
        mock_urlopen.assert_called()
        # Verify it tried POST first
        first_call_args = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(first_call_args.method, "POST")

    @patch("urllib.request.urlopen")
    def test_try_https_endpoint_success_get_fallback(self, mock_urlopen):
        # Mock POST request raising exception, but GET succeeding
        mock_response_get = MagicMock()
        mock_response_get.getcode.return_value = 200
        mock_response_get.read.return_value = b'{"email": "get_test@domain.com", "password": "get_pass"}'

        # Side effect: first call (POST) raises HTTPError, second (GET) returns response
        mock_urlopen.side_effect = [Exception("POST Failed"), mock_response_get]

        email, password = cmping.try_https_endpoint("domain.com")
        self.assertEqual(email, "get_test@domain.com")
        self.assertEqual(password, "get_pass")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_try_https_endpoint_full_url(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"email": "url@domain.com", "password": "pass"}'
        mock_urlopen.return_value = mock_response

        email, password = cmping.try_https_endpoint("https://custom.com/new_email?token=123")
        self.assertEqual(email, "url@domain.com")
        self.assertEqual(password, "pass")
        
        # Verify it requested the full URL directly
        called_req = mock_urlopen.call_args[0][0]
        self.assertEqual(called_req.full_url, "https://custom.com/new_email?token=123")

    @patch("urllib.request.urlopen")
    def test_try_https_endpoint_failure(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection Failed")
        result = cmping.try_https_endpoint("domain.com")
        self.assertIsNone(result)

    def test_get_relay_account_reuse(self):
        # Mock DeltaChat instance
        mock_dc = MagicMock()
        
        # Mock existing account
        mock_account = MagicMock()
        mock_account.get_config.side_effect = lambda key: "test@domain.com" if key in ("configured_addr", "addr") else None
        mock_dc.get_all_accounts.return_value = [mock_account]

        maker = cmping.AccountMaker(mock_dc, verbose=0)
        account = maker.get_relay_account("domain.com")

        # Verify it reused the existing account and started IO
        self.assertEqual(account, mock_account)
        mock_account.start_io.assert_called_once()
        self.assertIn(mock_account, maker.online)

    @patch("cmping.try_https_endpoint")
    def test_get_relay_account_new_qr_success(self, mock_try_https):
        mock_dc = MagicMock()
        mock_dc.get_all_accounts.return_value = []
        
        mock_new_account = MagicMock()
        mock_dc.add_account.return_value = mock_new_account
        
        maker = cmping.AccountMaker(mock_dc, verbose=0)
        account = maker.get_relay_account("domain.com")

        # Verify it created a new account and configured it from QR
        self.assertEqual(account, mock_new_account)
        mock_new_account.set_config_from_qr.assert_called_with("dcaccount:domain.com")
        mock_new_account.start_io.assert_called_once()
        mock_try_https.assert_not_called()

    @patch("cmping.try_https_endpoint")
    def test_get_relay_account_fallback_https_success(self, mock_try_https):
        mock_dc = MagicMock()
        mock_dc.get_all_accounts.return_value = []
        
        mock_bad_account = MagicMock()
        # QR config fails
        mock_bad_account.set_config_from_qr.side_effect = [Exception("QR Failed"), None]
        
        # After remove() we create a clean account
        mock_clean_account = MagicMock()
        mock_dc.add_account.side_effect = [mock_bad_account, mock_clean_account]

        # HTTPS endpoint returns credentials
        mock_try_https.return_value = ("fallback@domain.com", "fallback_password")

        maker = cmping.AccountMaker(mock_dc, verbose=0)
        account = maker.get_relay_account("domain.com")

        # Verify bad account was removed
        mock_bad_account.remove.assert_called_once()
        # Verify clean account was configured with dclogin
        self.assertEqual(account, mock_clean_account)
        mock_try_https.assert_called_with("domain.com", 0)
        
        # Verify correct dclogin URL structure with ih and sh
        expected_dclogin = (
            "dclogin:fallback@domain.com/?"
            "p=fallback_password&v=1&ih=domain.com&sh=domain.com&ip=993&sp=465&ic=3&ss=default"
        )
        mock_clean_account.set_config_from_qr.assert_called_with(expected_dclogin)
        mock_clean_account.start_io.assert_called_once()


if __name__ == "__main__":
    unittest.main()
