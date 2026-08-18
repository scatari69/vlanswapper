# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CLI tool that configures an access-VLAN on a switch port over **Telnet**, using
the rule `vlan_id = 100 + port_number` (an ISP per-port-VLAN scheme). The port is
given by number or resolved from a client MAC. The switch vendor is
auto-detected on connect. Supported vendors: D-Link, Eltex, Huawei, BDCOM, Zyxel.
Model-specific drivers subclass the generic one (e.g. `dlink_des1210` extends
`dlink`, and both `dlink_1100` and `dlink_1100_me` extend `dlink_des1210` — the
/ME line shares the 1210 CLI but *not* the plain 1100's pager workaround, so it
deliberately does not sit under `dlink_1100`); `detect._match` picks the driver
with the **longest** matching
marker, so `des-1210`/`dgs-1100` win over the generic `des-`/`dgs-`, and the
`/ME` markers (`1100-10/me`, ...) must stay longer than `dgs-1100` to win in
turn — that ordering is what keeps the Metro Ethernet line off the plain-1100
driver.

## Workflow

Work on the feature branch, then **merge into `main` and push without asking** —
the maintainer has given standing approval for that. Keep the full test suite
green before merging.

## Commands

```bash
# Run (module or wrapper script are equivalent):
python3 vlanswapper.py --host 192.0.2.10 --port 5 --yes
python3 -m vlanswapper  --host 192.0.2.10 --mac aa:bb:cc:dd:ee:ff

# Full test suite (stdlib unittest — pytest is NOT available in this env):
python3 -m unittest discover -s tests -v

# One test:
python3 -m unittest tests.test_integration.IntegrationTests.test_login_detect_and_swap
```

There is no build/lint/package step and **no external dependencies** — stdlib
only. The environment has Python 3.14 with **no `pip`/`netmiko`/`telnetlib3`**,
so do not add third-party imports; keep everything on the standard library.

## Why the custom Telnet client

