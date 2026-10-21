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
        
        # Domain relay: explicit standard ports with SSL, no autoconfig
        expected_dclogin = (
            "dclogin:fallback@domain.com/?"
            "p=fallback_password&v=1&ih=domain.com&ip=993&is=ssl&sh=domain.com&sp=465&ss=ssl"
        )
        mock_clean_account.set_config_from_qr.assert_called_with(expected_dclogin)
        mock_clean_account.start_io.assert_called_once()

    def test_wait_all_online_success(self):
        mock_dc = MagicMock()
        mock_account = MagicMock()
        
        # Mock wait_for_event to return IMAP_INBOX_IDLE event
        mock_event = MagicMock()
        mock_event.kind = cmping.EventType.IMAP_INBOX_IDLE
        mock_account.wait_for_event.return_value = mock_event
        mock_account.get_config.return_value = "test@domain.com"
        
        maker = cmping.AccountMaker(mock_dc, verbose=0)
        maker.online = [mock_account]
        
        # Should complete without throwing exception
        maker.wait_all_online()
        mock_account.wait_for_event.assert_called_once()

    def test_wait_all_online_error_raises_exception(self):
        mock_dc = MagicMock()
        mock_account = MagicMock()
        
        # Mock wait_for_event to return ERROR event
        mock_event = MagicMock()
        mock_event.kind = cmping.EventType.ERROR
        mock_event.msg = "Connection timed out"
        mock_account.wait_for_event.return_value = mock_event
        mock_account.get_config.return_value = "test@domain.com"
        
        maker = cmping.AccountMaker(mock_dc, verbose=0)
        maker.online = [mock_account]
        
        # Should raise an Exception
        with self.assertRaises(Exception) as context:
            maker.wait_all_online()
            
        self.assertIn("Profile setup error: Connection timed out", str(context.exception))
        mock_account.wait_for_event.assert_called_once()



class TestDeleteOldChats(unittest.TestCase):
    def _account(self, acc_id, chat_ids):
        account = MagicMock()
        account.id = acc_id
        chats = []
        for cid in chat_ids:
            chat = MagicMock()
            chat.id = cid
            chats.append(chat)
        account.get_chatlist.return_value = chats
        return account, chats

    def test_deletes_real_chats_and_skips_specials(self):
        account, chats = self._account(1, [6, 10, 11])
        self.assertEqual(cmping.delete_old_chats([account]), 2)
        account.get_chatlist.assert_called_once_with(no_specials=True)
        chats[0].delete.assert_not_called()
        chats[1].delete.assert_called_once()
        chats[2].delete.assert_called_once()

    def test_same_account_is_cleaned_once(self):
        # relay1 == relay2: sender and a receiver can share the profile list
        account, _ = self._account(1, [10])
        self.assertEqual(cmping.delete_old_chats([account, account]), 1)
        account.get_chatlist.assert_called_once()

    def test_time_budget_stops_cleanup(self):
        account, chats = self._account(1, [10, 11, 12])
        self.assertEqual(cmping.delete_old_chats([account], time_budget=0), 0)
        for chat in chats:
            chat.delete.assert_not_called()

    def test_errors_do_not_stop_the_run(self):
        broken, _ = self._account(1, [])
        broken.get_chatlist.side_effect = Exception("rpc down")
        account, chats = self._account(2, [10, 11])
        chats[0].delete.side_effect = Exception("gone")
        self.assertEqual(cmping.delete_old_chats([broken, account]), 1)


