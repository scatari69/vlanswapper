"""D-Link DES-1210 driver (Smart Managed, -10/-28/-52 series).

Differences from the base :class:`DlinkDriver` (which targets "full" managed
switches like the DES-3200):

* This is a **Smart Managed** switch. A Telnet CLI exists only on firmware
  revisions **C1/F** and newer; on earlier revisions management is web/
  SmartConsole/SNMP only — the driver cannot work there.
* The port is moved purely by untagged VLAN membership; no PVID command is
  issued (neither ``config port_vlan ... pvid`` nor the DES-3200's ``config
  gvrp ports ... pvid``).
* ``disable clipaging`` may be accepted and still leave ``show ports``/``show
  vlan`` paging (seen on a DES-1210-28/ME), so listings are read through the
  pager-aware path inherited from :class:`DlinkDriver`.

All templates are **best-effort**, verified against docs rather than hardware.
Before production use, check the output with ``--dry-run --vendor dlink_des1210``.
"""

from __future__ import annotations

import re

from .base import DriverError, parse_int_ranges
from .dlink import DlinkDriver

# D-Link port lists ('1-3,5,7-9') are parsed by the same range parser.
_parse_port_list = parse_int_ranges


class DlinkDes1210Driver(DlinkDriver):
    name = "dlink_des1210"
    # 'des-1210' is longer than the generic 'des-' → autodetect prefers this driver.
    detect_markers = ("des-1210",)

    #: a VLAN block header, e.g. 'VID                : 253       VLAN NAME : mgmt'
    _VID_RE = re.compile(r"\bVID\b\s*:\s*(\d+)")
    #: a port-list line inside a block ('Member/Untagged/Tagged/Forbidden Ports')
    _PORTS_RE = re.compile(r"\b(member|untagged|tagged|forbidden)\s+ports\b\s*:(.*)$",
                           re.IGNORECASE)

    def _vlan_blocks(self, text: str) -> list[dict] | None:
        """Split a ``show vlan`` dump into well-formed blocks, or ``None``.

        ``None`` means the dump cannot be trusted. That matters because these
        listings are read through a pager, and a page seam landing inside a block
        can drop or fuse lines — after which a port list gets attributed to the
        wrong VID and the caller would happily delete the port from a VLAN it was
        never in. Rather than guess, the callers treat ``None`` as "don't know".

        Rejected as damaged: a VID header sharing a line with a port list, a port
        list before any VID header, the same port list twice in one block (which
        is what a lost VID header looks like), and a block cut short.
        """
        blocks: list[dict] = []
        cur: dict | None = None
        for line in text.splitlines():
            vid_m = self._VID_RE.search(line)
            ports_m = self._PORTS_RE.search(line)
            if vid_m and ports_m:
                return None
            if vid_m:
                cur = {"vid": int(vid_m.group(1))}
                blocks.append(cur)
                continue
            if ports_m:
                if cur is None:
                    return None
                key = ports_m.group(1).lower()
                if key in cur:
                    return None
                cur[key] = parse_int_ranges(ports_m.group(2))
        if not blocks:
            return None
        if any("member" not in b or "untagged" not in b for b in blocks):
            return None
        return blocks

    def _read_vlan_blocks(self) -> list[dict] | None:
        return self._vlan_blocks(self._run_view("show vlan"))

    def _current_untagged_vlans(self, port_number: int) -> list[int]:
        """VIDs where the port is currently an untagged member.

        The DES-1210 has no ``show vlan ports <n>``; membership comes from the
        block-style ``show vlan``. Raises rather than returning a guess, since the
        caller deletes the port from whatever this reports.
        """
        blocks = self._read_vlan_blocks()
        if blocks is None:
            raise DriverError(
                "could not read a complete 'show vlan' listing (the pager cut it "
                "short) — refusing to guess which VLAN the port is in")
        return [b["vid"] for b in blocks if port_number in b["untagged"]]

    def port_vlans(self, port_number: int) -> set[int] | None:
        # Membership counts tagged *and* untagged, so a trunked uplink (a port in
        # 'Member Ports' with an empty 'Untagged Ports') is caught by the guard.
        blocks = self._read_vlan_blocks()
        if blocks is None:
            return None
        return {b["vid"] for b in blocks
                if port_number in (b["member"] | b["untagged"])}

    def find_uplink_ports(self, uplink_vlan: int) -> list[int]:
        blocks = self._read_vlan_blocks()
        if blocks is None:
            self.s.log(f"[{self.name}] incomplete 'show vlan' — tagging no uplink")
            return []
        ports: set[int] = set()
        for b in blocks:
            if b["vid"] == uplink_vlan:
                ports |= b["member"] | b["untagged"]
        return sorted(ports)

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        # Remove the port from its old untagged VLANs and add it to the target
        # (same as the base driver).
        for old in self._current_untagged_vlans(port_number):
            if old != vlan_id:
                self._run(f"config vlan vlanid {old} delete {port_number}")
        # No PVID command on purpose (see DlinkDriver.set_access_vlan).
        out = self._run(f"config vlan vlanid {vlan_id} add untagged {port_number}")
        self._check_untagged_accepted(out, port_number, vlan_id)
