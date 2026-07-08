"""Interactive text menu.

Shown when the script is run interactively without an explicit --port/--mac.
The connection and vendor are already established by the caller; the menu loops,
letting several ports be configured in one session.
"""

from __future__ import annotations

import sys

from . import mac as macfmt
from .drivers import DriverError


def _ask(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip()


def _choose_port_by_number(driver) -> int | None:
    raw = _ask("Port number: ")
    if not raw.isdigit():
        print("An integer is required.", file=sys.stderr)
        return None
    return int(raw)


def _choose_port_by_mac(driver) -> int | None:
    raw = _ask("Client MAC address: ")
    try:
        mac = macfmt.normalize(raw)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return None
    print(f"Looking up port by MAC {macfmt.colon(mac)}...", file=sys.stderr)
    port = driver.find_port_by_mac(mac)
    if port is None:
        print("MAC not found in the forwarding table.", file=sys.stderr)
        return None
    print(f"MAC found on port {port}.", file=sys.stderr)
    return port


def _show_mac_table(driver) -> None:
    print("Reading MAC table...", file=sys.stderr)
    try:
        out = driver.show_mac_table()
    except DriverError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return
    text = out.strip()
    print(text if text else "MAC table is empty or unavailable.", file=sys.stderr)


#: emoji and ANSI color for each link status.
_STATUS_EMOJI = {"up": "🟢", "down": "🔴", "disabled": "⚫", "unknown": "⚪"}
_STATUS_COLOR = {"up": "32", "down": "31", "disabled": "90", "unknown": "37"}


def _fmt_status(status: str) -> str:
    """Build 'emoji + label', coloring the label with ANSI when stderr is a TTY."""
    label = status
    if sys.stderr.isatty():
        color = _STATUS_COLOR.get(status, "37")
        label = f"\033[{color}m{status}\033[0m"
    return f"{_STATUS_EMOJI.get(status, '⚪')} {label}"


def _show_ports(driver) -> None:
    print("Reading port list...", file=sys.stderr)
    try:
        ports = driver.list_port_status()
    except DriverError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return
    if not ports:
        print("Port list is empty or unavailable.", file=sys.stderr)
        return
    for port, status in ports:
        print(f"  Port {port:>3}  {_fmt_status(status)}", file=sys.stderr)


def run_menu(driver, host: str, vendor: str, apply_cb) -> int:
    """Loop the menu until exit. ``apply_cb(port) -> bool`` applies the VLAN to a port."""
    while True:
        print(
            f"\n=== Switch {host} ({vendor}) ===\n"
            "  1. Enter port number\n"
            "  2. Find port by MAC address\n"
            "  3. Show MAC table\n"
            "  4. Show port list\n"
            "  0. Quit",
            file=sys.stderr,
        )
        choice = _ask("Choice: ")
        if choice in ("0", "q", "exit"):
            print("Bye.", file=sys.stderr)
            return 0
        if choice == "3":
            _show_mac_table(driver)
            continue
        if choice == "4":
            _show_ports(driver)
            continue
        if choice == "1":
            port = _choose_port_by_number(driver)
        elif choice == "2":
            port = _choose_port_by_mac(driver)
        else:
            print("Unknown menu item.", file=sys.stderr)
            continue
        if port is None:
            continue
        try:
            apply_cb(port)
        except DriverError as exc:
            print(f"Error: {exc}", file=sys.stderr)
