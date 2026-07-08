# vlanswapper

A tool for configuring an access VLAN on switch ports over **Telnet**. The VLAN
is assigned by the rule:

```
vlan_id = 100 + port_number
```

The port can be given by number or found automatically by the client's MAC
address. The vendor is detected automatically on connect.

## Supported vendors

D-Link · Eltex · Huawei · BDCOM · Zyxel

There is also a driver for the specific **D-Link DES-1210** model (`--vendor
dlink_des1210`) — a Smart Managed series with a stripped-down CLI; autodetect
picks it over the generic D-Link based on the `DES-1210` marker in the banner.
The Telnet CLI is available only on firmware revisions C1/F and newer.

> The CLI syntax depends heavily on the series and firmware version. The command
> templates are best-effort; spots that vary are marked `# TODO` in the drivers
> (`vlanswapper/drivers/*.py`). Before production use, verify against your model
> with `--dry-run` + `--vendor`.

## Requirements

Standard library only, **Python 3.11+**. No external dependencies (a minimal
socket-based Telnet client is bundled, since `telnetlib` was removed from the
stdlib in Python 3.13).

## Installation

No installation required — just clone and run `vlanswapper.py`.

```bash
cp config.ini.example config.ini   # fill in the username/password
```

## Usage

### Interactive mode (menu)

Running without `--port`/`--mac` — the script asks for the switch IP, connects,
detects the vendor and shows a menu:

```
$ python3 vlanswapper.py
Switch IP/host: 192.0.2.10
Username: admin
Password:
Vendor: eltex

=== Switch 192.0.2.10 (eltex) ===
  1. Enter port number
  2. Find port by MAC address
  3. Show MAC table
  4. Show port list
  0. Quit
Choice:
```

After a port is configured the menu appears again — several ports can be
configured over one connection.

Items **3** and **4** are read-only and change nothing:

* **3. MAC table** — the switch's full FDB dump, verbatim.
* **4. Port list** — each port with its link status shown as an emoji (and color
  in a terminal): 🟢 up · 🔴 down · ⚫ disabled.

## Uplink foolproof guard

Before configuring a port the script checks whether it already carries the
**uplink VLAN** (default `253`) — either as access or tagged in a trunk. If so,
the change is cancelled to avoid accidentally cutting the uplink:

```
⛔ Port 25 looks like an uplink: it carries VLAN 253 (trunk).
   Aborted to avoid cutting the uplink. Re-run with --force if you're sure.
```

* `--force` — lift the guard and configure the port anyway;
* `--uplink-vlan N` — set your own uplink VLAN number (default `253`);
* `--uplink-vlan 0` — disable the check entirely.

If the port's current VLANs can't be determined (unfamiliar output format,
`--dry-run`), the script doesn't block — it only warns and proceeds.

### Non-interactive mode (arguments)

```bash
# Port given explicitly, IP asked interactively:
python3 vlanswapper.py --port 5

# Everything via arguments, no questions:
python3 vlanswapper.py --host 192.0.2.10 --port 5 --yes

# Find the port by the client's MAC:
python3 vlanswapper.py --host 192.0.2.10 --mac aa:bb:cc:dd:ee:ff

# Configure a port even if it looks like an uplink (lift the VLAN 253 guard):
python3 vlanswapper.py --host 192.0.2.10 --port 25 --force --yes

# Preview the commands without sending anything (dry-run needs an explicit
# --vendor, since autodetect requires a live device response):
python3 vlanswapper.py --host 192.0.2.10 --port 5 --vendor eltex --dry-run
```

### Where connection parameters come from

Priority: **CLI argument → environment variable → `config.ini` → interactive
prompt**. The password is read hidden in the dialog (`getpass`).

| Parameter | CLI | Env | config.ini `[switch]` |
|-----------|-----|-----|------------------------|
| address | `--host` | `VLANSWAPPER_HOST` | `host` |
| username | `--username` | `VLANSWAPPER_USER` | `username` |
| password | `--password` | `VLANSWAPPER_PASSWORD` | `password` |
| enable password | — | `VLANSWAPPER_ENABLE` | `enable_password` |
| TCP port | `--port-tcp` | `VLANSWAPPER_PORT` | `port` |

`enable_password` is needed for Cisco-like devices (Eltex/BDCOM) when the prompt
ends with `>` after login.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The integration tests run against the bundled mock switch
(`tests/mock_switch.py`) — no real hardware required.
