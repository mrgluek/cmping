"""
chatmail ping aka "cmping" transmits messages between relays.

Message Flow:
=============
1. ACCOUNT SETUP: Create sender and receiver accounts on specified relay domains
   - Each account connects to its relay's IMAP/SMTP servers
   - Accounts wait for IMAP_INBOX_IDLE state indicating readiness

2. GROUP CREATION: Sender creates a group chat and adds all receivers

3. PING SEND: Sender transmits messages to the group at specified intervals
   - Messages contain: unique-id timestamp sequence-number
   - Messages flow: Sender -> relay1 SMTP -> relay2 IMAP -> Receivers

4. PING RECEIVE: Each receiver waits for incoming messages
   - On receipt, round-trip time is calculated from embedded timestamp
   - Progress is tracked per-sequence across all receivers
   - Stats are accumulated for final report
"""

import argparse
import contextlib
import getpass
import ipaddress
import os
import queue
from queue import Empty
import random
import shutil
import signal
import string
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from statistics import stdev

from deltachat_rpc_client import DeltaChat, EventType, Rpc
from xdg_base_dirs import xdg_cache_home

# Spinner characters for progress display
SPINNER_CHARS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


@dataclass
class RelayContext:
    """Context for a relay including its RPC connection, DeltaChat instance, and account maker."""

    rpc: Rpc
    dc: DeltaChat
    maker: "AccountMaker"


def log_event_verbose(event, addr, verbose_level=3):
    """Helper function to log events at specified verbose level."""
    if hasattr(event, "msg") and event.msg:
        print(f"  [{addr}] {event.kind}: {event.msg}")
    else:
        print(f"  [{addr}] {event.kind}")


def self_addr(account):
    """The profile's own address, or "" if it is not configured.

    Read from the self contact: core 2.66 removed the configured_addr config
    (get_config raises "unknown key"), and "addr" has been empty since 2.61."""
    try:
        addr = account.self_contact.get_snapshot().address
    except Exception:
        return ""
    return addr if isinstance(addr, str) else ""