`telnetlib` was removed from the stdlib in Python 3.13, and no telnet package is
installable here. `vlanswapper/telnet.py` is a from-scratch minimal Telnet client
over a raw socket. It keeps two buffers: `_buf` (IAC-stripped text the switch
"printed") and `_raw` (an unfinished IAC sequence carried across `recv` chunks).
`_feed` parses IAC negotiations and **refuses every offered option** (`WONT`/
`DONT`) to stay in dumb line mode. If you touch reading logic, remember IAC
sequences can split across TCP reads (there's a test for this).

## Architecture (request flow)

`cli.run` orchestrates the whole thing in this order:

1. `settings.resolve` — merges connection params by precedence
   **CLI arg → env (`VLANSWAPPER_*`) → config.ini → interactive prompt**.
   Right after it, the **blacklist check** (`blacklist.py`): if the host matches
   an entry in `blacklist.txt` (cwd or `~/.config/vlanswapper/`, override with
   `--blacklist`; exact IP/hostname or CIDR), `run` refuses with exit 1 before
   any connection. No bypass flag by design.
2. `telnet.TelnetClient` opens the socket; `session.Session` logs in
   (`login()` watches for user/password/prompt regexes, optionally does
   `enable`) and runs commands via `run()` / `run_expect()`, stripping the
   command echo and trailing prompt.
3. `detect.detect_vendor` runs neutral probe commands (`show version`,
   `display version`, `show switch`) and matches each driver's
   `detect_markers` against the output → picks a driver from
   `drivers.REGISTRY`. Probes go through `Session.run_paged`: an *unsupported*
   probe can print a long help listing that pages (DGS-1100/ME), and a plain
   read would burn the timeout **and** leave the device sitting in the pager,
   desynchronizing every later command.
4. Port resolution splits two ways:
   - explicit `--port`/`--mac` → one-shot via `_resolve_port_from_args` then
     `_apply` (returns exit code).
   - interactive with neither → `menu.run_menu` loops a text menu (1: enter
     port, 2: find by MAC, 3: dump MAC table, 4: list ports with an
     up/down/disabled status shown as emoji/ANSI color, 0: quit), calling
     `_apply` per port selection so several ports can be done on one connection.
     Read-only views (3/4) go straight to the driver and never configure.
   `_apply` computes `vlan = 100 + port`, runs the **uplink guard**
   (`_uplink_guard` → `BaseDriver.is_uplink_port`: refuses a port that already
   carries the uplink VLAN — default `253`, from `UPLINK_VLAN`; bypass with
   `--force`, disable with `--uplink-vlan 0`), confirms (unless `--yes`/dry-run),
   and runs `BaseDriver.swap()`. The guard reads the port's current VLANs via
   `BaseDriver.port_vlans` (returns `None` = couldn't tell → warn but proceed, so
   `--dry-run` isn't blocked). `_apply` also calls `BaseDriver.find_uplink_ports`
   (the ports carrying `UPLINK_VLAN`) and passes them to `swap` so the new VLAN
   gets tagged upstream (skipped when `--uplink-vlan 0`).

`BaseDriver.swap()` (in `drivers/base.py`) is the **shared skeleton** — enter
config → `create_vlan` → `set_access_vlan` → (for each uplink port ≠ target)
`add_tagged_vlan` → exit config → `save`. Vendors only override the CLI-specific
primitives. Do not put vendor branching in the caller; add/override methods on
the driver instead.

## Adding or fixing a vendor driver

Each `drivers/<vendor>.py` subclasses `BaseDriver` and defines:
`detect_markers`, `disable_paging`, `enter_config`/`exit_config`, `iface`,
`create_vlan`, `set_access_vlan` (must clear the old access VLAN — most
Cisco-like CLIs do this implicitly, D-Link does **not** and removes it
explicitly), `find_port_by_mac` (returns an `int` port number — parse the
mac-table output), and `save`. Register the class in `drivers/__init__.py`
`REGISTRY`.

For the read-only menu views, set two class attributes with the vendor's
commands: `mac_table_cmd` (full FDB dump → `BaseDriver.show_mac_table`) and
`port_status_cmd` (→ `BaseDriver.list_port_status`, which calls
`parse_port_status` and merges combo-port duplicates by up>down>disabled). Every
listing goes through `BaseDriver._run_view` rather than `_run` — that's the one
hook to override when a firmware keeps **paging on** for these views despite
`disable_paging` (`dlink_1100` routes it to `Session.run_paged`, which answers
the `--More--`/`Next Page` legend with a space until the prompt returns and
strips the pager lines; with no pager it behaves exactly like `run`). The
base `parse_port_status` handles Cisco-like `show interfaces status`; override
it when the format differs (D-Link `show ports`, Huawei `display interface
brief` — both do).

For the uplink guard, `BaseDriver.port_vlans(port)` returns the set of VLANs the
port currently carries (access + trunk) or `None` if undeterminable. The base
parses Cisco-like `show interfaces switchport <iface>`; D-Link overrides it
(`show vlan ports`, and DES-1210 `show vlan` — both count tagged **and**
untagged so a trunked uplink is caught) and Huawei overrides it (`display port
vlan`). `parse_int_ranges` (in `base.py`) expands `1-3,5,7-9` VLAN/port lists.

To tag the new VLAN on the uplink trunk, `BaseDriver.find_uplink_ports(vlan)`
returns the ports carrying it — the base enumerates via `list_port_status` +
`port_vlans` (one query per port), DES-1210 overrides it to parse the VID's
member-ports line in a single `show vlan`. `BaseDriver.add_tagged_vlan(port,
vlan)` adds a tagged member: the Cisco-like default extends the trunk allowed
list (`switchport trunk allowed vlan add`); D-Link (`config vlan ... add
tagged`), Huawei (`port trunk allow-pass vlan`) and Zyxel (`fixed`) override it.

Two cross-vendor helpers live on `BaseDriver`: `_run_confirm` for commands that
prompt `[Y/N]`, and `_last_port_number` to extract the trailing port index from
tokens like `gi1/0/5`. MAC formatting per vendor is in `mac.py`
(`colon`/`dot`/`dash`/`huawei`).

CLI command templates are **best-effort and hardware-dependent** — spots that
vary by series/firmware are marked `# TODO` in the drivers. Prefer verifying
against real gear with `--dry-run --vendor <name>` before trusting a template.

## Testing without hardware

`tests/mock_switch.py` is a threaded socket server emulating an Eltex-like
switch (it deliberately sends IAC negotiations in its banner to exercise the
parser). Integration tests connect a real `TelnetClient` to it and assert on the
exact commands the "switch" received (`MockSwitch.received`). When you add a
driver behavior, assert on `received` rather than mocking the session.

## Gotchas

- **`--dry-run` disables autodetect.** `Session.run` short-circuits in dry-run
  and returns `""`, so `detect_vendor` has nothing to match — pass `--vendor`
  explicitly with `--dry-run`.
- After matching a prompt during login, **clear the buffer before writing the
  next command** or the stale prompt gets re-matched (this bit `_enter_enable`;
  see the `take_buffer()` call there).
- All user-facing output goes to **stderr**; exit code is the contract
  (`0` ok, `1` error/declined, `2` bad settings, `130` interrupted).
