# chatmail relay server ping tool

## Installation in a virtual environment (venv)

It is recommended to install `cmping` in a Python virtual environment:

```bash
# Create a virtual environment
python3 -m venv cmping-venv

# Activate the virtual environment
source cmping-venv/bin/activate  # On Linux/macOS
cmping-venv\Scripts\activate     # On Windows

# Install cmping
pip install cmping
```

**Note**: This package requires `deltachat-rpc-client` version **2.56.0** or higher to work correctly.

## Quick start

To send and receive from a single chatmail relay:

    cmping chatmail.uk   # <-- substitute with the domain you want to test 

To send from first domain to a second domain and receive from second domain:

    cmping chatmail.uk arcanechat.me 

### Custom Chatmail Relays with HTTPS URL Endpoints

For custom chatmail relay servers that support the [mailadm HTTP API](https://github.com/deltachat/mailadm), you can use HTTPS URL endpoints for account creation:

    cmping https://your-relay.com/new_email?t=token1 chat.gluek.info

The `DCACCOUNT:https://...` format allows the relay server to return account setup information as a JSON object:

```json
{
    "email": "customlogin@your-relay.com",
    "password": "securePasswordHere..."
}
```

To show help:

    cmping -h

Custom login length (default 9 symbols):

To specify a custom login/username length before @server.tld, use the `-l` or `--login-length` option:

    cmping -l 10 chatmail.uk   # generates logins with 10 characters
    cmping -l 8 chatmail.uk     # generates logins with 8 characters 

### Custom login length example

Using `-l 10` to generate logins with 10 characters instead of the default 9:

    $ cmping -l 10 -c 3 chatmail.uk 

    # using accounts_dir at: /home/user/.cache/cmping
    PING chatmail.uk(7x2m9p3wqr@chatmail.uk) -> chatmail.uk(n8v5c1x6zt@chatmail.uk) count=3
    64 bytes ME -> chatmail.uk -> chatmail.uk -> ME seq=0 time=245.32ms
    64 bytes ME -> chatmail.uk -> chatmail.uk -> ME seq=1 time=238.76ms
    64 bytes ME -> chatmail.uk -> chatmail.uk -> ME seq=2 time=251.09ms
    --- 7x2m9p3wqr@chatmail.uk -> n8v5c1x6zt@chatmail.uk statistics ---
    3 transmitted, 3 received, 0.00% loss
    rtt min/avg/max/mdev = 238.760/245.057/251.090/5.368 ms

## Example outputs

Example output for single-domain ping:

    $ cmping -c 10 nine.testrun.org 

    # using accounts_dir at: /home/user/.cache/cmping
    PING nine.testrun.org(brrey3kzk@nine.testrun.org) -> nine.testrun.org(uogfnwf3r@nine.testrun.org) count=10
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=0 time=490.42ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=1 time=298.80ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=2 time=232.80ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=3 time=245.76ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=4 time=253.45ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=5 time=293.69ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=6 time=238.98ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=7 time=321.58ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=8 time=209.66ms
    64 bytes ME -> nine.testrun.org -> nine.testrun.org -> ME seq=9 time=233.20ms
    --- brrey3kzk@nine.testrun.org -> uogfnwf3r@nine.testrun.org statistics ---
    10 transmitted, 10 received, 0.00% loss
    rtt min/avg/max/mdev = 209.661/281.833/490.416/81.265 ms

Example output for two-domain ping:

    $ cmping -c 10 mailchat.pl mehl.cloud 
    # using accounts_dir at: /home/user/.cache/cmping
    PING mailchat.pl(3yeby7i8m@mailchat.pl) -> mehl.cloud(eeaocuozy@mehl.cloud) count=10
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=0 time=792.29ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=1 time=370.50ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=2 time=379.01ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=3 time=367.38ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=4 time=367.56ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=5 time=367.98ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=6 time=374.11ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=7 time=373.28ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=8 time=401.45ms
    64 bytes ME -> mailchat.pl -> mehl.cloud -> ME seq=9 time=378.89ms
    --- 3yeby7i8m@mailchat.pl -> eeaocuozy@mehl.cloud statistics ---
    10 transmitted, 10 received, 0.00% loss
    rtt min/avg/max/mdev = 367.384/417.246/792.292/132.163 ms

## Developing / Releasing cmping

1. clone the git repository at <https://github.com/chatmail/cmping>

2. install 'cmping" in editing mode: `pip install -e .`

3. edit cmping.py and test, finally commit your changes

4. update CHANGELOG.md with the new version number and changes

5. install build/release tools: `pip install build twine`

6. run the release script:

        python release.py

   The release script will:
   - Validate the version in CHANGELOG.md is a proper version jump
   - Create and push a git tag for the version
   - Build the package and upload to PyPI
   - Add a dev changelog entry and commit it
   - Print which tag was uploaded to PyPI