class TestAccountCreationHardening(unittest.TestCase):
    """Configure attempts are time limited and failures leave no accounts."""

    def _hanging_account(self, release):
        # set_config_from_qr blocks like core hanging on an unreachable port;
        # stop_ongoing_process makes it fail like core's "Canceled".
        account = MagicMock()
        account.id = 7

        def hang(qr):
            release.wait(5)
            raise Exception("Canceled")

        account.set_config_from_qr.side_effect = hang
        account._rpc.stop_ongoing_process.side_effect = lambda acc_id: release.set()
        return account

    def test_build_dclogin_url(self):
        self.assertEqual(
            cmping.build_dclogin_url("u@relay.example", "p/w"),
            "dclogin:u@relay.example/?p=p%2Fw&v=1&ih=relay.example&ip=993&is=ssl"
            "&sh=relay.example&sp=465&ss=ssl",
        )
        # IP relays keep the old parameters
        self.assertEqual(
            cmping.build_dclogin_url("u@1.2.3.4", "pw"),
            "dclogin:u@1.2.3.4/?p=pw&v=1&ih=1.2.3.4&sh=1.2.3.4&ip=993&sp=465&ic=3&ss=default",
        )

    def test_configure_with_timeout_stops_hanging_configure(self):
        import threading
        release = threading.Event()
        account = self._hanging_account(release)
        self.assertFalse(cmping.configure_with_timeout(account, "dcaccount:x", timeout=0.1))
        account._rpc.stop_ongoing_process.assert_called_once_with(7)

    @patch("cmping.CONFIGURE_TIMEOUT", 0.1)
    @patch("cmping.try_https_endpoint")
    def test_timeout_then_fallback_success(self, mock_try_https):
        import threading
        release = threading.Event()
        hanging = self._hanging_account(release)
        good = MagicMock()
        good.is_configured.return_value = True
        mock_dc = MagicMock()
        mock_dc.get_all_accounts.return_value = []
        mock_dc.add_account.side_effect = [hanging, good]
        mock_try_https.return_value = ("abc@relay.example", "pw")

        account = cmping.AccountMaker(mock_dc).get_relay_account("relay.example")

        self.assertIs(account, good)
        hanging._rpc.stop_ongoing_process.assert_called_once()
        hanging.remove.assert_called_once()
        good.set_config_from_qr.assert_called_once_with(
            "dclogin:abc@relay.example/?p=pw&v=1&ih=relay.example&ip=993&is=ssl"
            "&sh=relay.example&sp=465&ss=ssl"
        )
        good.remove.assert_not_called()
        good.start_io.assert_called_once()

    @patch("cmping.try_https_endpoint")
    def test_both_paths_fail_account_removed(self, mock_try_https):
        first = MagicMock()
        first.set_config_from_qr.side_effect = Exception("dcaccount failed")
        second = MagicMock()
        second.set_config_from_qr.side_effect = Exception("login failed")
        mock_dc = MagicMock()
        mock_dc.get_all_accounts.return_value = []
        mock_dc.add_account.side_effect = [first, second]
        mock_try_https.return_value = ("abc@relay.example", "pw")

        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(Exception):
                cmping.AccountMaker(mock_dc).get_relay_account("relay.example")

        first.remove.assert_called_once()
        second.remove.assert_called_once()
        second.start_io.assert_not_called()

    @patch("cmping.try_https_endpoint", return_value=None)
    def test_no_https_credentials_account_removed(self, mock_try_https):
        first = MagicMock()
        first.set_config_from_qr.side_effect = Exception("dcaccount failed")
        mock_dc = MagicMock()
        mock_dc.get_all_accounts.return_value = []
        mock_dc.add_account.return_value = first

        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(Exception):
                cmping.AccountMaker(mock_dc).get_relay_account("relay.example")
        first.remove.assert_called_once()
        mock_dc.add_account.assert_called_once()

    def test_leftover_unconfigured_accounts_removed(self):
        configured = MagicMock()
        configured.is_configured.return_value = True
        configured.get_config.side_effect = (
            lambda key: "me@relay.example" if key == "configured_addr" else None
        )
        leftover = MagicMock()
        leftover.is_configured.return_value = False
        leftover.get_config.return_value = None
        unknown = MagicMock()
        unknown.is_configured.side_effect = Exception("rpc error")
        unknown.get_config.return_value = None
        mock_dc = MagicMock()
        mock_dc.get_all_accounts.return_value = [leftover, unknown, configured]

        maker = cmping.AccountMaker(mock_dc)
        self.assertIs(maker.get_relay_account("relay.example"), configured)
        leftover.remove.assert_called_once()
        # Configured accounts and accounts of unknown state are kept
        configured.remove.assert_not_called()
        unknown.remove.assert_not_called()
        mock_dc.add_account.assert_not_called()

        # Cleanup runs once per maker, not for every receiver
        leftover.remove.reset_mock()
        mock_dc.get_all_accounts.return_value = [leftover]
        maker.remove_unconfigured_accounts = MagicMock()
        mock_dc.add_account.return_value = MagicMock()
        maker.get_relay_account("relay.example")
        maker.remove_unconfigured_accounts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
