# cmping — chatmail relay message tester

`cmping` sends real Delta Chat messages between chatmail relays and reports
whether they arrive and how long delivery takes, like `ping` does for hosts.
Its companion [cmcall](https://github.com/mrgluek/cmcall) does the same for
Delta Chat calls.

A run has three steps:

1. **Setup**: a sender profile on relay1 and a receiver profile on relay2. The
   profiles are kept and reused across runs (see [Test profiles](#test-profiles)).
2. **Ping**: the sender creates a group with the receiver and sends a message
   every interval. Each message carries the time it was sent.
3. **Receive**: when the receiver gets a message, its time is *now − send time*:
   sender → relay1 (SMTP) → relay2 (IMAP) → receiver.

With `-b`/`--both` the receiver then sends back to the sender the same way, so
one run times both directions with the same profiles.

This is a fork of [chatmail/cmping](https://github.com/chatmail/cmping) with
fallbacks for relays that are hard to create profiles on, timeouts for
monitoring use, `--both` and `--json`.

## Installation

```bash
python3 -m venv cmping-venv
source cmping-venv/bin/activate
pip install git+https://github.com/mrgluek/cmping.git
```

`pip install cmping` installs the upstream version from PyPI, not this fork.

Requires `deltachat-rpc-client`/`deltachat-rpc-server` **2.56.0** or newer
(installed as dependencies). Tested with Python 3.11.

## Quick start

Ping one relay (sender and receiver both on `chatmail.uk`):

    cmping chatmail.uk

Ping from `chatmail.uk` to `chat.gluek.info`, and back:

    cmping -b chatmail.uk chat.gluek.info

Example output:

    $ cmping -c 2 -b chatmail.uk chat.gluek.info
    # using accounts_dir for chat.gluek.info at: ~/.cache/cmping/chat.gluek.info
    # using accounts_dir for chatmail.uk at: ~/.cache/cmping/chatmail.uk
    # Setting up profiles... Done!
    # Waiting for profiles to be online... Done!
    CMPING chatmail.uk(g9xn5gqze@chatmail.uk) -> chat.gluek.info(group with 1 receivers) count=2 interval=1.1s
    64 bytes ME -> chatmail.uk -> chat.gluek.info -> ME seq=0 time=1109.23ms 1/1 (elapsed: 0.00ms)
    64 bytes ME -> chatmail.uk -> chat.gluek.info -> ME seq=1 time=1009.18ms 1/1 (elapsed: 0.00ms)
    --- g9xn5gqze@chatmail.uk -> 1 receivers statistics ---
    2 transmitted, 2 received, 0.00% loss
    rtt min/avg/max/mdev = 1009.177/1059.202/1109.227/70.746 ms
    CMPING chat.gluek.info(nniylfz4m@chat.gluek.info) -> chatmail.uk(group with 1 receivers) count=2 interval=1.1s
    64 bytes ME -> chat.gluek.info -> chatmail.uk -> ME seq=0 time=1271.74ms 1/1 (elapsed: 0.00ms)
    64 bytes ME -> chat.gluek.info -> chatmail.uk -> ME seq=1 time=827.19ms 1/1 (elapsed: 0.00ms)
    --- nniylfz4m@chat.gluek.info -> 1 receivers statistics ---
    2 transmitted, 2 received, 0.00% loss
    rtt min/avg/max/mdev = 827.189/1049.463/1271.737/314.343 ms
    --- timing statistics ---
    account setup: 837.37ms
    message send/recv chatmail.uk -> chat.gluek.info: 2.12s
    send rate chatmail.uk -> chat.gluek.info: 0.94 msg/s
    recv rate chatmail.uk -> chat.gluek.info: 0.94 msg/s
    message send/recv chat.gluek.info -> chatmail.uk: 1.94s
    send rate chat.gluek.info -> chatmail.uk: 1.03 msg/s
    recv rate chat.gluek.info -> chatmail.uk: 1.03 msg/s

### Reading the numbers

- **`time=`** and **`rtt min/avg/max/mdev`** are the delivery time of one
  message in one direction, from sending to receipt: relay1's SMTP, relay1
  delivering to relay2, relay2's IMAP push to the receiver. The name `rtt` is
  kept from `ping`; nothing travels back. Sender and receiver run in the same
  `cmping` process, so both times come from one clock.
- **`1/1 (elapsed: …)`**: with several receivers (`-g`), how many got the message
  so far, and the spread between the first and the last.
- **`loss`**: pings that did not arrive within `--grace` seconds after the last one
  was sent.
- **`account setup`**: setting up the profiles and waiting until all of them
  are online. It is short when the cached profiles are reused.
- **`message send/recv`** and **rates**: the time from the first ping to the last
  receipt per direction.
- **`✗ ERROR: profile uses multiple relays`**: a profile has more than one relay,
  so not only the given relays were measured.

The exit code is 0 when every ping arrived in every direction, 1 otherwise.

## Options

    cmping [-c N] [-i S] [-g N] [-b] [-w S] [--grace S] [-l N] [--reset] [--json] [-v]
           relay1 [relay2]

- `relay1`, `relay2`: a relay domain, an IP address or an `https://` account
  endpoint (see below). Without `relay2`, `relay1` pings itself.
- `-c N`: number of pings per direction (default 30).
- `-i S`: seconds between pings (default 1.1).
- `-g N`: number of receivers on relay2 (default 1). `--both` sends back from
  the first one.
- `-b`, `--both`: also ping from relay2 back to relay1 with the same profiles.
  Ignored when both relays are the same.
- `-w S`: seconds to wait for the profiles to come online (default 60). After
  that the run fails with `<address> not online after Ss`, naming the profile
  whose relay does not let it in.
- `--grace S`: seconds to wait for missing pings after the last one was sent
  (default 60). Lost pings then count as loss.
- `-l N`: login length for profiles `cmping` creates itself, i.e. on IP relays
  (default 9). A domain relay picks its own logins.
- `--reset`: delete the cached profiles of the given relays first.
- `--json`: print one JSON object (see below) on stdout; the usual output goes
  to stderr.
- `-v`: more output; `-vvv` logs every core event.
- `--version`: print the version.

Every limit is meant for monitoring too: with `-w` and `--grace` below the
caller's own timeout, a run ends with a result instead of being killed.

### JSON output

    $ cmping -c 2 -b --json chatmail.uk chat.gluek.info 2>/dev/null

```json
{
  "cmping": "0.19.0",
  "relay1": "chatmail.uk",
  "relay2": "chat.gluek.info",
  "count": 2,
  "interval": 1.1,
  "ok": true,
  "error": null,
  "account_setup_s": 0.727,
  "directions": [
    {
      "from": "chatmail.uk",
      "to": "chat.gluek.info",
      "sender": "g9xn5gqze@chatmail.uk",
      "receivers": ["nniylfz4m@chat.gluek.info"],
      "sent": 2,
      "received": 2,
      "expected": 2,
      "loss_pct": 0.0,
      "delivery_ms": {"min": 949.314, "avg": 1047.718, "max": 1146.122, "mdev": 139.164},
      "message_s": 2.058
    },
    {
      "from": "chat.gluek.info",
      "to": "chatmail.uk",
      "sender": "nniylfz4m@chat.gluek.info",
      "receivers": ["g9xn5gqze@chatmail.uk"],
      "sent": 2,
      "received": 2,
      "expected": 2,
      "loss_pct": 0.0,
      "delivery_ms": {"min": 905.248, "avg": 1129.361, "max": 1353.473, "mdev": 316.943},
      "message_s": 2.011
    }
  ],
  "warnings": []
}
```

- `ok`: no error, and every direction sent all pings and received all expected.
- `error`: why the run stopped before pinging, e.g. `Timeout or error waiting for
  profiles to be online: nniylfz4m@chat.gluek.info not online after 30s`;
  `directions` is then empty.
- `delivery_ms`: the `rtt` line of the text output; `null` when nothing arrived.
- `warnings`: e.g. a profile that uses multiple relays.

## Test profiles

Profiles are kept per relay in `~/.cache/cmping/<relay>/` (`$XDG_CACHE_HOME`)
and reused, because some relays only hand out new profiles from some networks
or limit how many. A run:

- removes profiles left unconfigured by killed runs (configured ones are never
  removed);
- deletes the chats of earlier runs (local only, at most 10 s per run);
- gives up on a configure attempt after 20 s.

Profiles are created with `dcaccount:<domain>`, or from an IP address with a
generated `dclogin:` link. When that fails, `cmping` asks the relay's HTTPS
`/new` endpoint for an address and password, and as a last resort (interactive
runs only) for credentials typed in.

### Custom relays with HTTPS URL endpoints

For relays that support the [mailadm HTTP API](https://github.com/deltachat/mailadm),
give the endpoint instead of a domain:

    cmping https://your-relay.com/new_email?t=token1 chat.gluek.info

The endpoint returns the account as JSON:

```json
{
    "email": "customlogin@your-relay.com",
    "password": "securePasswordHere..."
}
```

## Development

    pip install -e .
    pip install pytest
    python -m pytest tests

Releases: add a section to `CHANGELOG.md`, commit, then tag `vX.Y.Z` and push
the tag; the version comes from the tag (setuptools-git-versioning).
