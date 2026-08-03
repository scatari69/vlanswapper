"""Base driver: the shared workflow and the contract for vendor implementations.

The core action is the same for every vendor:

    vlan_id = VLAN_BASE + port
    → remove the old access VLAN from the port
    → create the target VLAN (if it doesn't exist yet)
    → assign it as the port's access VLAN
    → (optionally) save the config

Only the CLI syntax differs — it is encapsulated in methods that subclasses
override. The :meth:`swap` method holds the shared skeleton.
"""

from __future__ import annotations

import re

from ..session import Session

VLAN_BASE = 100

#: typical uplink trunk VLAN — the "foolproof" guard run before configuring a port.
UPLINK_VLAN = 253


def parse_int_ranges(spec: str) -> set[int]:
    """Parse a number list like ``1-3,5,7-9`` (ports or VIDs) into a set."""
    out: set[int] = set()
    for token in spec.replace(";", ",").split(","):
        for chunk in token.split():
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.match(r"(\d+)\s*-\s*(\d+)$", chunk)
            if m:
                out.update(range(int(m.group(1)), int(m.group(2)) + 1))
            elif chunk.isdigit():
                out.add(int(chunk))
    return out


#: normalization of the raw link-status words emitted by different vendors.
LINK_STATUS_MAP = {
    "connected": "up",
    "up": "up",
    "notconnect": "down",
    "notconnected": "down",
    "not-connect": "down",
    "down": "down",
    "disabled": "disabled",
    "admin-down": "disabled",
    "err-disabled": "disabled",
    "errdisabled": "disabled",
}

#: status priority when merging duplicates (e.g. combo port: copper down, fiber up).
_STATUS_RANK = {"up": 0, "down": 1, "disabled": 2, "unknown": 3}


class DriverError(Exception):
    """A device-side failure (port not found, command rejected, etc.)."""


