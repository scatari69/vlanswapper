"""CLI entry point: argument parsing, interactive dialog, orchestration.

Flow:
  1. Resolve connection settings (CLI/env/config, ask for anything missing).
  2. Connect over Telnet, log in, enter enable mode (if needed).
  3. Detect the vendor (or take it from --vendor) and build the driver.
  4. Determine the port number: from --port, from --mac, or by asking.
  5. Compute vlan = 100 + port and run the swap (with confirmation).
"""

from __future__ import annotations

import argparse
import sys

from . import mac as macfmt
from .detect import build_driver, detect_vendor
from .drivers import REGISTRY, UPLINK_VLAN, VLAN_BASE, DriverError
from .menu import run_menu
from .session import LoginError, Session, TelnetError
from .settings import resolve
from .telnet import TelnetClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vlanswapper",
        description="Configure an access VLAN (vlan = 100 + port number) on switch ports over Telnet.",
    )
    p.add_argument("--host", help="switch IP/host (else from env/config or we ask)")
    p.add_argument("--port-tcp", dest="port", type=int, help="Telnet TCP port (default 23)")
    p.add_argument("--username", help="username (else from env/config or we ask)")
    p.add_argument("--password", help="password (prefer env/config over the command line)")
    p.add_argument("--vendor", choices=sorted(REGISTRY),
                   help="force the vendor instead of autodetection")
    p.add_argument("--timeout", type=float, help="read timeout, seconds (default 10)")

    target = p.add_mutually_exclusive_group()
    target.add_argument("-p", "--port", dest="switch_port", type=int,
                        help="switch port number")
    target.add_argument("-m", "--mac", help="client MAC — find the port by it")

    p.add_argument("--uplink-vlan", type=int, default=UPLINK_VLAN,
                   help=f"uplink VLAN guarded against accidental reconfiguration "
                        f"(default {UPLINK_VLAN}; 0 disables the check)")
    p.add_argument("--force", action="store_true",
                   help="configure the port even if it looks like an uplink (removes the guard)")
    p.add_argument("--no-save", action="store_true", help="don't save the config after configuring")
    p.add_argument("--dry-run", action="store_true",
                   help="show the commands but don't send them to the device")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p.add_argument("-v", "--verbose", action="store_true", help="print the commands being sent")
    return p


def _logger(verbose: bool):
    def log(msg: str) -> None:
        if verbose or msg.startswith("$"):
            print(msg, file=sys.stderr)
    return log


def _resolve_port_from_args(driver, args) -> int:
    """Determine the port from explicit --port/--mac (non-interactive path)."""
    if args.switch_port is not None:
        return args.switch_port
    mac = macfmt.normalize(args.mac)
    print(f"Looking up port by MAC {macfmt.colon(mac)}...", file=sys.stderr)
    port = driver.find_port_by_mac(mac)
    if port is None:
        raise DriverError(f"MAC {macfmt.colon(mac)} not found in the forwarding table")
    print(f"MAC found on port {port}", file=sys.stderr)
    return port


def _uplink_guard(driver, port: int, args) -> bool:
    """Foolproof guard: don't let an uplink port be reconfigured. Return True if OK to proceed."""
    if args.force or not args.uplink_vlan:
        return True
    verdict = driver.is_uplink_port(port, args.uplink_vlan)
    if verdict is None:
        print(f"⚠ Could not check port {port} for uplink (VLAN {args.uplink_vlan}) — proceeding.",
              file=sys.stderr)
        return True
    if verdict:
        print(f"⛔ Port {port} looks like an uplink: it carries VLAN {args.uplink_vlan} (trunk).\n"
              f"   Aborted to avoid cutting the uplink. Re-run with --force if you're sure.",
              file=sys.stderr)
        return False
    return True


def _apply(driver, port: int, args, interactive: bool) -> bool:
    """Compute the VLAN, confirm (if needed) and run the swap. Return success."""
    if not _uplink_guard(driver, port, args):
        return False
    vlan_id = driver.vlan_for_port(port)
    # Find the uplink trunk(s) so the new VLAN gets a path upstream (tagged there).
    uplinks = [u for u in driver.find_uplink_ports(args.uplink_vlan) if u != port] \
        if args.uplink_vlan else []
    print(f"Port {port} → access VLAN {vlan_id} (= {VLAN_BASE} + {port})", file=sys.stderr)
    if uplinks:
        print(f"VLAN {vlan_id} will also be tagged on uplink port(s) "
              f"{', '.join(map(str, uplinks))}", file=sys.stderr)
    if not args.yes and interactive and not args.dry_run:
        if input("Apply? [y/N]: ").strip().lower() not in ("y", "yes"):
            print("Skipped.", file=sys.stderr)
            return False
    driver.swap(port, vlan_id, save=not args.no_save, uplink_ports=uplinks)
    done = f"Done: port {port} = VLAN {vlan_id}"
    if uplinks:
        done += f"; VLAN {vlan_id} tagged on uplink {', '.join(map(str, uplinks))}"
    if args.dry_run:
        done += " (dry-run, nothing sent)"
    print(done, file=sys.stderr)
    return True


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    interactive = sys.stdin.isatty()
    log = _logger(args.verbose)

    try:
        cfg = resolve(args, interactive=interactive)
    except ValueError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 2

    client = TelnetClient(cfg.host, cfg.port, timeout=cfg.timeout)
    try:
        client.open()
        session = Session(client, dry_run=args.dry_run, log=log)
        prompt = session.login(cfg.username, cfg.password, cfg.enable_password)

        vendor = args.vendor or detect_vendor(session, prompt)
        driver = build_driver(session, vendor)
        print(f"Vendor: {vendor}", file=sys.stderr)
        driver.disable_paging()

        # An explicit port/MAC means a one-shot run. Otherwise the interactive menu.
        if args.switch_port is not None or args.mac:
            port = _resolve_port_from_args(driver, args)
            return 0 if _apply(driver, port, args, interactive) else 1

        if not interactive:
            raise ValueError("no port given: specify --port or --mac")

        return run_menu(driver, cfg.host, vendor,
                        lambda port: _apply(driver, port, args, interactive))

    except (TelnetError, LoginError, DriverError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    finally:
        client.close()