def is_ip_address(host):
    """Check if the given host is an IP address."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def generate_credentials():
    """Generate random username and password for IP-based login.

    Returns:
        tuple: (username, password) where username is 12 chars and password is 20 chars
    """
    chars = string.ascii_lowercase + string.digits
    username = "".join(random.choices(chars, k=12))
    password = "".join(random.choices(chars, k=20))
    return username, password


def create_qr_url(domain_or_ip):
    """Create either a dcaccount or dclogin URL based on input type.

    Args:
        domain_or_ip: Either a domain name, IP address, or HTTPS URL endpoint

    Returns:
        str: Either dcaccount:domain, dclogin:username@ip/?p=..., or dcaccount:https://...
    """
    # Check if domain_or_ip is an HTTPS URL endpoint (for mailadm-style API)
    if domain_or_ip.startswith("https://"):
        return f"dcaccount:{domain_or_ip}"
    
    if is_ip_address(domain_or_ip):
        # Generate credentials for IP address
        username, password = generate_credentials()

        # Build dclogin URL according to spec
        # dclogin:username@ip/?p=password&v=1&ip=993&sp=465&ic=3&ss=default
        encoded_password = urllib.parse.quote(password, safe="")

        # Format: dclogin:username@host/?query
        qr_url = (
            f"dclogin:{username}@{domain_or_ip}/?"
            f"p={encoded_password}&v=1&ip=993&sp=465&ic=3&ss=default"
        )
        return qr_url
    else:
        # Use dcaccount for domain names (server/client generates 9-char login automatically)
        return f"dcaccount:{domain_or_ip}"


def try_https_endpoint(domain_or_url, verbose=0):
    """Try to obtain account from https://domain/new or https://domain/new_email endpoint.
    Or if domain_or_url is a URL, try to obtain from it directly.

    Args:
        domain_or_url: The domain name (e.g., 'chatmail.uk') or full HTTPS URL
        verbose: Verbosity level

    Returns:
        tuple: (email, password) if successful, None otherwise
    """
    if domain_or_url.startswith("https://"):
        endpoints = [domain_or_url]
    else:
        # Try both /new and /new_email endpoints
        endpoints = [f"https://{domain_or_url}/new", f"https://{domain_or_url}/new_email"]
    
    for endpoint in endpoints:
        # Try POST method first (usually required by chatmail to bypass dcaccount: browser redirect)
        for method in ['POST', 'GET']:
            try:
                # Make a request to the endpoint
                req = urllib.request.Request(endpoint, method=method)
                # Add a user agent to avoid being blocked
                req.add_header('User-Agent', 'cmping/1.0')
                response = urllib.request.urlopen(req, timeout=10)
                if response.getcode() == 200:
                    data = response.read().decode('utf-8')
                    import json
                    result = json.loads(data)
                    if 'email' in result and 'password' in result:
                        return result['email'], result['password']
            except Exception as e:
                if verbose >= 3:
                    print(f"  HTTPS endpoint {endpoint} ({method}) failed: {e}")
                continue
    
    return None


def prompt_manual_credentials(domain):
    """Prompt user to manually enter login and password.

    Args:
        domain: The domain name for the account

    Returns:
        tuple: (email, password)
    """
    print(f"\n✗ Failed to automatically create account for {domain}")
    print("Please enter your credentials manually:")
    
    # Get email address
    while True:
        email = input(f"Enter email address for {domain} (e.g., yourlogin@{domain}): ").strip()
        if email and '@' in email:
            break
        print("Invalid email format. Please try again.")
    
    # Get password
    while True:
        password = getpass.getpass(f"Enter password for {email}: ")
        if password:
            break
        print("Password cannot be empty. Please try again.")
    
    return email, password


def print_progress(message, current=None, total=None, spinner_idx=0, done=False):
    """Print progress with optional spinner and counter.

    Args:
        message: The progress message to display
        current: Current count (optional)
        total: Total count (optional)
        spinner_idx: Index into SPINNER_CHARS for spinner animation
        done: If True, print 'Done!' and newline
    """
    if done:
        print(f"\r# {message}... Done!".ljust(60))
    elif current is not None and total is not None:
        spinner = SPINNER_CHARS[spinner_idx % len(SPINNER_CHARS)]
        print(f"\r# {message} {spinner} {current}/{total}", end="", flush=True)
    else:
        spinner = SPINNER_CHARS[spinner_idx % len(SPINNER_CHARS)]
        print(f"\r# {message} {spinner}", end="", flush=True)


def format_duration(seconds):
    """Format a duration in seconds to a human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        str: Formatted duration (e.g., "1.23s" or "45.67ms")
    """
    if seconds >= 1:
        return f"{seconds:.2f}s"
    else:
        return f"{seconds * 1000:.2f}ms"


def main():
    """Ping between addresses of specified chatmail relay domains or IP addresses."""

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "relay1",
        action="store",
        help="chatmail relay domain or IP address",
    )
    parser.add_argument(
        "relay2",
        action="store",
        nargs="?",
        help="chatmail relay domain or IP address (defaults to relay1 if not specified)",
    )
    parser.add_argument(
        "-c",
        dest="count",
        type=int,
        default=30,
        help="number of message pings",
    )
    parser.add_argument(
        "-i",
        dest="interval",
        type=float,
        default=1.1,
        help="seconds between message sending (default 1.1)",
    )
    parser.add_argument(
        "-v", dest="verbose", action="count", default=0, help="increase verbosity"
    )
    parser.add_argument(
        "-g",
        dest="numrecipients",
        type=int,
        default=1,
        help="number of group recipients (default 1)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="remove all account directories of tested relays to force fresh account creation",
    )
    parser.add_argument(
        "-l", "--login-length",
        dest="login_length",
        type=int,
        default=9,
        help="length of login/username before @server.tld (default 9)",
    )
    args = parser.parse_args()
    if not args.relay2:
        args.relay2 = args.relay1

    pinger = perform_ping(args)
    expected_total = pinger.sent * args.numrecipients
    raise SystemExit(0 if pinger.received == expected_total else 1)


# Seconds one configure attempt may take. Core has no overall configure
# timeout: chat.chudppl.org's autoconfig lists ports that are unreachable from
# some networks first, and core then hangs on them for minutes. Callers such
# as monitors run cmping with a 60-90 s timeout, so a hanging configure got
# the whole run killed. 20 s is plenty for a reachable relay (usually < 2 s)
# and leaves time for the dclogin fallback.
CONFIGURE_TIMEOUT = 20.0

# Seconds to wait for the configure call to return after asking core to stop.
CONFIGURE_STOP_GRACE = 5.0


def build_dclogin_url(email, password):
    """Build a dclogin URL for credentials obtained from an HTTPS endpoint.

    For a domain relay the URL names the standard ports explicitly
    (IMAP 993 and SMTP 465, both SSL), so core tries only those and skips
    autoconfig, whose port list may start with ports that are blocked from
    our network. IP relays keep the old parameters (ic=3 accepts their
    self-signed certificates, ss=default lets core pick the security).
    """
    username, host = email.split("@", 1)
    encoded_password = urllib.parse.quote(password, safe="")
    if is_ip_address(host):
        params = f"ih={host}&sh={host}&ip=993&sp=465&ic=3&ss=default"
    else:
        params = f"ih={host}&ip=993&is=ssl&sh={host}&sp=465&ss=ssl"
    return f"dclogin:{username}@{host}/?p={encoded_password}&v=1&{params}"


def configure_with_timeout(account, qr_url, timeout=None, verbose=0):
    """Configure account from a dcaccount/dclogin QR code within timeout seconds.

    set_config_from_qr() blocks until core finishes configuring, with no time
    limit of its own. It runs in a helper thread; on timeout core is told to
    stop (configure is an "ongoing process" core can cancel) and we wait a
    little for the call to return.

    Returns True if the account is configured, False on error or timeout.
    """
    if timeout is None:
        timeout = CONFIGURE_TIMEOUT
    result = {}

    def run():
        try:
            account.set_config_from_qr(qr_url)
            result["ok"] = True
        except Exception as e:
            result["error"] = e

    # Daemon thread: if core never answers, it must not keep cmping alive.
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        if verbose >= 2:
            print(f"\n✗ Configure did not finish within {timeout:.0f}s, stopping it")
        try:
            # Account has no wrapper for this JSON-RPC method.
            account._rpc.stop_ongoing_process(account.id)
        except Exception as e:
            if verbose >= 3:
                print(f"  stop_ongoing_process failed: {e}")
        thread.join(CONFIGURE_STOP_GRACE)
        return False
    if "error" in result:
        if verbose >= 2:
            print(f"\n✗ Configure failed: {result['error']}")
        return False
    try:
        return bool(account.is_configured())
    except Exception:
        return False


def remove_account_quietly(account, verbose=0):
    """Remove an account, ignoring errors (it may already be gone)."""
    try:
        account.remove()
    except Exception as e:
        if verbose >= 3:
            print(f"  Could not remove account {account.id}: {e}")


class AccountMaker:
    def __init__(self, dc, verbose=0):
        self.dc = dc
        self.online = []
        self.verbose = verbose
        self._cleaned_up = False

    def _log_event(self, event, addr):
        """Helper method to log events at verbose level 3."""
        if self.verbose >= 3:
            if hasattr(event, "msg") and event.msg:
                print(f"  {event.kind}: {event.msg} [{addr}]")
            else:
                print(f"  {event.kind} [{addr}]")

    def wait_all_online(self):
        remaining = list(self.online)
        while remaining:
            ac = remaining.pop()
            while True:
                event = ac.wait_for_event()
                if event.kind == EventType.IMAP_INBOX_IDLE:
                    if self.verbose >= 3:
                        addr = self_addr(ac)
                        print(f"✓ IMAP_INBOX_IDLE: {addr} is now idle and ready")
                    break
                elif event.kind == EventType.ERROR:
                    if self.verbose >= 1:
                        print(f"✗ ERROR during profile setup: {event.msg}")
                    raise Exception(f"Profile setup error: {event.msg}")
                elif self.verbose >= 3:
                    # Show all events during online phase when verbose level 3
                    addr = self_addr(ac)
                    self._log_event(event, addr)

    def _add_online(self, account):
        if self.verbose >= 3:
            addr = self_addr(account)
            print(f"  Starting I/O for account: {addr}")

        # Enable bot mode in all accounts before starting I/O
        # so we don't have to accept contact requests.
        account.set_config("bot", "1")
        account.start_io()
        self.online.append(account)

    def remove_unconfigured_accounts(self):
        """Remove accounts that never finished configuring.

        A run killed during configure (e.g. by a caller's timeout) leaves an
        unconfigured account in the accounts dir; without cleanup every such
        run adds one more. Configured accounts are never touched, as some
        relays only hand out new accounts from certain networks.
        """
        for account in self.dc.get_all_accounts():
            if account in self.online:
                continue
            try:
                configured = account.is_configured()
            except Exception:
                # Unknown state: keep it rather than risk losing an account.
                continue
            if not configured:
                if self.verbose >= 2:
                    print(f"  Removing unconfigured leftover account {account.id}")
                remove_account_quietly(account, self.verbose)

    def get_relay_account(self, domain):
        if not self._cleaned_up:
            self._cleaned_up = True
            self.remove_unconfigured_accounts()

        # Try to find an existing account for this domain/IP/URL
        for account in self.dc.get_all_accounts():
            addr = self_addr(account)
            if addr:
                # Extract the domain/IP/URL from the configured address
                if "@" in addr:
                    addr_domain = addr.split("@")[1]
                else:
                    # For HTTPS URL endpoints, the address format may vary
                    addr_domain = domain if "https://" in str(domain) else None
                
                # Check if domain matches (for standard domains or HTTPS URLs)
                is_https_url = "https://" in str(domain)
                if is_https_url:
                    # For HTTPS URLs, check if the account was created from a similar URL
                    qr_config = account.get_config("qr_url")
                    if qr_config and domain in qr_config:
                        if account not in self.online:
                            if self.verbose >= 3:
                                print(f"  Reusing existing account: {addr}")
                            break
                elif addr_domain == domain:
                    if account not in self.online:
                        if self.verbose >= 3:
                            print(f"  Reusing existing account: {addr}")
                        break
        else:
            account = self._create_account(domain)

        try:
            self._add_online(account)
        except Exception as e:
            print(f"✗ Failed to bring profile online for {domain}: {e}")
            raise

        return account

    def _create_account(self, domain):
        """Create and configure a new account on domain.

        Order: dcaccount QR (core creates the account), then for domain
        relays the HTTPS /new endpoint + dclogin with standard ports, then
        (only on a terminal) manually entered credentials. Each automatic
        attempt is time limited. An account that ends up unconfigured is
        removed, so failed runs leave nothing behind.
        """
        account = self.dc.add_account()
        if self.verbose >= 3:
            print(f"  Creating new account for domain: {domain}")
            print(f"  Configuring account from QR: {domain}")
        if configure_with_timeout(account, create_qr_url(domain), verbose=self.verbose):
            if self.verbose >= 3:
                print(f"  Account configured: {self_addr(account)}")
            return account

        # Partly configured state could confuse the next attempt: start over
        # with a fresh account.
        remove_account_quietly(account, self.verbose)

        # Fallback for domain names and HTTPS URLs. IP relays get random
        # credentials in the dclogin QR already, there is no /new to ask.
        if not is_ip_address(domain):
            if self.verbose >= 2:
                print(f"\n✗ Failed to configure profile on {domain} via QR")
                print("  Trying HTTPS endpoint fallback...")
            result = try_https_endpoint(domain, self.verbose)
            if result:
                email, password = result
                if self.verbose >= 2:
                    print(f"  Successfully obtained credentials from HTTPS endpoint: {email}")
                account = self.dc.add_account()
                if configure_with_timeout(
                    account, build_dclogin_url(email, password), verbose=self.verbose
                ):
                    if self.verbose >= 3:
                        print(f"  Account configured: {email}")
                    return account
                remove_account_quietly(account, self.verbose)
            elif self.verbose >= 2:
                print(f"  HTTPS endpoint fallback failed for {domain}")

        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            # Not in interactive mode, fail gracefully
            print(f"\n✗ Failed to automatically create account for {domain}")
            print("  (HTTPS endpoint fallback also failed)")
            print("✗ Cannot proceed in non-interactive mode - cannot prompt for manual credentials")
            raise Exception(f"Account creation failed for {domain}")

        email, password = prompt_manual_credentials(domain)
        account = self.dc.add_account()
        if self.verbose >= 3:
            print(f"  Configuring account manually: {email}")
        try:
            # No time limit: a person is watching and can press Ctrl-C.
            account.set_config_from_qr(build_dclogin_url(email, password))
        except Exception as e3:
            print(f"✗ Failed to configure profile with manual credentials: {e3}")
            remove_account_quietly(account, self.verbose)
            raise
        if self.verbose >= 3:
            print(f"  Account configured: {self_addr(account)}")
        return account


def setup_accounts(args, sender_maker, receiver_maker):
    """Set up sender and receiver accounts with progress display.

    Timing: This function's duration is tracked as 'account_setup_time'.

    Args:
        args: Command line arguments
        sender_maker: AccountMaker for the sender's relay
        receiver_maker: AccountMaker for the receiver's relay

    Returns:
        tuple: (sender_account, list_of_receiver_accounts)
    """
    # Calculate total profiles needed
    total_profiles = 1 + args.numrecipients
    profiles_created = 0

    # Create sender and receiver accounts with spinner
    print_progress("Setting up profiles", profiles_created, total_profiles, 0)

    try:
        sender = sender_maker.get_relay_account(args.relay1)
        profiles_created += 1
        print_progress("Setting up profiles", profiles_created, total_profiles, profiles_created)
    except Exception as e:
        print(f"\r✗ Failed to setup sender profile on {args.relay1}: {e}")
        sys.exit(1)

    # Create receiver accounts
    receivers = []
    for i in range(args.numrecipients):
        try:
            receiver = receiver_maker.get_relay_account(args.relay2)
            receivers.append(receiver)
            profiles_created += 1
            print_progress("Setting up profiles", profiles_created, total_profiles, profiles_created)
        except Exception as e:
            print(f"\r✗ Failed to setup receiver profile {i+1} on {args.relay2}: {e}")
            sys.exit(1)

    # Profile setup complete
    print_progress("Setting up profiles", done=True)

    return sender, receivers


# Chat ids up to 9 are special (trash, archive link, ...), never real chats
LAST_SPECIAL_CHAT_ID = 9


def delete_old_chats(accounts, time_budget=10.0, verbose=0):
    """Delete chats left over from earlier runs in the cached profiles.

    Every run creates a new "cmping" group on the sender and on each receiver.
    Nothing deleted them, so profiles reused across runs grew without bound
    (tens of thousands of groups, 20+ MB dc.db per profile for a monitor that
    pings every half hour). Clean up at the start of a run, not at its end: a
    run can be cut short by a timeout or SIGINT before any end-of-run cleanup.

    Deleting is local only (messages on the server are left alone). The time
    budget keeps a first run on a big old profile from slowing the ping; the
    rest is deleted on the next runs.

    Returns the number of deleted chats.
    """
    deadline = time.time() + time_budget
    deleted = 0
    seen = set()
    for account in accounts:
        if account.id in seen:
            continue
        seen.add(account.id)
        try:
            chats = account.get_chatlist(no_specials=True)
        except Exception as e:
            if verbose >= 1:
                print(f"✗ Could not list chats for cleanup: {e}")
            continue
        for chat in chats:
            if chat.id <= LAST_SPECIAL_CHAT_ID:
                continue
            if time.time() >= deadline:
                if verbose >= 2:
                    print(f"  Chat cleanup paused after {deleted} chats (time budget)")
                return deleted
            try:
                chat.delete()
                deleted += 1
            except Exception as e:
                if verbose >= 1:
                    print(f"✗ Could not delete chat {chat.id}: {e}")
    if verbose >= 2 and deleted:
        print(f"  Deleted {deleted} chats from earlier runs")
    return deleted


def create_group(sender, receivers, verbose=0):
    """Create a group chat.

    Returns:
        group: The created group chat object
    """
    # Create a group chat from sender and add all receivers
    if verbose >= 3:
        print("  Creating group chat 'cmping'")
    group = sender.create_group("cmping")
    for receiver in receivers:
        # Create a contact for the receiver account and add to group
        contact = sender.create_contact(receiver)
        if verbose >= 3:
            receiver_addr = self_addr(receiver)
            print(f"  Adding {receiver_addr} to group")
        group.add_contact(contact)

    return group


def wait_profiles_online(maker):
    """Wait for all profiles to be online with spinner progress.

    Args:
        maker: AccountMaker instance with accounts to wait for

    Raises:
        SystemExit: If waiting for profiles fails
    """
    # Flag to indicate when wait_all_online is complete
    online_complete = threading.Event()
    online_error = None

    def wait_online_thread():
        nonlocal online_error
        try:
            maker.wait_all_online()
        except Exception as e:
            online_error = e
        finally:
            online_complete.set()

    # Start the wait in a separate thread
    wait_thread = threading.Thread(target=wait_online_thread)
    wait_thread.start()

    # Show spinner while waiting
    spinner_idx = 0
    while not online_complete.is_set():
        print_progress("Waiting for profiles to be online", spinner_idx=spinner_idx)
        spinner_idx += 1
        online_complete.wait(timeout=0.1)

    wait_thread.join()

    if online_error:
        print(f"\n✗ Timeout or error waiting for profiles to be online: {online_error}")
        sys.exit(1)

    print_progress("Waiting for profiles to be online", done=True)


def wait_profiles_online_multi(makers):
    """Wait for all profiles to be online with spinner progress.

    Args:
        makers: List of AccountMaker instances with accounts to wait for

    Raises:
        SystemExit: If waiting for profiles fails
    """
    online_errors = []

    def wait_online_thread(maker):
        try:
            maker.wait_all_online()
        except Exception as e:
            online_errors.append(e)

    # Start a thread for each maker
    threads = []
    for maker in makers:
        wait_thread = threading.Thread(target=wait_online_thread, args=(maker,))
        wait_thread.start()
        threads.append(wait_thread)

    # Show spinner while waiting
    spinner_idx = 0
    while any(t.is_alive() for t in threads):
        print_progress("Waiting for profiles to be online", spinner_idx=spinner_idx)
        spinner_idx += 1
        time.sleep(0.1)

    for t in threads:
        t.join()

    if online_errors:
        print(f"\n✗ Timeout or error waiting for profiles to be online: {online_errors[0]}")
        sys.exit(1)

    print_progress("Waiting for profiles to be online", done=True)


def perform_ping(args):
    """Main ping execution function with timing measurements.

    Timing Phases:
    1. account_setup_time: Time to create and configure all accounts
    2. message_time: Time to send and receive all ping messages

    Returns:
        Pinger: The pinger object with results
    """
    base_accounts_dir = xdg_cache_home().joinpath("cmping")
    
    # Determine unique relays being tested. Using a set to deduplicate when
    # relay1 == relay2 (same relay testing), so we only create one RPC context.
    relays = {args.relay1, args.relay2}
    
    # Handle --reset option: remove account directories for tested relays
    if args.reset:
        for relay in relays:
            relay_dir = base_accounts_dir.joinpath(relay)
            if relay_dir.exists():
                print(f"# Removing account directory for {relay}: {relay_dir}")
                try:
                    shutil.rmtree(relay_dir)
                except Exception:
                    pass
    
    # Create per-relay account directories and RPC instances.
    relay_contexts = {}  # {relay: RelayContext}

    with contextlib.ExitStack() as exit_stack:
        for relay in relays:
            relay_dir = base_accounts_dir.joinpath(relay)
            print(f"# using accounts_dir for {relay} at: {relay_dir}")
            if relay_dir.exists() and not relay_dir.joinpath("accounts.toml").exists():
                try:
                    shutil.rmtree(relay_dir)
                except Exception:
                    pass

            try:
                rpc = exit_stack.enter_context(Rpc(accounts_dir=relay_dir))
            except Exception as e:
                print(f"✗ Failed to initialize RPC for {relay}: {e}")
                raise
            dc = DeltaChat(rpc)
            maker = AccountMaker(dc, verbose=args.verbose)
            relay_contexts[relay] = RelayContext(rpc=rpc, dc=dc, maker=maker)

        # Phase 1: Account Setup (timed)
        account_setup_start = time.time()

        # Set up sender and receiver accounts using per-relay makers
        sender_maker = relay_contexts[args.relay1].maker
        receiver_maker = relay_contexts[args.relay2].maker
        sender, receivers = setup_accounts(args, sender_maker, receiver_maker)

        # Wait for all accounts to be online with timeout feedback
        # Combine all makers for waiting
        all_makers = [relay_contexts[r].maker for r in relays]
        wait_profiles_online_multi(all_makers)

        account_setup_time = time.time() - account_setup_start

        # Not timed: housekeeping, not part of the relay's performance
        delete_old_chats([sender] + list(receivers), verbose=args.verbose)

        group = create_group(sender, receivers, verbose=args.verbose)

        # Phase 2: Message Ping/Pong (timed)
        message_start = time.time()

        pinger = Pinger(args, sender, group, receivers)
        received = {}
        # Track current sequence for output formatting
        current_seq = None
        # Track timing for each sequence: {seq: {'count': N, 'first_time': ms, 'last_time': ms, 'size': bytes}}
        seq_tracking = {}
        try:
            for seq, ms_duration, size, receiver_idx in pinger.receive():
                if seq not in received:
                    received[seq] = []
                received[seq].append(ms_duration)

                # Track timing for this sequence
                if seq not in seq_tracking:
                    seq_tracking[seq] = {
                        "count": 0,
                        "first_time": ms_duration,
                        "last_time": ms_duration,
                        "size": size,
                    }
                seq_tracking[seq]["count"] += 1
                seq_tracking[seq]["last_time"] = ms_duration

                # Print new line for new sequence or first message
                if current_seq != seq:
                    if current_seq is not None:
                        print()  # End previous line
                    # Start new line for this sequence
                    print(
                        f"{size} bytes ME -> {pinger.relay1} -> {pinger.relay2} -> ME seq={seq} time={ms_duration:0.2f}ms",
                        end="",
                        flush=True,
                    )
                    current_seq = seq

                # Print N/M ratio with in-place update (spinning effect)
                count = seq_tracking[seq]["count"]
                total = args.numrecipients
                # Calculate how many characters we need to overwrite from previous ratio
                if count > 1:
                    # Backspace over previous ratio to update in-place
                    prev_count = count - 1
                    prev_ratio_len = len(f" {prev_count}/{total}")
                    print("\b" * prev_ratio_len, end="", flush=True)
                print(f" {count}/{total}", end="", flush=True)

                # If all receivers have received, print elapsed time
                if count == total:
                    first_time = seq_tracking[seq]["first_time"]
                    last_time = seq_tracking[seq]["last_time"]
                    elapsed = last_time - first_time
                    print(f" (elapsed: {elapsed:0.2f}ms)", end="", flush=True)

        except KeyboardInterrupt:
            pass

        message_time = time.time() - message_start

        if current_seq is not None:
            print()  # End last line

        # Print statistics - show full addresses only in verbose >= 2
        if args.verbose >= 2:
            receivers_info = pinger.receivers_addrs_str
        else:
            receivers_info = f"{len(pinger.receivers_addrs)} receivers"
        print(f"--- {pinger.addr1} -> {receivers_info} statistics ---")
        print(
            f"{pinger.sent} transmitted, {pinger.received} received, {pinger.loss:.2f}% loss"
        )
        if received:
            all_durations = [d for durations in received.values() for d in durations]
            rmin = min(all_durations)
            ravg = sum(all_durations) / len(all_durations)
            rmax = max(all_durations)
            rmdev = stdev(all_durations) if len(all_durations) >= 2 else rmax
            print(
                f"rtt min/avg/max/mdev = {rmin:.3f}/{ravg:.3f}/{rmax:.3f}/{rmdev:.3f} ms"
            )

        # Print timing and rate statistics
        print("--- timing statistics ---")
        print(f"account setup: {format_duration(account_setup_time)}")
        print(f"message send/recv: {format_duration(message_time)}")

        # Calculate message rates
        if message_time > 0 and pinger.sent > 0:
            send_rate = pinger.sent / message_time
            print(f"send rate: {send_rate:.2f} msg/s")
        if message_time > 0 and pinger.received > 0:
            recv_rate = pinger.received / message_time
            print(f"recv rate: {recv_rate:.2f} msg/s")

        return pinger


class Pinger:
    """Handles sending ping messages and receiving responses.

    Message Flow:
    1. send_pings() runs in a background thread, sending messages at intervals
    2. Each message contains: unique_id timestamp sequence_number
    3. Messages are sent to a group chat (single send, multiple receivers)
    4. receive() yields (seq, duration, size, receiver_idx) for each received message
    5. Multiple receivers may receive each sequence number

    Attributes:
        sent: Number of messages sent
        received: Number of messages received (across all receivers)
        loss: Percentage of expected messages not received
    """

    def __init__(self, args, sender, group, receivers):
        """Initialize Pinger and start sending messages.

        Args:
            args: Command line arguments
            sender: Sender account object
            group: Group chat object
            receivers: List of receiver account objects
        """
        self.args = args
        self.sender = sender
        self.group = group
        self.receivers = receivers
        self.addr1 = self_addr(sender)
        self.receivers_addrs = [self_addr(receiver) for receiver in receivers]
        self.receivers_addrs_str = ", ".join(self.receivers_addrs)
        self.relay1 = self.addr1.split("@")[1]
        self.relay2 = self.receivers_addrs[0].split("@")[1]

        print(
            f"CMPING {self.relay1}({self.addr1}) -> {self.relay2}(group with {len(receivers)} receivers) count={args.count} interval={args.interval}s"
        )
        ALPHANUMERIC = string.ascii_lowercase + string.digits
        self.tx = "".join(random.choices(ALPHANUMERIC, k=30))
        t = threading.Thread(target=self.send_pings, daemon=True)
        self.sent = 0
        self.received = 0
        t.start()

    @property
    def loss(self):
        expected_total = self.sent * len(self.receivers)
        return 0.0 if expected_total == 0 else (1 - self.received / expected_total) * 100

    def send_pings(self):
        """Send ping messages to the group at regular intervals.

        Each message contains: unique_id timestamp sequence_number
        Flow: Sender -> SMTP relay1 -> IMAP relay2 -> All receivers
        """
        for seq in range(self.args.count):
            text = f"{self.tx} {time.time():.4f} {seq:17}"
            self.group.send_text(text)
            self.sent += 1
            time.sleep(self.args.interval)
        # we sent all pings, let's wait a bit, then force quit if main didn't finish
        time.sleep(60)
        os.kill(os.getpid(), signal.SIGINT)

    def receive(self):
        """Receive ping messages from all receivers.

        Yields:
            tuple: (seq, ms_duration, size, receiver_idx) for each received message
                - seq: Sequence number of the message
                - ms_duration: Round-trip time in milliseconds
                - size: Size of the message in bytes
                - receiver_idx: Index of the receiver that received the message
        """
        num_pending = self.args.count * len(self.receivers)
        start_clock = time.time()
        # Track which sequence numbers have been received by which receiver
        received_by_receiver = {}

        # Create a queue to collect events from all receivers
        event_queue = queue.Queue()

        def receiver_thread(receiver_idx, receiver):
            """Thread function to listen to events from a single receiver."""
            while True:
                try:
                    event = receiver.wait_for_event()
                    event_queue.put((receiver_idx, receiver, event))
                except Exception:
                    # If there's an error, put it in the queue
                    event_queue.put((receiver_idx, receiver, None))
                    break

        # Start a thread for each receiver
        threads = []
        for idx, receiver in enumerate(self.receivers):
            t = threading.Thread(
                target=receiver_thread, args=(idx, receiver), daemon=True
            )
            t.start()
            threads.append(t)

        while num_pending > 0:
            try:
                receiver_idx, receiver, event = event_queue.get(timeout=1.0)
                if event is None:
                    continue

                if event.kind == EventType.INCOMING_MSG:
                    msg = receiver.get_message_by_id(event.msg_id)
                    text = msg.get_snapshot().text
                    parts = text.strip().split()
                    if len(parts) == 3 and parts[0] == self.tx:
                        seq = int(parts[2])
                        if seq not in received_by_receiver:
                            received_by_receiver[seq] = set()
                        if receiver_idx not in received_by_receiver[seq]:
                            ms_duration = (time.time() - float(parts[1])) * 1000
                            self.received += 1
                            num_pending -= 1
                            received_by_receiver[seq].add(receiver_idx)
                            yield seq, ms_duration, len(text), receiver_idx
                            start_clock = time.time()
                    elif self.args.verbose >= 3:
                        # Log non-ping messages at verbose level 3
                        receiver_addr = self.receivers_addrs[receiver_idx]
                        ellipsis = "..." if len(text) > 50 else ""
                        print(
                            f"  [{receiver_addr}] INCOMING_MSG (non-ping): {text[:50]}{ellipsis}"
                        )
                elif event.kind == EventType.ERROR and self.args.verbose >= 1:
                    print(f"✗ ERROR: {event.msg}")
                elif event.kind == EventType.MSG_FAILED and self.args.verbose >= 1:
                    msg = receiver.get_message_by_id(event.msg_id)
                    text = msg.get_snapshot().text
                    print(f"✗ Message failed: {text}")
                elif (
                    event.kind in (EventType.INFO, EventType.WARNING)
                    and self.args.verbose >= 1
                ):
                    ms_now = (time.time() - start_clock) * 1000
                    print(f"INFO {ms_now:07.1f}ms: {event.msg}")
                elif self.args.verbose >= 3:
                    # Log all other events at verbose level 3
                    receiver_addr = self.receivers_addrs[receiver_idx]
                    log_event_verbose(event, receiver_addr)
            except Empty:
                # Timeout occurred, check if we should continue
                continue


if __name__ == "__main__":
    main()
