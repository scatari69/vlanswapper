# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CLI tool that configures an access-VLAN on a switch port over **Telnet**, using
the rule `vlan_id = 100 + port_number` (an ISP per-port-VLAN scheme). The port is
given by number or resolved from a client MAC. The switch vendor is
auto-detected on connect. Supported vendors: D-Link, Eltex, Huawei, BDCOM, Zyxel.
Model-specific drivers subclass the generic one (e.g. `dlink_des1210` extends
`dlink`); `detect._match` picks the driver with the **longest** matching marker,
so `des-1210` wins over the generic `des-`.

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
2. `telnet.TelnetClient` opens the socket; `session.Session` logs in
   (`login()` watches for user/password/prompt regexes, optionally does
   `enable`) and runs commands via `run()` / `run_expect()`, stripping the
   command echo and trailing prompt.
3. `detect.detect_vendor` runs neutral probe commands (`show version`,
   `display version`, `show switch`) and matches each driver's
   `detect_markers` against the output → picks a driver from
   `drivers.REGISTRY`.
4. Port resolution splits two ways:
   - explicit `--port`/`--mac` → one-shot via `_resolve_port_from_args` then
     `_apply` (returns exit code).
   - interactive with neither → `menu.run_menu` loops a text menu (1: enter
     port, 2: find by MAC, 0: quit), calling `_apply` per selection so several
     ports can be done on one connection.
   `_apply` computes `vlan = 100 + port`, confirms (unless `--yes`/dry-run), and
   runs `BaseDriver.swap()`.

`BaseDriver.swap()` (in `drivers/base.py`) is the **shared skeleton** — enter
config → `create_vlan` → `set_access_vlan` → exit config → `save`. Vendors only
override the CLI-specific primitives. Do not put vendor branching in the caller;
add/override methods on the driver instead.

## Adding or fixing a vendor driver

Each `drivers/<vendor>.py` subclasses `BaseDriver` and defines:
`detect_markers`, `disable_paging`, `enter_config`/`exit_config`, `iface`,
`create_vlan`, `set_access_vlan` (must clear the old access VLAN — most
Cisco-like CLIs do this implicitly, D-Link does **not** and removes it
explicitly), `find_port_by_mac` (returns an `int` port number — parse the
mac-table output), and `save`. Register the class in `drivers/__init__.py`
`REGISTRY`.

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