class BaseDriver:
    #: human-readable vendor name
    name: str = "base"
    #: substrings detect uses to identify the vendor in version-command output
    detect_markers: tuple[str, ...] = ()
    #: command that dumps the full MAC/FDB table (overridden per vendor)
    mac_table_cmd: str = ""
    #: command that lists port status (overridden per vendor)
    port_status_cmd: str = ""

    def __init__(self, session: Session):
        self.s = session

    # -- overridable primitives --------------------------------------------
    def disable_paging(self) -> None:
        """Disable paged output (--More--) so reads don't hang."""
        raise NotImplementedError

    def enter_config(self) -> None:
        """Enter configuration mode."""
        raise NotImplementedError

    def exit_config(self) -> None:
        """Leave configuration mode back to exec mode."""
        raise NotImplementedError

    def iface(self, port_number: int) -> str:
        """Build an interface name from a port number (e.g. 5 -> GigabitEthernet0/0/5)."""
        raise NotImplementedError

    def create_vlan(self, vlan_id: int) -> None:
        """Create the VLAN if it doesn't exist yet (idempotent)."""
        raise NotImplementedError

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        """Remove the port's old access VLAN and assign the new one."""
        raise NotImplementedError

    def find_port_by_mac(self, mac: str) -> int | None:
        """Return the port number where the MAC is seen, or ``None``."""
        raise NotImplementedError

    def save(self) -> None:
        """Save running config to startup config."""
        raise NotImplementedError

    def show_mac_table(self) -> str:
        """Return the MAC-address (FDB) table dump verbatim.

        The command is provided by the vendor via the :attr:`mac_table_cmd` attribute.
        """
        if not self.mac_table_cmd:
            raise DriverError("viewing the MAC table is not supported for this vendor")
        return self._run_view(self.mac_table_cmd)

    def parse_port_status(self, output: str) -> list[tuple[int, str]]:
        """Parse :attr:`port_status_cmd` output into a list of ``(port, status)``.

        The base implementation targets Cisco-like ``show interfaces status``
        (first column is the interface, a status word appears somewhere after).
        Vendors with a different format override this method.
        """
        rows: list[tuple[int, str]] = []
        for line in output.splitlines():
            toks = line.split()
            if len(toks) < 2:
                continue
            port = self._last_port_number(toks[0])
            if port is None:
                continue
            status = next((LINK_STATUS_MAP[t.lower()] for t in toks[1:]
                           if t.lower() in LINK_STATUS_MAP), None)
            if status is not None:
                rows.append((port, status))
        return rows

    def list_port_status(self) -> list[tuple[int, str]]:
        """Return a sorted list of ``(port, status)`` with no duplicates.

        Duplicates for the same port (e.g. a combo copper/fiber port) are merged
        into the "best" status: up > down > disabled.
        """
        if not self.port_status_cmd:
            raise DriverError("listing ports is not supported for this vendor")
        best: dict[int, str] = {}
        for port, status in self.parse_port_status(self._run_view(self.port_status_cmd)):
            rank = _STATUS_RANK.get(status, _STATUS_RANK["unknown"])
            if port not in best or rank < _STATUS_RANK.get(best[port], 3):
                best[port] = status
        return sorted(best.items())

    def port_vlans(self, port_number: int) -> set[int] | None:
        """Return the VLANs the port currently belongs to (access + trunk).

        Serves as the "foolproof" guard: if an uplink VLAN is among them, the
        caller refuses to reconfigure the port. Returning ``None`` means it could
        not be determined (empty output, dry-run, unfamiliar format) — in that
        case the guard does not block, but warns.

        The base parses Cisco-like ``show interfaces switchport <iface>`` (the
        ``Access Mode VLAN``, ``Trunking Native Mode VLAN`` and ``Trunking VLANs
        Enabled`` lines). Vendors with a different CLI override this method.
        """
        out = self._run(f"show interfaces switchport {self.iface(port_number)}")
        if not out.strip():
            return None
        vlans: set[int] = set()
        found = False
        for line in out.splitlines():
            if ":" not in line or "vlan" not in line.lower():
                continue
            low, val = line.lower(), line.split(":", 1)[1].strip()
            if "access mode vlan" in low or "native mode vlan" in low:
                m = re.match(r"(\d+)", val)
                if m:
                    vlans.add(int(m.group(1)))
                    found = True
            elif "trunk" in low and ("enable" in low or "allow" in low):
                got = parse_int_ranges(val)
                vlans |= got
                found = True
        return vlans if found else None

    def is_uplink_port(self, port_number: int, uplink_vlan: int) -> bool | None:
        """``True`` — the port carries ``uplink_vlan`` (looks like an uplink),
        ``False`` — it doesn't, ``None`` — couldn't determine."""
        vlans = self.port_vlans(port_number)
        if vlans is None:
            return None
        return uplink_vlan in vlans

    def find_uplink_ports(self, uplink_vlan: int) -> list[int]:
        """Return the ports that currently carry ``uplink_vlan`` (the uplink trunks).

        Generic implementation: enumerate ports via :meth:`list_port_status` and
        check each port's VLANs. Vendors override this for a cheaper single-command
        scan (e.g. reading the VLAN's member-ports line once).
        """
        ports: list[int] = []
        try:
            listed = self.list_port_status()
        except DriverError:
            return ports
        for port, _status in listed:
            vlans = self.port_vlans(port)
            if vlans and uplink_vlan in vlans:
                ports.append(port)
        return ports

    def add_tagged_vlan(self, port_number: int, vlan_id: int) -> None:
        """Add ``vlan_id`` as a tagged (trunk) member on ``port_number``.

        Cisco-like default (Eltex/BDCOM): extend the trunk's allowed VLAN list.
        Vendors with a different syntax override this method.
        """
        self._run(f"interface {self.iface(port_number)}")
        # TODO: some series use 'switchport trunk vlan-allowed add' — verify per model.
        self._run(f"switchport trunk allowed vlan add {vlan_id}")
        self._run("exit")

    # -- shared workflow ---------------------------------------------------
    def vlan_for_port(self, port_number: int) -> int:
        return VLAN_BASE + port_number

    def swap(self, port_number: int, vlan_id: int, save: bool = True,
             uplink_ports: "list[int] | tuple[int, ...]" = ()) -> None:
        """Full flow assigning ``vlan_id`` as the access VLAN on ``port_number``.

        If ``uplink_ports`` is given, ``vlan_id`` is also added tagged to each of
        them (except ``port_number`` itself) so the new VLAN has a path upstream.
        """
        self.enter_config()
        try:
            self.create_vlan(vlan_id)
            self.set_access_vlan(port_number, vlan_id)
            for uplink in uplink_ports:
                if uplink != port_number:
                    self.add_tagged_vlan(uplink, vlan_id)
        finally:
            self.exit_config()
        if save:
            self.save()

    # -- helpers for subclasses --------------------------------------------
    def _run(self, command: str, **kw) -> str:
        return self.s.run(command, **kw)

    def _run_view(self, command: str) -> str:
        """Run a read-only listing command (MAC table, port list, VLAN dump).

        Separate from :meth:`_run` so drivers whose firmware keeps paging enabled
        for these views despite :meth:`disable_paging` can override one place and
        answer the pager instead of timing out.
        """
        return self._run(command)

    def _run_confirm(self, command: str, yes: str = "Y", confirm_re: str = r"\[?(y/n|yes/no)\]?",
                     timeout: float | None = None) -> str:
        """Run a command that may ask for confirmation (Y/N).

        If the device asks for confirmation, send ``yes``. If the prompt comes
        back immediately, send nothing extra.
        """
        text, idx = self.s.run_expect(command, expect=[confirm_re, self.s.prompt_re], timeout=timeout)
        if idx == 0:
            return self._run(yes, timeout=timeout)
        return text

    @staticmethod
    def _last_port_number(token: str) -> int | None:
        """Extract the port number from a token like ``gi1/0/5`` / ``Eth0/0/5`` / ``5``.

        The last group of digits is taken — that's the port index in the stack/slot.
        """
        nums = re.findall(r"\d+", token)
        return int(nums[-1]) if nums else None
